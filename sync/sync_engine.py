"""
Core sync logic — v2

HOW SYNC WORKS:
- Hourly sync: INCREMENTAL — check for new listings since last run,
  process orders, push queued stock/price changes. Completes in seconds.
- Sync Now (manual): Full catalogue refresh + orders + queues.
  This is the only time ALL listings are re-fetched.

SYNC FLAG (new in v2):
- Editing stock or prices on the dashboard sets variants.needs_sync = TRUE.
- Hourly job: find needs_sync=TRUE → push stock AND price to both platforms → set needs_sync=FALSE.
- No more stock_push_* settings rows.

STOCK RULE: NEVER overwrite existing stock. Only set stock for brand new products.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(self, db, ss, ebay):
        self.db = db
        self.ss = ss
        self.ebay = ebay

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _load_blocklists(self):
        """Load merged_skus blocklist and existing eBay item IDs from channel_listings."""
        import json
        merged_skus_raw = self.db.get_setting("merged_skus")
        merged_skus = set()
        if merged_skus_raw:
            try:
                merged_skus = set(json.loads(merged_skus_raw))
                logger.info(f"Blocklist: {len(merged_skus)} merged SKU(s) will be skipped")
            except Exception:
                pass

        # Use channel_listings (not legacy platform_pricing)
        existing_ebay_item_ids = set()
        all_ebay_listings = self.db._rest("GET", "channel_listings",
                                          params={"channel": "eq.ebay",
                                                  "select": "channel_product_id",
                                                  "limit": "5000"})
        for ep in (all_ebay_listings or []):
            pid = ep.get("channel_product_id", "")
            parts = pid.split("|")
            if len(parts) >= 2:
                existing_ebay_item_ids.add(parts[1])

        return merged_skus, existing_ebay_item_ids

    def _save_ebay_item(self, item, merged_skus, existing_ebay_item_ids):
        """Save a single eBay item to DB. Returns 1 if new product created, 0 otherwise."""
        sku = item.get("sku")
        if not sku:
            return 0

        raw_item_id = item.get("item_id", sku)
        bare_id = raw_item_id.split("|")[1] if "|" in raw_item_id else raw_item_id

        # Skip if already linked to another product (merged) and no product exists for this SKU
        if bare_id in existing_ebay_item_ids and not self.db.get_product_by_sku(sku):
            logger.info(f"Skipping eBay item {sku} - already linked via channel_listings (merged)")
            return 0

        if sku in merged_skus:
            return 0

        name = item.get("title", "") or "Unnamed"
        if item.get("is_variant") and item.get("aspects"):
            label = " / ".join(
                f"{k}: {v}" for k, v in item["aspects"].items()
                if v and str(v).strip() and not str(v).startswith("_")
            )
            if label:
                name = f"{name} - {label}"

        existing = self.db.get_product_by_sku(sku)
        is_new = existing is None
        if not existing:
            rows = self.db.upsert_product({
                "name": name,
                "sku": sku,
                "description": item.get("description", ""),
            })
            existing = rows[0] if rows else self.db.get_product_by_sku(sku)
        else:
            if existing.get("name") != name:
                self.db.update_product_name(existing["id"], name)

        if existing:
            product_id = existing["id"]  # this is variant_id in v2
            if is_new:
                self.db.upsert_inventory({"variant_id": product_id,
                                          "product_id": existing.get("product_id"),
                                          "total_stock": item.get("quantity", 0)})
            variation_sku = item.get("variation_sku") if item.get("is_variant") else None
            self.db.upsert_price({
                "product_id": product_id,   # shim translates to variant_id
                "platform": "ebay",
                "price": float(item.get("price", 0.0)),
                "currency": "GBP",
                "platform_product_id": item.get("item_id", sku),
                "platform_variant_id": variation_sku,
            })
            existing_ebay_item_ids.add(bare_id)

        return 1 if is_new else 0

    # ─── Full Catalogue Sync (Sync Now only) ─────────────────────────────────

    def sync_product_catalogue(self, skip_squarespace=False):
        """Full catalogue import — BULK version.

        Pre-loads all existing variants + channel_listings in 2 DB calls,
        builds all new rows in memory, then bulk-inserts in batches.
        Reduces ~18,000 individual Supabase calls to ~20, completing in < 2 minutes.
        """
        import uuid as uuid_mod
        import json

        logger.info("Full catalogue sync (BULK): pulling all products from both platforms...")

        # Load merged SKUs blocklist
        merged_skus_raw = self.db.get_setting("merged_skus")
        merged_skus = set()
        if merged_skus_raw:
            try:
                merged_skus = set(json.loads(merged_skus_raw))
                logger.info(f"Blocklist: {len(merged_skus)} merged SKU(s) will be skipped")
            except Exception:
                pass

        # ── 1. Pre-load ALL existing data in 2 DB calls ───────────────────
        existing_variants = self.db.get_all_variants()   # dict: sku → variant row
        existing_listings = self.db.get_all_channel_listings()  # list

        # Build O(1) indexes from existing listings
        cl_by_channel_variant = {}   # (channel, channel_variant_id) → listing
        cl_by_variant_channel = {}   # (variant_id, channel) → listing
        existing_ebay_bare_ids = set()  # bare eBay item IDs already in DB
        for cl in existing_listings:
            ch  = cl.get("channel", "")
            cv  = cl.get("channel_variant_id")
            vid = cl.get("variant_id")
            cp  = cl.get("channel_product_id", "")
            if ch and cv:
                cl_by_channel_variant[(ch, cv)] = cl
            if vid and ch:
                cl_by_variant_channel[(vid, ch)] = cl
            if ch == "ebay":
                parts = cp.split("|")
                if len(parts) >= 2:
                    existing_ebay_bare_ids.add(parts[1])

        # ── 2. Fetch platform catalogues ──────────────────────────────────
        if skip_squarespace:
            logger.info("Skipping Squarespace sync")
            ss_products, ss_stock_map = [], {}
        else:
            ss_products = self.ss.get_products()
            ss_stock_map = {
                inv.get("variantId"): inv.get("quantity", 0)
                for inv in (self.ss.get_inventory() or [])
                if inv.get("variantId")
            }

        ebay_items = self.ebay.get_inventory_items()
        logger.info(f"Fetched {len(ss_products)} SS products, {len(ebay_items)} eBay items")

        # ── 3. Build new rows in memory ───────────────────────────────────
        new_products        = []
        new_variants        = []
        new_inventory       = []
        new_channel_listings = []
        creating_skus       = set()   # de-dupe within this batch

        # --- Squarespace ---
        for prod in ss_products:
            prod_name = prod.get("name", "Unnamed")
            variants  = prod.get("variants", [])
            for variant in variants:
                ss_vid = variant.get("id", "")
                sku    = variant.get("sku") or f"SS-{prod['id']}-{ss_vid}"

                if sku in merged_skus:
                    continue
                if ("squarespace", ss_vid) in cl_by_channel_variant:
                    continue   # already linked

                attrs = variant.get("attributes", {})
                if len(variants) > 1 and attrs:
                    label = " / ".join(str(v) for v in attrs.values() if v)
                    name  = f"{prod_name} - {label}" if label else prod_name
                else:
                    name = prod_name

                price_raw = ((variant.get("pricing") or {}).get("basePrice") or {})
                price_val = float(price_raw.get("value") or 0)
                stock_qty = ss_stock_map.get(ss_vid, 0)

                if sku in existing_variants:
                    # Variant exists — add listing if missing
                    ev = existing_variants[sku]
                    if (ev["id"], "squarespace") not in cl_by_variant_channel:
                        new_channel_listings.append({
                            "id": str(uuid_mod.uuid4()),
                            "variant_id": ev["id"],
                            "channel": "squarespace",
                            "channel_sku": sku,
                            "channel_price": price_val,
                            "channel_product_id": prod["id"],
                            "channel_variant_id": ss_vid,
                        })
                elif sku not in creating_skus:
                    creating_skus.add(sku)
                    product_id = str(uuid_mod.uuid4())
                    variant_id = str(uuid_mod.uuid4())
                    new_products.append({
                        "id": product_id, "name": name,
                        "sku": sku,
                        "description": (prod.get("description") or "")[:500],
                        "status": "active", "active": True,
                    })
                    new_variants.append({
                        "id": variant_id, "product_id": product_id,
                        "internal_sku": sku, "needs_sync": False,
                    })
                    new_inventory.append({
                        "variant_id": variant_id, "product_id": product_id,
                        "total_stock": stock_qty, "low_stock_threshold": 2,
                    })
                    new_channel_listings.append({
                        "id": str(uuid_mod.uuid4()),
                        "variant_id": variant_id, "channel": "squarespace",
                        "channel_sku": sku, "channel_price": price_val,
                        "channel_product_id": prod["id"], "channel_variant_id": ss_vid,
                    })

        # --- eBay ---
        # Build lookup: sku → variant_id for variants being created this run (from SS)
        new_sku_to_variant = {v["internal_sku"]: v["id"] for v in new_variants}

        for item in ebay_items:
            sku = item.get("sku")
            if not sku or sku in merged_skus:
                continue

            raw_item_id = item.get("item_id", sku)
            bare_id     = raw_item_id.split("|")[1] if "|" in raw_item_id else raw_item_id

            name = item.get("title", "") or "Unnamed"
            if item.get("is_variant") and item.get("aspects"):
                label = " / ".join(
                    f"{k}: {v}" for k, v in item["aspects"].items()
                    if v and str(v).strip() and not str(v).startswith("_")
                )
                if label:
                    name = f"{name} - {label}"

            variation_sku      = item.get("variation_sku") if item.get("is_variant") else None
            channel_product_id = item.get("item_id", sku)
            price_val          = float(item.get("price") or 0.0)
            stock_qty          = item.get("quantity", 0)

            if sku in existing_variants:
                # Variant exists — add eBay listing if missing
                ev = existing_variants[sku]
                if (ev["id"], "ebay") not in cl_by_variant_channel:
                    new_channel_listings.append({
                        "id": str(uuid_mod.uuid4()),
                        "variant_id": ev["id"], "channel": "ebay",
                        "channel_sku": sku, "channel_price": price_val,
                        "channel_product_id": channel_product_id,
                        "channel_variant_id": variation_sku,
                    })
            elif sku in new_sku_to_variant:
                # Created from SS this run — just add the eBay listing
                variant_id = new_sku_to_variant[sku]
                new_channel_listings.append({
                    "id": str(uuid_mod.uuid4()),
                    "variant_id": variant_id, "channel": "ebay",
                    "channel_sku": sku, "channel_price": price_val,
                    "channel_product_id": channel_product_id,
                    "channel_variant_id": variation_sku,
                })
            elif bare_id in existing_ebay_bare_ids:
                logger.info(f"Skipping eBay {sku} — item ID already linked (merged)")
            elif sku not in creating_skus:
                creating_skus.add(sku)
                product_id = str(uuid_mod.uuid4())
                variant_id = str(uuid_mod.uuid4())
                new_products.append({
                    "id": product_id, "name": name,
                    "sku": sku,
                    "description": (item.get("description") or "")[:500],
                    "status": "active", "active": True,
                })
                new_variants.append({
                    "id": variant_id, "product_id": product_id,
                    "internal_sku": sku, "needs_sync": False,
                })
                new_inventory.append({
                    "variant_id": variant_id, "product_id": product_id,
                    "total_stock": stock_qty, "low_stock_threshold": 2,
                })
                new_channel_listings.append({
                    "id": str(uuid_mod.uuid4()),
                    "variant_id": variant_id, "channel": "ebay",
                    "channel_sku": sku, "channel_price": price_val,
                    "channel_product_id": channel_product_id,
                    "channel_variant_id": variation_sku,
                })

        # ── 4. Bulk insert ────────────────────────────────────────────────
        logger.info(
            f"Bulk insert: {len(new_products)} products, {len(new_variants)} variants, "
            f"{len(new_inventory)} inventory, {len(new_channel_listings)} channel_listings"
        )
        self.db.bulk_insert_rows("products",        new_products)
        self.db.bulk_insert_rows("variants",        new_variants)
        self.db.bulk_insert_rows("inventory",       new_inventory)
        self.db.bulk_insert_rows("channel_listings", new_channel_listings)

        total_new = len(new_products)
        logger.info(f"Full catalogue sync complete: {total_new} new products added")
        return total_new

    # ─── Incremental New Listings (Hourly) ───────────────────────────────────

    def sync_new_listings(self, since_timestamp):
        """Check for new eBay listings created since since_timestamp.
        Fast: only fetches listings listed after that point (usually 0-5 per day).
        Called every hourly sync.
        """
        logger.info(f"Checking for new eBay listings since {since_timestamp}")

        merged_skus, existing_ebay_item_ids = self._load_blocklists()

        raw_new = self.ebay.get_new_listings(since_timestamp)
        if not raw_new:
            logger.info("No new eBay listings since last sync")
            return 0

        logger.info(f"Found {len(raw_new)} new listing(s) — expanding for variants...")

        results = []
        seen_groups = set()
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.ebay._expand_item, item, seen_groups, lock) for item in raw_new]
            for future in as_completed(futures):
                try:
                    entries = future.result()
                    if entries:
                        results.extend(entries)
                except Exception as e:
                    logger.warning("New listing expansion error: %s", e)

        added = 0
        for item in results:
            added += self._save_ebay_item(item, merged_skus, existing_ebay_item_ids)

        logger.info(f"New listings sync: {added} new product(s) added")
        return added

    # ─── Order Processing ────────────────────────────────────────────────────

    def process_squarespace_orders(self, since: str = None):
        """Process new SS orders → deduct stock → mark variant needs_sync=TRUE."""
        logger.info(f"Processing SS orders since {since}")
        orders = self.ss.get_orders(modified_after=since)
        processed = 0
        for order in orders:
            order_id = order.get("id")
            status = order.get("fulfillmentStatus", "PENDING")

            if self.db.order_exists("squarespace", order_id):
                if status == "FULFILLED":
                    self.db.update_order_status(order_id, status)
                continue

            if status not in ("PENDING", "FULFILLED"):
                continue

            # Save one row per line item (no customer data stored)
            for line in order.get("lineItems", []):
                variant_id = line.get("variantId")
                qty_sold = int(line.get("quantity", 1))
                sku = line.get("sku") or f"SS-{line.get('productId')}-{variant_id}"
                price = float(line.get("unitPricePaid", {}).get("value", 0))
                product = self.db.get_product_by_sku(sku)
                if not product:
                    logger.warning(f"SS SKU {sku} not in DB — saving order without product link")

                self.db.insert_order({
                    "platform": "squarespace",
                    "platform_order_id": order_id,
                    "product_id": product["product_id"] if product else None,
                    "sku": sku,
                    "quantity": qty_sold,
                    "unit_price": price,
                    "currency": "GBP",
                    "status": status,
                    "ordered_at": order.get("createdOn"),
                    "fulfillment_status": status,
                    "order_total": round(price * qty_sold, 2),
                    "item_name": line.get("productName", ""),
                    "order_number": order.get("orderNumber", ""),
                })

                if product:
                    variant_db_id = product["id"]  # this is variant_id in v2
                    inv_rows = self.db.get_inventory(variant_id=variant_db_id)
                    if inv_rows:
                        inv = inv_rows[0]
                        new_stock = max(0, inv["total_stock"] - qty_sold)
                        self.db.upsert_inventory({
                            "id": inv["id"],
                            "variant_id": variant_db_id,
                            "product_id": product.get("product_id"),
                            "total_stock": new_stock,
                        })
                        # Mark needs_sync so hourly job pushes new stock to eBay too
                        self.db.mark_variant_needs_sync(variant_db_id)
                        logger.info(f"SS sale: {sku} qty={qty_sold} → stock now {new_stock} (needs_sync=TRUE)")
            processed += 1
        logger.info(f"SS orders processed: {processed}")
        return processed

    def process_ebay_orders(self, since: str = None):
        """Process new eBay orders + reconcile statuses for last 90 days."""
        reconcile_since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f"Processing eBay orders (reconciling last 90 days)")
        orders = self.ebay.get_orders(created_after=reconcile_since)
        processed = 0

        for order in orders:
            order_id = order.get("orderId")
            if not self.db.order_exists("ebay", order_id):
                continue
            cancel_state = order.get("cancelStatus", {}).get("cancelState", "NONE_REQUESTED")
            if cancel_state in ("CANCELLED", "CANCEL_ACCEPTED"):
                self.db.update_order_status(order_id, "CANCELLED")
            else:
                ebay_status = order.get("orderFulfillmentStatus", "NOT_STARTED")
                self.db.update_order_status(order_id, ebay_status)

        for order in orders:
            order_id = order.get("orderId")
            cancel_state = order.get("cancelStatus", {}).get("cancelState", "NONE_REQUESTED")
            if cancel_state in ("CANCELLED", "CANCEL_ACCEPTED"):
                continue
            if self.db.order_exists("ebay", order_id):
                continue

            # Save one row per line item (no customer data stored)
            for line in order.get("lineItems", []):
                sku = line.get("sku", "")
                legacy_item_id = line.get("legacyItemId", "")
                qty_sold = int(line.get("quantity", 1))
                price = float(line.get("lineItemCost", {}).get("value", 0))

                product = self.db.get_product_by_sku(sku) if sku else None
                if not product and legacy_item_id:
                    product = self.db.get_product_by_platform_id("ebay", legacy_item_id)
                    if product:
                        logger.info(f"eBay order: resolved item {legacy_item_id} → variant {product['id']}")

                if not product:
                    logger.warning(f"eBay SKU '{sku}' / item {legacy_item_id} not in DB")

                self.db.insert_order({
                    "platform": "ebay",
                    "platform_order_id": order_id,
                    "product_id": product["product_id"] if product else None,
                    "sku": sku,
                    "quantity": qty_sold,
                    "unit_price": price,
                    "currency": "GBP",
                    "status": order.get("orderFulfillmentStatus", "NOT_STARTED"),
                    "ordered_at": order.get("creationDate"),
                    "fulfillment_status": order.get("orderFulfillmentStatus", "NOT_STARTED"),
                    "order_total": round(price * qty_sold, 2),
                    "item_name": line.get("title", ""),
                    "order_number": order.get("orderId", ""),
                })

                if product:
                    variant_db_id = product["id"]  # variant_id in v2
                    inv_rows = self.db.get_inventory(variant_id=variant_db_id)
                    if inv_rows:
                        inv = inv_rows[0]
                        new_stock = max(0, inv["total_stock"] - qty_sold)
                        self.db.upsert_inventory({
                            "id": inv["id"],
                            "variant_id": variant_db_id,
                            "product_id": product.get("product_id"),
                            "total_stock": new_stock,
                        })
                        # Mark needs_sync so hourly job pushes new stock to SS too
                        self.db.mark_variant_needs_sync(variant_db_id)
                        logger.info(f"eBay sale: {sku} qty={qty_sold} → stock now {new_stock} (needs_sync=TRUE)")

            processed += 1

        logger.info(f"eBay orders processed: {processed} new")
        return processed

    # ─── Unified Stock + Price Push (needs_sync=TRUE) ────────────────────────

    def sync_pending_variants(self):
        """
        Core sync step — runs hourly.

        1. Find all variants where needs_sync = TRUE
        2. For each variant: push current stock AND current prices to eBay + Squarespace
        3. Set needs_sync = FALSE, last_synced_at = NOW()

        This ensures stock and prices are NEVER pushed immediately when edited on the dashboard.
        Only this hourly job does the actual platform writes.
        """
        pending = self.db.get_variants_needing_sync()
        if not pending:
            logger.info("Sync queue: empty (no variants with needs_sync=TRUE)")
            return 0

        logger.info(f"Sync queue: {len(pending)} variant(s) to push")

        pushed = 0
        for item in pending:
            variant_id = item["variant_id"]
            stock = item["stock"]
            listings = item["listings"]  # channel_listings rows

            ss_variant_updates = []
            errors = []

            for listing in listings:
                channel = listing.get("channel")
                channel_product_id = listing.get("channel_product_id")
                channel_variant_id = listing.get("channel_variant_id")
                channel_price = listing.get("channel_price")

                if channel == "ebay":
                    if channel_product_id:
                        try:
                            # Push stock
                            self.ebay.update_inventory_quantity(
                                channel_product_id, stock,
                                listing.get("channel_variant_id")
                            )
                            # Push price
                            if channel_price is not None:
                                variation_sku = listing.get("channel_variant_id")
                                self.ebay.update_offer_price(
                                    channel_product_id, float(channel_price), variation_sku
                                )
                            # Mark listing as synced
                            self.db.mark_price_synced(listing["id"])
                            pushed += 1
                        except Exception as e:
                            errors.append(f"eBay {channel_product_id}: {e}")
                            logger.error(f"eBay push failed for variant {variant_id}: {e}")

                elif channel == "squarespace":
                    if channel_variant_id:
                        ss_variant_updates.append({
                            "variantId": channel_variant_id,
                            "quantity": stock,
                        })
                        # Push price separately
                        if channel_product_id and channel_variant_id and channel_price is not None:
                            try:
                                self.ss.update_variant_price(
                                    channel_product_id, channel_variant_id, float(channel_price)
                                )
                                self.db.mark_price_synced(listing["id"])
                                pushed += 1
                            except Exception as e:
                                errors.append(f"SS price {channel_variant_id}: {e}")
                                logger.error(f"SS price push failed for variant {variant_id}: {e}")

            # Batch push SS stock
            if ss_variant_updates:
                try:
                    self.ss.set_variant_stocks(ss_variant_updates)
                    pushed += len(ss_variant_updates)
                except Exception as e:
                    errors.append(f"SS stock batch: {e}")
                    logger.error(f"SS batch stock push failed for variant {variant_id}: {e}")

            # Mark synced only if no errors (so it retries next hour on error)
            if not errors:
                self.db.clear_variant_sync_flag(variant_id)
                logger.info(f"Variant {variant_id} (sku={item['sku']}): pushed stock={stock}, listings={len(listings)}")
            else:
                logger.warning(f"Variant {variant_id} kept needs_sync=TRUE due to errors: {errors}")

        logger.info(f"Sync complete: {pushed} platform update(s) for {len(pending)} variant(s)")
        return pushed

    # ─── Legacy compatibility wrappers ───────────────────────────────────────
    # These are called from run_full_sync / run_quick_check.
    # They now delegate to sync_pending_variants for needs_sync items,
    # and handle any price-only changes via channel_listings updated_at comparison.

    def sync_pending_stock_changes(self):
        """Delegate to sync_pending_variants (handles stock + price together)."""
        return self.sync_pending_variants()

    def sync_pending_price_changes(self):
        """
        Push price changes that aren't covered by needs_sync.
        (Safety net: catches any channel_listing updated_at > last_synced_at.)
        Skips listings for variants already covered by needs_sync (to avoid double-push).
        """
        pending_variant_ids = {
            item["variant_id"] for item in self.db.get_variants_needing_sync()
        }
        pending = self.db.get_pending_price_changes()
        pushed = 0
        for row in pending:
            variant_id = row.get("variant_id")
            # Skip — will be handled by sync_pending_variants
            if variant_id in pending_variant_ids:
                continue
            try:
                channel = row.get("channel")
                channel_product_id = row.get("channel_product_id")
                channel_variant_id = row.get("channel_variant_id")
                channel_price = row.get("channel_price")

                if channel == "squarespace":
                    if channel_product_id and channel_variant_id:
                        self.ss.update_variant_price(
                            channel_product_id, channel_variant_id, float(channel_price)
                        )
                elif channel == "ebay":
                    if channel_product_id:
                        variation_sku = channel_variant_id
                        self.ebay.update_offer_price(
                            channel_product_id, float(channel_price), variation_sku
                        )
                self.db.mark_price_synced(row["id"])
                pushed += 1
            except Exception as e:
                logger.error(f"Price sync failed for listing {row.get('id')}: {e}")
        logger.info(f"Price-only changes pushed: {pushed}")
        return pushed

    def update_daily_snapshots(self):
        today = datetime.now(timezone.utc).date().isoformat()
        orders = self.db.get_orders(limit=500)
        tally = {}
        for o in orders:
            if not o.get("ordered_at"):
                continue
            if not o.get("product_id"):
                continue
            sale_day = o["ordered_at"][:10]
            if sale_day != today:
                continue
            key = (o["product_id"], o["platform"])
            if key not in tally:
                tally[key] = {"units": 0, "revenue": 0.0}
            tally[key]["units"] += o["quantity"]
            tally[key]["revenue"] += float(o.get("unit_price", 0)) * o["quantity"]
        for (product_id, platform), vals in tally.items():
            self.db.upsert_snapshot({
                "product_id": product_id,
                "date": today,
                "platform": platform,
                "units_sold": vals["units"],
                "revenue": round(vals["revenue"], 2),
            })
        logger.info(f"Daily snapshot updated: {len(tally)} entries")

    def push_pending_tracking(self) -> int:
        orders = self.db.get_orders_needing_tracking_push()
        pushed = 0
        for order in orders:
            platform = (order.get("platform") or "").lower()
            platform_order_id = order.get("platform_order_id") or ""
            tracking_number = order.get("tracking_number") or ""
            carrier = order.get("tracking_carrier") or "Royal Mail"
            order_id = order.get("id")

            if not tracking_number or not platform_order_id:
                continue

            try:
                if "ebay" in platform:
                    self.ebay.create_shipping_fulfillment(platform_order_id, tracking_number, carrier)
                elif "squarespace" in platform:
                    self.ss.update_order_fulfillment(platform_order_id, tracking_number, carrier)
                self.db.mark_tracking_pushed(order_id)
                pushed += 1
            except Exception as e:
                logger.warning(f"Failed to push tracking for order {platform_order_id}: {e}")

        return pushed

    # ─── Main Sync Entry Points ──────────────────────────────────────────────

    def run_full_sync(self):
        """Hourly sync: new listings + orders + sync queue.

        Full catalogue refresh runs when:
          - DB is empty (first run / after a reset)
          - OR more than 24 hours have passed since the last catalogue sync
            (picks up any new Squarespace OR eBay products added since then)

        In between hourly runs: fast eBay-only incremental check for new listings.
        Skips entirely if a manual Sync Now ran within the last 30 minutes.
        """
        total = 0
        now = datetime.now(timezone.utc)

        # Skip if Sync Now button was used within the last 30 minutes
        last_quick = self.db.get_setting("last_quick_sync_at")
        if last_quick:
            try:
                lq_time = datetime.fromisoformat(last_quick.replace("Z", "+00:00"))
                mins_ago = (now - lq_time).total_seconds() / 60
                if mins_ago < 30:
                    logger.info(
                        "Manual Sync Now ran %.1f min ago — skipping scheduled hourly run to avoid overlap",
                        mins_ago,
                    )
                    return 0
            except Exception:
                pass

        product_count = self.db.count_products()

        last_cat_sync = self.db.get_setting("last_catalogue_sync")
        hours_since_catalogue = 999.0
        if last_cat_sync:
            try:
                last_time = datetime.fromisoformat(last_cat_sync.replace("Z", "+00:00"))
                hours_since_catalogue = (now - last_time).total_seconds() / 3600
            except Exception:
                pass

        if product_count == 0 or hours_since_catalogue >= 24:
            logger.info(
                "Running full catalogue sync (products=%d, hours_since_last=%.1f)...",
                product_count, hours_since_catalogue,
            )
            total += self.sync_product_catalogue()
            self.db.set_setting("last_catalogue_sync", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        else:
            if not last_cat_sync:
                last_cat_sync = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.info("Incremental eBay check (%.1f h since last full catalogue)", hours_since_catalogue)
            total += self.sync_new_listings(last_cat_sync)
            self.db.set_setting("last_catalogue_sync", now.strftime("%Y-%m-%dT%H:%M:%SZ"))

        last = self.db.get_setting("last_full_sync")
        since = last if last else (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

        total += self.process_squarespace_orders(since)
        total += self.process_ebay_orders(since)

        # Push queued stock + price changes (needs_sync=TRUE variants)
        total += self.sync_pending_variants()

        # Safety net: any price-only changes not covered by needs_sync
        total += self.sync_pending_price_changes()

        total += self.push_pending_tracking()
        self.update_daily_snapshots()
        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return total

    def run_quick_check(self):
        """Triggered by Sync Now button: full catalogue refresh + orders + queues.
        Always does a complete re-fetch of all listings when manually triggered.
        Records the timestamp so the next scheduled hourly run skips if this ran recently.
        """
        count = 0
        # Record that a manual sync ran — hourly job will skip if within 30 minutes
        self.db.set_setting("last_quick_sync_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

        count += self.push_pending_tracking()
        count += self.sync_pending_variants()
        count += self.sync_pending_price_changes()

        if self.db.is_sync_requested():
            logger.info("Sync Now: running full catalogue refresh...")
            self.db.clear_sync_request()
            count += self.sync_product_catalogue()
            self.db.set_setting("last_catalogue_sync",
                                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

        last = self.db.get_setting("last_full_sync")
        since = last if last else (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        count += self.process_squarespace_orders(since)
        count += self.process_ebay_orders(since)
        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.update_daily_snapshots()

        return count
