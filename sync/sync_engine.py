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

    def _save_ebay_item(self, item, merged_skus, existing_ebay_item_ids, existing_skus=None):
        """Save a single eBay item to DB. Returns 1 if new product created, 0 otherwise."""
        sku = item.get("sku")
        if not sku:
            return 0

        if sku in merged_skus:
            return 0

        # Fast in-memory check: existing_skus is a set of (bare_item_id, sku) tuples.
        # SKUs are NOT globally unique — same SKU can exist on different eBay listings.
        if existing_skus is not None:
            item_id_raw_check = item.get("item_id", "")
            bare_id_check = item_id_raw_check.split("|")[1] if "|" in item_id_raw_check else item_id_raw_check
            if (bare_id_check, sku) in existing_skus:
                return 0  # already in DB, skip

        # Also check by eBay item ID — prevents reimport when SKU format changed
        # (e.g. item previously imported under eBay item ID, now returning with custom variant SKU)
        # BUT only for single-variant items. Multi-variant items share the same item_id
        # across all variants, so checking item_id would skip variants 2+ of the same listing.
        item_id_raw = item.get("item_id", "")
        if item_id_raw and existing_ebay_item_ids and not item.get("is_variant"):
            normalised = item_id_raw.split("|")[1] if "|" in item_id_raw else item_id_raw
            if normalised in existing_ebay_item_ids:
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
                if existing_skus is not None:
                    bare_id_new = item_id_raw.split("|")[1] if "|" in item_id_raw else item_id_raw
                    existing_skus.add((bare_id_new, sku))  # keep in-memory set up to date
                # Track item ID for single-variant items only — multi-variant items
                # share the same item_id, so adding it would block sibling variants
                if item_id_raw and existing_ebay_item_ids is not None and not item.get("is_variant"):
                    normalised_new = item_id_raw.split("|")[1] if "|" in item_id_raw else item_id_raw
                    existing_ebay_item_ids.add(normalised_new)
            variation_sku = item.get("variation_sku") if item.get("is_variant") else None
            try:
                self.db.upsert_price({
                    "product_id": product_id,   # shim translates to variant_id
                    "platform": "ebay",
                    "sku": sku,
                    "price": float(item.get("price", 0.0)),
                    "currency": "GBP",
                    "platform_product_id": item.get("item_id", sku),
                    "platform_variant_id": variation_sku,
                })
                logger.debug("upsert_price OK for sku=%s variant_id=%s", sku, product_id)
            except Exception as e:
                logger.error("upsert_price FAILED for sku=%s variant_id=%s: %s", sku, product_id, e)

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

        # Build lookup: variant_id → product_id (from existing_variants dict)
        variant_id_to_product_id = {
            v["id"]: v["product_id"]
            for v in existing_variants.values()
            if v.get("id") and v.get("product_id")
        }

        # Build lookup: SS product ID → existing DB product ID
        # (so new variants on an already-linked SS product go to the right product)
        ss_product_to_db_product = {}
        for cl in existing_listings:
            if cl.get("channel") == "squarespace":
                ss_pid = cl.get("channel_product_id")
                vid    = cl.get("variant_id")
                if ss_pid and vid and vid in variant_id_to_product_id:
                    ss_product_to_db_product[ss_pid] = variant_id_to_product_id[vid]

        # Also track SS product IDs being created this run (for multi-variant SS products
        # where none of the variants exist yet)
        ss_product_to_new_product_id = {}  # ss_product_id → new product_id

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
                    variant_id = str(uuid_mod.uuid4())

                    # Check if this SS product is already linked to an existing DB product
                    # (handles new variants added to an already-synced product)
                    existing_db_product_id = (
                        ss_product_to_db_product.get(prod["id"]) or
                        ss_product_to_new_product_id.get(prod["id"])
                    )

                    if existing_db_product_id:
                        # Add variant to existing product — no new product row needed
                        product_id = existing_db_product_id
                        logger.info(f"SS variant {sku} → adding to existing product {product_id}")
                    else:
                        # Brand new product
                        product_id = str(uuid_mod.uuid4())
                        ss_product_to_new_product_id[prod["id"]] = product_id
                        new_products.append({
                            "id": product_id, "name": name,
                            "sku": sku,
                            "description": (prod.get("description") or "")[:500],
                            "status": "active", "active": True,
                        })

                    new_variants.append({
                        "id": variant_id, "product_id": product_id,
                        "internal_sku": sku, "needs_sync": False,
                        "option1": " / ".join(str(v) for v in (variant.get("attributes") or {}).values() if v) or None,
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
            sku = item.get("sku") or item.get("item_id", "")
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


    def sync_new_ebay_listings_only(self):
        """Incremental eBay check — only fetches listings created since last sync.

        Unlike sync_missing_ebay_listings() which re-expands ALL listings (causing
        duplicates when SKU formats change), this only processes truly NEW listings
        using eBay's StartTimeFrom filter.
        """
        last_sync = self.db.get_setting("last_full_sync")
        if not last_sync:
            last_sync = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"Checking for new eBay listings since {last_sync}...")
        new_summaries = self.ebay.get_new_listings(last_sync)
        if not new_summaries:
            logger.info("No new eBay listings found")
            return 0

        logger.info(f"Found {len(new_summaries)} new eBay listing(s) to process")

        merged_skus, existing_ebay_item_ids = self._load_blocklists()

        # Pre-load existing SKUs for dedup
        existing_skus = set()
        try:
            rows = self.db._rest("GET", "channel_listings",
                                 params={"select": "channel_sku,channel_product_id",
                                         "channel": "eq.ebay", "limit": "10000"})
            for r in rows:
                s = r.get("channel_sku")
                pid = r.get("channel_product_id", "")
                bare_id = pid.split("|")[1] if pid and "|" in pid else pid
                if s:
                    existing_skus.add((bare_id, s))
        except Exception as e:
            logger.warning("Could not pre-load existing_skus: %s", e)

        import threading
        results = []
        seen_groups = set()
        lock = threading.Lock()

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.ebay._expand_item, item, seen_groups, lock)
                       for item in new_summaries]
            for future in as_completed(futures):
                try:
                    entries = future.result()
                    if entries:
                        results.extend(entries)
                except Exception as e:
                    logger.warning("Listing expansion error: %s", e)

        added = 0
        for item in results:
            added += self._save_ebay_item(item, merged_skus, existing_ebay_item_ids, existing_skus)

        logger.info(f"New listings sync: {added} new product(s) added")
        return added

    # ─── Incremental New Listings (Hourly) ───────────────────────────────────

    def sync_missing_ebay_listings(self):
        """Fetch ALL eBay listings, expand every one, and insert any variants not already in DB.

        Uses a pre-loaded set of all existing variant SKUs for fast in-memory dedup —
        no DB call per variant. Handles the case where a listing was partially imported
        (e.g. some variants had custom SKUs, others didn't) by always checking ALL variants
        of every listing against the existing SKU set.

        Called every hourly sync and on every Sync Now press.
        """
        logger.info("Checking for missing eBay listings (full diff against DB)...")

        merged_skus, existing_ebay_item_ids = self._load_blocklists()

        # Pre-load (item_id, SKU) pairs from channel_listings — not just SKUs.
        # SKUs are NOT globally unique: e.g. "38mm Pearl" exists on multiple eBay listings.
        # We must dedup by (item_id, sku) pair to allow the same SKU on different listings.
        existing_skus = set()  # set of (bare_item_id, sku) tuples
        try:
            rows = self.db._rest("GET", "channel_listings",
                                 params={"select": "channel_sku,channel_product_id", "channel": "eq.ebay", "limit": "10000"})
            for r in rows:
                s = r.get("channel_sku")
                pid = r.get("channel_product_id", "")
                bare_id = pid.split("|")[1] if pid and "|" in pid else pid
                if s:
                    existing_skus.add((bare_id, s))
            logger.info("Pre-loaded %d existing eBay (item_id, sku) pairs", len(existing_skus))
        except Exception as e:
            logger.warning("Could not pre-load existing_skus — falling back to per-item DB checks: %s", e)

        # Get raw summary items (itemId, legacyItemId, title) — NOT expanded yet
        all_summaries = self.ebay._get_all_summary_items()
        if not all_summaries:
            logger.info("No eBay listings returned")
            return 0

        logger.info(
            "Expanding all %d eBay listing(s) to find missing variants...",
            len(all_summaries)
        )

        results = []
        seen_groups = set()
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.ebay._expand_item, item, seen_groups, lock) for item in all_summaries]
            for future in as_completed(futures):
                try:
                    entries = future.result()
                    if entries:
                        results.extend(entries)
                except Exception as e:
                    logger.warning("Listing expansion error: %s", e)

        added = 0
        for item in results:
            added += self._save_ebay_item(item, merged_skus, existing_ebay_item_ids, existing_skus)

        logger.info("Missing listings sync: %d new product(s) added", added)
        return added

    # ─── Order Processing ────────────────────────────────────────────────────

    def process_squarespace_orders(self, since: str = None):
        """Process new SS orders → read actual SS stock → mark variant needs_sync=TRUE."""
        logger.info(f"Processing SS orders since {since}")
        orders = self.ss.get_orders(modified_after=since)
        if not orders:
            return 0
        # Fetch SS inventory once up front — dict of {variantId: quantity}
        try:
            ss_inventory = {i["variantId"]: i.get("quantity", 0) for i in self.ss.get_inventory()}
        except Exception as e:
            logger.warning(f"Could not fetch SS inventory for order stock sync: {e}")
            ss_inventory = {}
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
                    # Read actual current stock from Squarespace rather than subtracting
                    # (Squarespace already decremented its own stock when the order was placed)
                    ss_vid = variant_id  # SS variantId from the order line item
                    current_stock = ss_inventory.get(ss_vid)
                    if current_stock is not None:
                        inv_rows = self.db.get_inventory(variant_id=variant_db_id)
                        if inv_rows:
                            inv = inv_rows[0]
                            self.db.upsert_inventory({
                                "id": inv["id"],
                                "variant_id": variant_db_id,
                                "product_id": product.get("product_id"),
                                "total_stock": current_stock,
                            })
                            self.db.mark_variant_needs_sync(variant_db_id)
                            logger.info(f"SS sale: {sku} → SS stock={current_stock} synced to dashboard (needs_sync=TRUE)")
                    else:
                        logger.warning(f"SS sale: {sku} variantId={ss_vid} not in SS inventory — skipping stock update")
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
                # lineItemCost is the LINE TOTAL (qty × unit price) — divide to get unit price
                line_total = float(line.get("lineItemCost", {}).get("value", 0))
                price = round(line_total / max(1, qty_sold), 2)

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
                    # Read actual current stock from eBay rather than subtracting
                    # (eBay already decremented its own stock when the order was placed)
                    current_stock = self.ebay.get_item_stock(legacy_item_id, variation_sku=sku or None)
                    if current_stock is not None:
                        inv_rows = self.db.get_inventory(variant_id=variant_db_id)
                        if inv_rows:
                            inv = inv_rows[0]
                            self.db.upsert_inventory({
                                "id": inv["id"],
                                "variant_id": variant_db_id,
                                "product_id": product.get("product_id"),
                                "total_stock": current_stock,
                            })
                            self.db.mark_variant_needs_sync(variant_db_id)
                            logger.info(f"eBay sale: item {legacy_item_id} → eBay stock={current_stock} synced to dashboard (needs_sync=TRUE)")
                    else:
                        logger.warning(f"eBay sale: item {legacy_item_id} sku={sku} — could not read stock from eBay, skipping")

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

                if channel_price is None:
                    continue  # skip rows with no price set
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

        In between hourly runs: only check for NEWLY LISTED eBay items since last sync.
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
            # Full catalogue sync (first run or daily refresh)
            reason = "First run" if product_count == 0 else "24h refresh"
            logger.info(f"{reason} — importing full catalogue from both platforms...")
            total += self.sync_product_catalogue()
            self.db.set_setting("last_catalogue_sync", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        else:
            # Incremental: only check for NEWLY LISTED eBay items since last sync
            # This prevents the duplicate bug where re-expanding ALL listings
            # caused items with changed SKU formats to be reimported.
            logger.info("Incremental new-listings-only check...")
            total += self.sync_new_ebay_listings_only()

        last = self.db.get_setting("last_full_sync")
        since = last if last else (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            total += self.process_squarespace_orders(since)
        except Exception as e:
            logger.error(f"process_squarespace_orders failed (non-fatal, continuing): {e}")

        try:
            total += self.process_ebay_orders(since)
        except Exception as e:
            logger.error(f"process_ebay_orders failed (non-fatal, continuing): {e}")

        # Fix broken eBay variant metadata BEFORE pushing stock/prices
        try:
            self.refresh_ebay_variant_metadata()
        except Exception as e:
            logger.warning(f"refresh_ebay_variant_metadata failed (non-fatal): {e}")

        # Push queued stock + price changes (needs_sync=TRUE variants)
        total += self.sync_pending_variants()

        # Safety net: any price-only changes not covered by needs_sync
        total += self.sync_pending_price_changes()

        total += self.push_pending_tracking()
        try:
            self.update_daily_snapshots()
        except Exception as e:
            logger.warning(f"update_daily_snapshots failed (non-fatal): {e}")
        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return total

    def refresh_ebay_variant_metadata(self):
        """Fix broken channel_variant_id and option1 for multi-variant eBay listings.

        Finds variants whose channel_variant_id looks like '<itemid>-vN' (positional placeholder)
        and re-fetches actual variation data from eBay to set correct values.

        For variants WITH a custom SKU on eBay: channel_variant_id = the SKU string
        For variants WITHOUT a custom SKU: channel_variant_id = JSON dict of variation specifics
            e.g. '{"Colour": "Red"}' — used by update_inventory_quantity/update_offer_price
        Also sets option1 on the variant for dashboard display.
        """
        import json, re

        # Find all eBay channel_listings that need fixing:
        # 1. Positional -vN channel_variant_ids (need full fix)
        # 2. JSON channel_variant_ids where the variant's option1 is still NULL (need option1 fix only)
        all_listings = self.db.get_all_channel_listings()
        
        # Load all variant option1 values in one query
        all_variants = self.db._rest("GET", "variants", {"select": "id,option1", "limit": "2000"})
        variant_option1 = {v["id"]: v.get("option1") for v in (all_variants or [])}
        
        broken = []
        for cl in all_listings:
            if cl.get("channel") != "ebay":
                continue
            cvid = cl.get("channel_variant_id") or ""
            vid = cl.get("variant_id")
            # Broken positional placeholder
            if re.match(r'^\d+-v\d+$', cvid) or re.match(r'^v1\|\d+\|\d+-v\d+$', cvid):
                broken.append(cl)
            # Already has JSON cvid but variant option1 is still NULL
            elif cvid.startswith("{") and vid and not variant_option1.get(vid):
                broken.append(cl)

        if not broken:
            logger.info("refresh_ebay_variant_metadata: no broken variants found")
            return 0

        logger.info("refresh_ebay_variant_metadata: %d broken channel_variant_ids to fix", len(broken))

        # Group by eBay item ID (legacy_id)
        by_item = {}
        for cl in broken:
            cpid = cl.get("channel_product_id", "")
            # Extract legacy item ID from v1|<id>|0 format
            if "|" in cpid:
                legacy_id = cpid.split("|")[1]
            else:
                legacy_id = cpid
            if legacy_id:
                by_item.setdefault(legacy_id, []).append(cl)

        fixed = 0
        for legacy_id, listings in by_item.items():
            try:
                variations = self.ebay.get_item_variations(legacy_id)
            except Exception as e:
                logger.warning("Failed to fetch variations for %s: %s", legacy_id, e)
                continue

            if not variations:
                logger.debug("No variations returned for %s — may be single item", legacy_id)
                continue

            # Match DB listings to eBay variations by position or SKU
            for cl in listings:
                variant_id = cl.get("variant_id")
                channel_sku = cl.get("channel_sku", "")
                
                # Fast path: if channel_variant_id is already JSON, just fix option1
                cvid_existing = cl.get("channel_variant_id", "")
                if cvid_existing.startswith("{") and variant_id:
                    try:
                        aspects = json.loads(cvid_existing)
                        option1 = " / ".join(v for v in aspects.values() if v and not str(v).startswith("_"))
                        if option1:
                            self.db._patch("variants", {"id": f"eq.{variant_id}"}, {"option1": option1})
                            fixed += 1
                    except Exception as e:
                        logger.error("Failed to fix option1 from existing JSON for %s: %s", variant_id, e)
                    continue
                matched_var = None

                # Try matching by channel_sku
                if channel_sku:
                    for var in variations:
                        if var.get("sku") == channel_sku:
                            matched_var = var
                            break

                # Try matching by internal_sku on the variant
                if not matched_var and variant_id:
                    # Get the variant's internal_sku
                    vdata = self.db.get_product_by_id(variant_id)
                    if vdata:
                        isk = vdata.get("internal_sku", "")
                        for var in variations:
                            if var.get("sku") and var["sku"] == isk:
                                matched_var = var
                                break

                # Fallback: match by position from the -vN suffix
                if not matched_var:
                    cvid = cl.get("channel_variant_id", "")
                    pos_m = re.search(r'-v(\d+)$', cvid)
                    if pos_m:
                        pos = int(pos_m.group(1))
                        if pos < len(variations):
                            matched_var = variations[pos]

                if not matched_var:
                    logger.warning("Could not match variant %s to any eBay variation on %s",
                                   cl.get("id"), legacy_id)
                    continue

                # Determine the correct channel_variant_id
                aspects = matched_var.get("aspects", {})
                ebay_sku = matched_var.get("sku")

                if ebay_sku:
                    new_cvid = ebay_sku
                elif aspects:
                    new_cvid = json.dumps(aspects, separators=(",", ":"))
                else:
                    logger.warning("Variation on %s has neither SKU nor aspects — skipping", legacy_id)
                    continue

                # Build option1 label from aspects
                option1 = " / ".join(v for v in aspects.values() if v and not v.startswith("_")) if aspects else None

                # Update channel_listing
                try:
                    self.db._patch("channel_listings",
                                   {"id": f"eq.{cl['id']}"},
                                   {"channel_variant_id": new_cvid,
                                    "channel_sku": ebay_sku or cl.get("channel_sku")})
                except Exception as e:
                    logger.error("Failed to update channel_listing %s: %s", cl["id"], e)
                    continue

                # Update variant option1 (name column is on products, not variants)
                if variant_id and option1:
                    try:
                        self.db._patch("variants", {"id": f"eq.{variant_id}"}, {"option1": option1})
                    except Exception as e:
                        logger.error("Failed to update variant %s option1: %s", variant_id, e)

                fixed += 1
                logger.debug("Fixed variant %s: channel_variant_id=%s, option1=%s",
                             variant_id, new_cvid, option1)

        logger.info("refresh_ebay_variant_metadata: fixed %d/%d broken variants", fixed, len(broken))
        return fixed

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

        # Fix any broken variant metadata (positional -vN IDs → real SKU/aspects)
        count += self.refresh_ebay_variant_metadata()

        # Full catalogue refresh (both platforms) — clean reimport with proper dedup
        count += self.sync_product_catalogue()
        self.db.set_setting("last_catalogue_sync",
                            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

        last = self.db.get_setting("last_full_sync")
        since = last if last else (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            count += self.process_squarespace_orders(since)
        except Exception as e:
            logger.error(f"process_squarespace_orders failed (non-fatal, continuing): {e}")

        try:
            count += self.process_ebay_orders(since)
        except Exception as e:
            logger.error(f"process_ebay_orders failed (non-fatal, continuing): {e}")

        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        try:
            self.update_daily_snapshots()
        except Exception as e:
            logger.warning(f"update_daily_snapshots failed (non-fatal): {e}")

        return count
