"""
Supabase (PostgreSQL) database client — v2

Schema used:
  products        — product groups (id, name, sku [legacy], status, cost_price, ...)
  variants        — individual SKUs (id, product_id, internal_sku, needs_sync, last_synced_at)
  inventory       — stock levels  (id, variant_id, product_id [legacy], total_stock, low_stock_threshold)
  channel_listings— per-platform listings (id, variant_id, channel, channel_sku,
                    channel_price, channel_product_id, channel_variant_id, last_synced_at)
  orders          — sales records
  sales_trends    — daily snapshot data
  sync_log        — audit log
  settings        — key-value store

SYNC FLAG:
  Editing stock or prices → sets variants.needs_sync = TRUE
  Hourly GitHub Actions job → finds needs_sync=TRUE rows → pushes → sets needs_sync=FALSE
  No more stock_push_* settings entries.
"""
import os
import json
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_KEY_ENV = "SUPABASE_SERVICE_KEY"


class Database:
    def __init__(self):
        self.url = os.environ[SUPABASE_URL_ENV].rstrip("/")
        self.key = os.environ[SUPABASE_KEY_ENV]
        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _rest(self, method, table, params=None, payload=None, headers_extra=None):
        h = {**self.headers, **(headers_extra or {})}
        r = requests.request(
            method,
            f"{self.url}/rest/v1/{table}",
            headers=h, params=params, json=payload, timeout=30,
        )
        r.raise_for_status()
        return r.json() if r.text else []

    def _patch(self, table, params, payload):
        r = requests.patch(
            f"{self.url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": "return=representation"},
            params=params,
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json() if r.text else []

    def _delete(self, table, params):
        r = requests.delete(
            f"{self.url}/rest/v1/{table}",
            headers={k: v for k, v in self.headers.items() if k != "Prefer"},
            params=params,
            timeout=30,
        )
        r.raise_for_status()

    # ─── Settings ────────────────────────────────────────────────────────────

    def get_setting(self, key: str):
        rows = self._rest("GET", "settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: str):
        self._rest("POST", "settings",
                   payload={"key": key, "value": str(value) if value is not None else None},
                   headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    def get_all_variants(self):
        """Fetch ALL variants in one call. Returns dict keyed by internal_sku."""
        rows = self._rest("GET", "variants", params={"select": "*", "limit": "10000"})
        return {r["internal_sku"]: r for r in rows if r.get("internal_sku")}

    def get_all_channel_listings(self):
        """Fetch ALL channel_listings in one call. Returns list."""
        return self._rest("GET", "channel_listings", params={"select": "*", "limit": "10000"})

    def bulk_insert_rows(self, table: str, rows: list, batch_size: int = 500):
        """Bulk insert rows in batches. Uses return=minimal for speed."""
        if not rows:
            return
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                self._rest("POST", table, payload=batch,
                           headers_extra={"Prefer": "return=minimal"})
                logger.info(f"Bulk insert {table}: {len(batch)} rows (batch {i // batch_size + 1})")
            except Exception as e:
                logger.error(f"Bulk insert {table} batch {i // batch_size + 1} failed: {e}")
                raise

    def count_products(self):
        rows = self._rest("GET", "variants", params={"select": "id", "limit": "1"})
        # Use count header via HEAD request for accuracy
        r = requests.head(
            f"{self.url}/rest/v1/variants",
            headers={**self.headers, "Prefer": "count=exact"},
            timeout=30,
        )
        cr = r.headers.get("content-range", "")
        if "/" in cr:
            try:
                return int(cr.split("/")[1])
            except (ValueError, IndexError):
                pass
        return len(rows)

    # ─── Products ────────────────────────────────────────────────────────────
    # In v2, "product" = a variants row joined with its products row.
    # Returning a unified dict keeps sync_engine.py changes minimal.

    def get_product_by_sku(self, sku: str):
        """Return a unified product+variant dict keyed by variant id.
        Returns None if not found."""
        rows = self._rest("GET", "variants",
                          params={"internal_sku": f"eq.{sku}", "select": "*", "limit": "1"})
        if not rows:
            return None
        v = rows[0]
        # Fetch the parent product for name/description
        prod_rows = self._rest("GET", "products",
                               params={"id": f"eq.{v['product_id']}", "select": "*", "limit": "1"})
        if not prod_rows:
            return None
        p = prod_rows[0]
        return {**p, "id": v["id"], "product_id": v["product_id"],
                "sku": v["internal_sku"], "needs_sync": v.get("needs_sync", False),
                "last_synced_at": v.get("last_synced_at")}

    def get_product_by_id(self, variant_id: str):
        """Return unified dict for a given variant id."""
        rows = self._rest("GET", "variants",
                          params={"id": f"eq.{variant_id}", "select": "*", "limit": "1"})
        if not rows:
            return None
        v = rows[0]
        prod_rows = self._rest("GET", "products",
                               params={"id": f"eq.{v['product_id']}", "select": "*", "limit": "1"})
        if not prod_rows:
            return None
        p = prod_rows[0]
        return {**p, "id": v["id"], "product_id": v["product_id"],
                "sku": v["internal_sku"], "needs_sync": v.get("needs_sync", False)}

    def upsert_product(self, product: dict):
        """Create or update a product+variant pair.
        product dict: {name, sku, description}
        Returns a list with one unified dict (id = variant_id).
        """
        sku = product.get("sku")
        name = product.get("name", "")

        # Check if variant with this SKU already exists
        if sku:
            existing_variant = self._rest("GET", "variants",
                                          params={"internal_sku": f"eq.{sku}", "select": "*"})
            if existing_variant:
                v = existing_variant[0]
                # Update product name if changed
                self._patch("products", {"id": f"eq.{v['product_id']}"},
                            {"name": name, "updated_at": datetime.now(timezone.utc).isoformat()})
                return [self.get_product_by_sku(sku)]

        # Create new product row
        now = datetime.now(timezone.utc).isoformat()
        prod_payload = {
            "name": name,
            "sku": sku,  # kept for legacy compat
            "description": product.get("description", ""),
            "status": "active",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
        try:
            prod_rows = self._rest("POST", "products", payload=prod_payload,
                                   headers_extra={"Prefer": "return=representation"})
        except Exception as e:
            if "409" in str(e) or "23505" in str(e) or "conflict" in str(e).lower():
                # Race condition — SKU already inserted, return existing
                if sku:
                    logger.warning(f"Duplicate product sku {sku}, returning existing")
                    return [self.get_product_by_sku(sku)]
            raise

        if not prod_rows:
            return []
        prod = prod_rows[0]
        product_id = prod["id"]

        # Create variant row
        var_payload = {
            "product_id": product_id,
            "internal_sku": sku,
            "needs_sync": False,
            "created_at": now,
            "updated_at": now,
        }
        try:
            var_rows = self._rest("POST", "variants", payload=var_payload,
                                  headers_extra={"Prefer": "return=representation"})
        except Exception as e:
            if "409" in str(e) or "23505" in str(e):
                # Variant race condition
                if sku:
                    return [self.get_product_by_sku(sku)]
            raise

        if not var_rows:
            return []
        v = var_rows[0]
        return [{**prod, "id": v["id"], "product_id": product_id,
                 "sku": v["internal_sku"], "needs_sync": False}]

    def update_product_name(self, variant_id: str, name: str):
        """Update name on the parent product via variant_id."""
        rows = self._rest("GET", "variants",
                          params={"id": f"eq.{variant_id}", "select": "product_id"})
        if rows:
            self._patch("products", {"id": f"eq.{rows[0]['product_id']}"},
                        {"name": name, "updated_at": datetime.now(timezone.utc).isoformat()})

    def delete_product(self, variant_id: str):
        """Delete a variant and (if it was the last variant) its parent product."""
        rows = self._rest("GET", "variants",
                          params={"id": f"eq.{variant_id}", "select": "product_id"})
        if not rows:
            return
        product_id = rows[0]["product_id"]

        # Delete channel_listings (cascades via FK, but explicit for clarity)
        self._delete("channel_listings", {"variant_id": f"eq.{variant_id}"})
        # Delete inventory
        self._delete("inventory", {"variant_id": f"eq.{variant_id}"})
        # Delete variant
        self._delete("variants", {"id": f"eq.{variant_id}"})

        # Delete parent product if no variants remain
        remaining = self._rest("GET", "variants",
                               params={"product_id": f"eq.{product_id}", "select": "id"})
        if not remaining:
            self._delete("orders", {"product_id": f"eq.{product_id}"})
            self._delete("sales_trends", {"product_id": f"eq.{product_id}"})
            self._delete("products", {"id": f"eq.{product_id}"})

    # ─── eBay helper ─────────────────────────────────────────────────────────

    def get_all_ebay_products(self):
        """Return list of {id (variant_id), sku, name} for all eBay-listed variants."""
        rows = self._rest("GET", "channel_listings",
                          params={"channel": "eq.ebay", "select": "variant_id", "limit": "5000"})
        variant_ids = list({r["variant_id"] for r in rows if r.get("variant_id")})
        if not variant_ids:
            return []
        id_filter = "in.(" + ",".join(variant_ids) + ")"
        variants = self._rest("GET", "variants",
                              params={"id": id_filter, "select": "id,internal_sku,product_id", "limit": "5000"})
        result = []
        for v in variants:
            result.append({"id": v["id"], "sku": v["internal_sku"], "product_id": v["product_id"]})
        return result

    # ─── Channel Listings (replaces platform_pricing) ────────────────────────

    def get_channel_listings_for_variant(self, variant_id: str, channel: str = None):
        params = {"variant_id": f"eq.{variant_id}", "select": "*"}
        if channel:
            params["channel"] = f"eq.{channel}"
        return self._rest("GET", "channel_listings", params=params)

    def get_channel_listing_by_channel_variant_id(self, channel: str, channel_variant_id: str):
        """Check if a platform variant_id is already linked to any variant."""
        if not channel_variant_id:
            return None
        rows = self._rest("GET", "channel_listings", params={
            "channel": f"eq.{channel}",
            "channel_variant_id": f"eq.{channel_variant_id}",
            "select": "variant_id",
            "limit": "1",
        })
        return rows[0]["variant_id"] if rows else None

    def get_variant_by_channel_item_id(self, channel: str, channel_product_id: str):
        """Find a variant by eBay item id or SS product id."""
        # Extract bare item id if in format "v1|XXXXX|0"
        bare_id = channel_product_id.split("|")[1] if "|" in channel_product_id else channel_product_id
        # Try exact match first
        rows = self._rest("GET", "channel_listings", params={
            "channel": f"eq.{channel}",
            "channel_product_id": f"like.*{bare_id}*",
            "select": "variant_id",
            "limit": "1",
        })
        if rows:
            return self.get_product_by_id(rows[0]["variant_id"])
        return None

    def upsert_channel_listing(self, listing: dict):
        """Upsert a channel listing.
        listing dict: {variant_id, channel, channel_sku, channel_price,
                       channel_product_id, channel_variant_id}
        """
        now = datetime.now(timezone.utc).isoformat()
        listing["updated_at"] = now
        if "last_synced_at" not in listing:
            listing["last_synced_at"] = now

        variant_id = listing.get("variant_id")
        channel = listing.get("channel")
        if variant_id and channel:
            existing = self._rest("GET", "channel_listings", params={
                "variant_id": f"eq.{variant_id}",
                "channel": f"eq.{channel}",
                "select": "id",
            })
            if existing:
                rec_id = existing[0]["id"]
                patch = dict(listing)
                # Don't overwrite channel_variant_id with null if already set
                if patch.get("channel_variant_id") is None:
                    patch.pop("channel_variant_id", None)
                return self._patch("channel_listings", {"id": f"eq.{rec_id}"}, patch)
        return self._rest("POST", "channel_listings", payload=listing,
                          headers_extra={"Prefer": "return=representation"})

    def get_pending_price_changes(self):
        """Return channel_listings where updated_at > last_synced_at (price was changed)."""
        rows = self._rest("GET", "channel_listings",
                          params={"select": "*", "limit": "5000"})
        pending = []
        for r in rows:
            last_synced = r.get("last_synced_at")
            updated = r.get("updated_at")
            if updated and (not last_synced or updated > last_synced):
                pending.append(r)
        return pending

    def mark_price_synced(self, listing_id: str):
        self._patch("channel_listings", {"id": f"eq.{listing_id}"},
                    {"last_synced_at": datetime.now(timezone.utc).isoformat()})

    # ─── needs_sync flag (replaces stock_push_* settings queue) ──────────────

    def mark_variant_needs_sync(self, variant_id: str):
        """Mark a variant as needing stock+price push on next sync run."""
        self._patch("variants", {"id": f"eq.{variant_id}"},
                    {"needs_sync": True, "updated_at": datetime.now(timezone.utc).isoformat()})
        logger.info(f"Marked variant {variant_id} needs_sync=TRUE")

    def get_variants_needing_sync(self):
        """Return all channel_listings for variants where needs_sync=TRUE.
        Returns list of {variant_id, channel_listings, stock}.
        """
        # Get variants that need sync
        variants = self._rest("GET", "variants", params={
            "needs_sync": "eq.true",
            "select": "id,internal_sku,product_id",
            "limit": "5000",
        })
        if not variants:
            return []

        result = []
        for v in variants:
            variant_id = v["id"]
            # Get current stock
            inv_rows = self.get_inventory(variant_id=variant_id)
            stock = inv_rows[0]["total_stock"] if inv_rows else 0
            # Get channel listings for this variant
            listings = self.get_channel_listings_for_variant(variant_id)
            result.append({
                "variant_id": variant_id,
                "sku": v["internal_sku"],
                "product_id": v["product_id"],
                "stock": stock,
                "listings": listings,
            })
        return result

    def clear_variant_sync_flag(self, variant_id: str):
        """Mark a variant as synced."""
        now = datetime.now(timezone.utc).isoformat()
        self._patch("variants", {"id": f"eq.{variant_id}"},
                    {"needs_sync": False, "last_synced_at": now, "updated_at": now})

    # ─── Legacy queue compatibility (for any callers still using old API) ─────
    # These shim methods forward to the new needs_sync approach.

    def queue_stock_push(self, variant_id: str, new_stock: int):
        """Legacy shim: mark variant needs_sync=TRUE instead of writing to settings."""
        self.mark_variant_needs_sync(variant_id)

    def get_stock_push_queue(self):
        """Legacy shim: return variants needing sync as queue-style list."""
        return [{"product_id": v["variant_id"], "stock": v["stock"]}
                for v in self.get_variants_needing_sync()]

    def clear_stock_push(self, variant_id: str):
        """Legacy shim: clear needs_sync flag."""
        self.clear_variant_sync_flag(variant_id)

    # ─── Inventory ───────────────────────────────────────────────────────────

    def get_inventory(self, product_id: str = None, variant_id: str = None):
        params = {"select": "*"}
        if variant_id:
            params["variant_id"] = f"eq.{variant_id}"
        elif product_id:
            # Try variant_id first (in case product_id is actually a variant_id)
            # This handles legacy callers that pass variant_id as product_id
            params["variant_id"] = f"eq.{product_id}"
            rows = self._rest("GET", "inventory", params=params)
            if rows:
                return rows
            # Fall back to product_id FK
            params = {"select": "*", "product_id": f"eq.{product_id}"}
        return self._rest("GET", "inventory", params=params)

    def upsert_inventory(self, inv: dict):
        inv["updated_at"] = datetime.now(timezone.utc).isoformat()
        variant_id = inv.get("variant_id") or inv.get("product_id")  # accept either key
        if variant_id:
            existing = self._rest("GET", "inventory",
                                  params={"variant_id": f"eq.{variant_id}", "select": "id"})
            if not existing:
                # Also try product_id for very old rows
                existing = self._rest("GET", "inventory",
                                      params={"product_id": f"eq.{variant_id}", "select": "id"})
            if existing:
                inv_id = existing[0]["id"]
                return self._patch("inventory", {"id": f"eq.{inv_id}"}, inv)
        return self._rest("POST", "inventory", payload=inv,
                          headers_extra={"Prefer": "return=representation"})

    # ─── Legacy platform_pricing shims ───────────────────────────────────────
    # The sync engine still calls upsert_price / get_platform_pricing_for_product
    # These shim to channel_listings using variant_id.

    def upsert_price(self, price_row: dict):
        """Shim: maps platform_pricing-style dict to channel_listings upsert."""
        variant_id = price_row.get("product_id")  # product_id was actually variant_id post-migration
        if not variant_id:
            logger.warning("upsert_price called with no product_id/variant_id — skipping")
            return []

        listing = {
            "variant_id": variant_id,
            "channel": price_row.get("platform"),
            "channel_sku": price_row.get("sku") or None,
            "channel_price": price_row.get("price"),
            "channel_product_id": price_row.get("platform_product_id"),
            "channel_variant_id": price_row.get("platform_variant_id"),
        }
        # Preserve last_synced_at if supplied (catalogue imports pass it)
        if "last_synced_at" in price_row:
            listing["last_synced_at"] = price_row["last_synced_at"]
        return self.upsert_channel_listing(listing)

    def get_platform_pricing_for_product(self, variant_id: str, platform: str = None):
        """Shim: returns channel_listings for a variant, in platform_pricing-style dicts."""
        listings = self.get_channel_listings_for_variant(variant_id, channel=platform)
        # Map back to expected keys
        result = []
        for cl in listings:
            result.append({
                "id": cl["id"],
                "product_id": variant_id,
                "platform": cl["channel"],
                "price": cl["channel_price"],
                "currency": "GBP",
                "platform_product_id": cl["channel_product_id"],
                "platform_variant_id": cl["channel_variant_id"],
                "last_synced_at": cl.get("last_synced_at"),
                "updated_at": cl.get("updated_at"),
            })
        return result

    def get_platform_pricing_by_variant_id(self, platform: str, variant_id: str):
        """Shim: check if a channel_variant_id is already linked."""
        return self.get_channel_listing_by_channel_variant_id(platform, variant_id)

    def get_product_by_platform_id(self, platform: str, platform_product_id: str):
        """Find a variant by its platform item/product id."""
        return self.get_variant_by_channel_item_id(platform, platform_product_id)

    def get_prices(self, product_id: str = None):
        """Return channel_listings as pricing-style dicts."""
        if product_id:
            return self.get_platform_pricing_for_product(product_id)
        rows = self._rest("GET", "channel_listings", params={"select": "*", "limit": "5000"})
        return rows

    def mark_price_synced_legacy(self, price_id: str):
        self.mark_price_synced(price_id)

    # ─── Orders ──────────────────────────────────────────────────────────────

    def order_exists(self, platform: str, platform_order_id: str) -> bool:
        rows = self._rest("GET", "orders",
                          params={"platform": f"eq.{platform}",
                                  "platform_order_id": f"eq.{platform_order_id}",
                                  "select": "id"})
        return len(rows) > 0

    def insert_order(self, order: dict):
        return self._rest("POST", "orders", payload=order)

    def update_order_status(self, platform_order_id: str, status: str):
        self._patch("orders", {"platform_order_id": f"eq.{platform_order_id}"},
                    {"fulfillment_status": status, "status": status})

    def get_orders(self, platform: str = None, limit: int = 200):
        params = {"select": "*", "order": "ordered_at.desc", "limit": str(limit)}
        if platform:
            params["platform"] = f"eq.{platform}"
        return self._rest("GET", "orders", params=params)

    def get_order_by_id(self, order_id: str):
        rows = self._rest("GET", "orders", params={"id": f"eq.{order_id}", "select": "*"})
        return rows[0] if rows else None

    def update_order_tracking(self, order_id: str, tracking_number: str, carrier: str,
                              status: str = "SHIPPED"):
        self._patch("orders", {"id": f"eq.{order_id}"},
                    {"tracking_number": tracking_number,
                     "tracking_carrier": carrier,
                     "fulfillment_status": status})

    def get_orders_needing_tracking_push(self):
        try:
            return self._rest("GET", "orders", params={
                "select": "*",
                "tracking_number": "not.is.null",
                "fulfillment_status": "eq.SHIPPED",
            })
        except Exception as e:
            logger.warning(f"get_orders_needing_tracking_push failed: {e}")
            return []

    def mark_tracking_pushed(self, order_id: str):
        try:
            self._patch("orders", {"id": f"eq.{order_id}"},
                        {"fulfillment_status": "TRACKING_PUSHED", "status": "TRACKING_PUSHED"})
        except Exception as e:
            logger.warning(f"mark_tracking_pushed failed for {order_id}: {e}")

    # ─── Sales Trends ────────────────────────────────────────────────────────

    def upsert_snapshot(self, snap: dict):
        snap["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            params = {
                "date": f"eq.{snap['date']}",
                "platform": f"eq.{snap['platform']}",
            }
            if snap.get("product_id"):
                params["product_id"] = f"eq.{snap['product_id']}"
            else:
                params["product_id"] = "is.null"
            requests.delete(f"{self.url}/rest/v1/sales_trends",
                            headers=self.headers, params=params, timeout=30)
        except Exception as e:
            logger.warning(f"Snapshot pre-delete failed (non-fatal): {e}")
        return self._rest("POST", "sales_trends", payload=snap)

    def get_snapshots(self, product_id: str = None, days: int = 30):
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        params = {"select": "*", "date": f"gte.{cutoff}", "order": "date.asc"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "sales_trends", params=params)

    # ─── Sync Log ────────────────────────────────────────────────────────────

    def start_sync_log(self, sync_type: str) -> str:
        rows = self._rest("POST", "sync_log",
                          payload={"sync_type": sync_type, "status": "started",
                                   "started_at": datetime.now(timezone.utc).isoformat()})
        return rows[0]["id"]

    def finish_sync_log(self, log_id: str, status: str, items: int = 0, errors: str = None):
        details = json.dumps({"items_synced": items}) if items else None
        self._patch("sync_log", {"id": f"eq.{log_id}"},
                    {"status": status, "details": details,
                     "error_message": errors,
                     "completed_at": datetime.now(timezone.utc).isoformat()})

    def get_sync_logs(self, limit: int = 20):
        return self._rest("GET", "sync_log",
                          params={"select": "*", "order": "started_at.desc", "limit": str(limit)})

    # ─── Manual sync flag ────────────────────────────────────────────────────

    def is_sync_requested(self) -> bool:
        val = self.get_setting("manual_sync_requested")
        return val == "true"

    def request_sync(self):
        self.set_setting("manual_sync_requested", "true")

    def clear_sync_request(self):
        self.set_setting("manual_sync_requested", "false")
