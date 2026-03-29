"""
Supabase (PostgreSQL) database client.
All DB interactions go through this module.
"""
import os
import json
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/")
        self.key = os.environ["SUPABASE_SERVICE_KEY"]
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

    # ─── Settings (key-value) ────────────────────────────────────────────────

    def count_products(self):
        rows = self._rest("GET", "products", params={"select": "id"})
        return len(rows)

    def get_setting(self, key: str):
        rows = self._rest("GET", "settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: str):
        self._rest("POST", "settings", payload={"key": key, "value": str(value)},
                   headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    # ─── Stock Push Queue ────────────────────────────────────────────────────
    # Each queued push is stored as a settings row: key=stock_push_{product_id}, value=new_stock_int
    # This means only EXPLICITLY changed products ever get pushed to platforms.

    def queue_stock_push(self, product_id: str, new_stock: int):
        """Queue a stock push for a product. Called after any stock change."""
        self.set_setting(f"stock_push_{product_id}", str(new_stock))
        logger.info(f"Queued stock push for product {product_id} → {new_stock}")

    def get_stock_push_queue(self):
        """Return list of {product_id, stock} for all queued stock pushes."""
        try:
            rows = self._rest("GET", "settings", params={
                "key": "like.stock_push_%",
                "select": "key,value",
            })
            result = []
            for r in rows:
                product_id = r["key"][len("stock_push_"):]
                try:
                    result.append({"product_id": product_id, "stock": int(r["value"])})
                except (ValueError, TypeError):
                    pass
            return result
        except Exception as e:
            logger.warning(f"get_stock_push_queue failed: {e}")
            return []

    def clear_stock_push(self, product_id: str):
        """Remove a product from the stock push queue after successful push."""
        try:
            r = requests.delete(
                f"{self.url}/rest/v1/settings",
                headers={k: v for k, v in self.headers.items() if k != "Prefer"},
                params={"key": f"eq.stock_push_{product_id}"},
                timeout=30,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"clear_stock_push failed for {product_id}: {e}")

    # ─── Products ────────────────────────────────────────────────────────────

    def get_products(self):
        return self._rest("GET", "products", params={"select": "*", "order": "name.asc"})

    def upsert_product(self, product: dict):
        sku = product.get("sku")
        if sku:
            existing = self._rest("GET", "products", params={"sku": f"eq.{sku}", "select": "id"})
            if existing:
                rec_id = existing[0]["id"]
                r = requests.patch(
                    f"{self.url}/rest/v1/products",
                    headers={**self.headers, "Prefer": "return=representation"},
                    params={"id": f"eq.{rec_id}"},
                    json=product,
                    timeout=30,
                )
                r.raise_for_status()
                return r.json() if r.text else []
        return self._rest("POST", "products", payload=product,
                          headers_extra={"Prefer": "return=representation"})

    def get_product_by_sku(self, sku: str):
        rows = self._rest("GET", "products", params={"sku": f"eq.{sku}", "select": "*"})
        return rows[0] if rows else None

    def update_product_name(self, product_id: str, name: str):
        r = requests.patch(
            f"{self.url}/rest/v1/products",
            headers=self.headers,
            params={"id": f"eq.{product_id}"},
            json={"name": name, "updated_at": datetime.now(timezone.utc).isoformat()},
            timeout=30,
        )
        r.raise_for_status()

    def delete_product(self, product_id: str):
        """Delete a product and its related pricing/inventory rows."""
        # Delete platform_pricing rows first
        requests.delete(f"{self.url}/rest/v1/platform_pricing", headers=self.headers,
                        params={"product_id": f"eq.{product_id}"}, timeout=30)
        # Delete inventory rows
        requests.delete(f"{self.url}/rest/v1/inventory", headers=self.headers,
                        params={"product_id": f"eq.{product_id}"}, timeout=30)
        # Delete the product
        r = requests.delete(f"{self.url}/rest/v1/products", headers=self.headers,
                            params={"id": f"eq.{product_id}"}, timeout=30)
        r.raise_for_status()

    # ─── Platform Pricing Lookups ────────────────────────────────────────────

    def get_platform_pricing_for_product(self, product_id: str, platform: str = None):
        params = {"select": "*", "product_id": f"eq.{product_id}"}
        if platform:
            params["platform"] = f"eq.{platform}"
        return self._rest("GET", "platform_pricing", params=params)

    def get_product_by_platform_id(self, platform: str, platform_product_id: str):
        rows = self._rest("GET", "platform_pricing",
                          params={"platform": f"eq.{platform}",
                                  "platform_product_id": f"eq.{platform_product_id}",
                                  "select": "product_id"})
        if not rows:
            return None
        product_id = rows[0]["product_id"]
        prod_rows = self._rest("GET", "products", params={"id": f"eq.{product_id}", "select": "*"})
        return prod_rows[0] if prod_rows else None

    # ─── Inventory ───────────────────────────────────────────────────────────

    def get_inventory(self, product_id: str = None):
        params = {"select": "*"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "inventory", params=params)

    def upsert_inventory(self, inv: dict):
        inv["updated_at"] = datetime.now(timezone.utc).isoformat()
        product_id = inv.get("product_id")
        if product_id:
            # Check if inventory record already exists for this product
            existing = self._rest("GET", "inventory", params={"product_id": f"eq.{product_id}", "select": "id"})
            if existing:
                # UPDATE existing record via PATCH
                inv_id = existing[0]["id"]
                r = requests.patch(
                    f"{self.url}/rest/v1/inventory",
                    headers={**self.headers, "Prefer": "return=representation"},
                    params={"id": f"eq.{inv_id}"},
                    json=inv,
                    timeout=30,
                )
                r.raise_for_status()
                return r.json() if r.text else []
        # INSERT new record
        return self._rest("POST", "inventory", payload=inv,
                          headers_extra={"Prefer": "return=representation"})

    # ─── Platform Pricing ────────────────────────────────────────────────────

    def get_prices(self, product_id: str = None):
        params = {"select": "*"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "platform_pricing", params=params)

    def upsert_price(self, price_row: dict):
        price_row["updated_at"] = datetime.now(timezone.utc).isoformat()
        product_id = price_row.get("product_id")
        platform   = price_row.get("platform")
        if product_id and platform:
            existing = self._rest("GET", "platform_pricing", params={
                "product_id": f"eq.{product_id}",
                "platform": f"eq.{platform}",
                "select": "id"
            })
            if existing:
                rec_id = existing[0]["id"]
                r = requests.patch(
                    f"{self.url}/rest/v1/platform_pricing",
                    headers={**self.headers, "Prefer": "return=representation"},
                    params={"id": f"eq.{rec_id}"},
                    json=price_row,
                    timeout=30,
                )
                r.raise_for_status()
                return r.json() if r.text else []
        return self._rest("POST", "platform_pricing", payload=price_row,
                          headers_extra={"Prefer": "return=representation"})

    def get_pending_price_changes(self):
        rows = self._rest("GET", "platform_pricing", params={"select": "*"})
        pending = []
        for r in rows:
            last_synced = r.get("last_synced_at")
            updated = r.get("updated_at")
            if updated and (not last_synced or updated > last_synced):
                pending.append(r)
        return pending

    def mark_price_synced(self, price_id: str):
        r = requests.patch(
            f"{self.url}/rest/v1/platform_pricing",
            headers=self.headers,
            params={"id": f"eq.{price_id}"},
            json={"last_synced_at": datetime.now(timezone.utc).isoformat()},
            timeout=30,
        )
        r.raise_for_status()

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
        r = requests.patch(
            f"{self.url}/rest/v1/orders",
            headers=self.headers,
            params={"platform_order_id": f"eq.{platform_order_id}"},
            json={"fulfillment_status": status, "status": status},
            timeout=30,
        )
        r.raise_for_status()

    def get_orders(self, platform: str = None, limit: int = 200):
        params = {"select": "*", "order": "ordered_at.desc", "limit": str(limit)}
        if platform:
            params["platform"] = f"eq.{platform}"
        return self._rest("GET", "orders", params=params)

    def get_order_by_id(self, order_id: str):
        rows = self._rest("GET", "orders", params={"id": f"eq.{order_id}", "select": "*"})
        return rows[0] if rows else None

    def update_order_tracking(self, order_id: str, tracking_number: str, carrier: str, status: str = "SHIPPED"):
        r = requests.patch(
            f"{self.url}/rest/v1/orders",
            headers=self.headers,
            params={"id": f"eq.{order_id}"},
            json={"tracking_number": tracking_number, "tracking_carrier": carrier, "fulfillment_status": status},
            timeout=30,
        )
        r.raise_for_status()

    def get_orders_needing_tracking_push(self):
        try:
            params = {
                "select": "*",
                "tracking_number": "not.is.null",
                "fulfillment_status": "eq.SHIPPED",
            }
            return self._rest("GET", "orders", params=params)
        except Exception as e:
            logger.warning(f"get_orders_needing_tracking_push failed: {e}")
            return []

    def mark_tracking_pushed(self, order_id: str):
        try:
            r = requests.patch(
                f"{self.url}/rest/v1/orders",
                headers=self.headers,
                params={"id": f"eq.{order_id}"},
                json={"fulfillment_status": "TRACKING_PUSHED", "status": "TRACKING_PUSHED"},
                timeout=30,
            )
            r.raise_for_status()
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
            requests.delete(
                f"{self.url}/rest/v1/sales_trends",
                headers=self.headers,
                params=params,
                timeout=30,
            )
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
        r = requests.patch(
            f"{self.url}/rest/v1/sync_log",
            headers=self.headers,
            params={"id": f"eq.{log_id}"},
            json={"status": status, "details": details,
                  "error_message": errors, "completed_at": datetime.now(timezone.utc).isoformat()},
            timeout=30,
        )
        r.raise_for_status()

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
