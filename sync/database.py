"""
Supabase (PostgreSQL) database client.
All DB interactions go through this module.
"""
import os
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

    def get_setting(self, key: str):
        rows = self._rest("GET", "settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: str):
        self._rest("POST", "settings", payload={"key": key, "value": str(value)},
                   headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    # ─── Products ────────────────────────────────────────────────────────────

    def get_products(self):
        return self._rest("GET", "products", params={"select": "*", "order": "name.asc"})

    def upsert_product(self, product: dict):
        return self._rest("POST", "products", payload=product,
                          headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    def get_product_by_sku(self, sku: str):
        rows = self._rest("GET", "products", params={"sku": f"eq.{sku}", "select": "*"})
        return rows[0] if rows else None

    def get_product_by_ss_id(self, ss_product_id: str):
        rows = self._rest("GET", "products",
                          params={"squarespace_product_id": f"eq.{ss_product_id}", "select": "*"})
        return rows[0] if rows else None

    # ─── Inventory ───────────────────────────────────────────────────────────

    def get_inventory(self, product_id: str = None):
        params = {"select": "*"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "inventory", params=params)

    def upsert_inventory(self, inv: dict):
        inv["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._rest("POST", "inventory", payload=inv,
                          headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    # ─── Pricing ─────────────────────────────────────────────────────────────

    def get_prices(self, product_id: str = None):
        params = {"select": "*"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "pricing", params=params)

    def upsert_price(self, price_row: dict):
        price_row["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._rest("POST", "pricing", payload=price_row,
                          headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    def get_pending_price_changes(self):
        """Return pricing rows that have been updated but not yet synced."""
        return self._rest("GET", "pricing",
                          params={"select": "*", "sync_pending": "eq.true"})

    def mark_price_synced(self, price_id: str):
        r = requests.patch(
            f"{self.url}/rest/v1/pricing",
            headers=self.headers,
            params={"id": f"eq.{price_id}"},
            json={"sync_pending": False},
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

    def get_orders(self, platform: str = None, limit: int = 200):
        params = {"select": "*", "order": "sale_date.desc", "limit": str(limit)}
        if platform:
            params["platform"] = f"eq.{platform}"
        return self._rest("GET", "orders", params=params)

    def get_order_by_id(self, order_id: str):
        """Get a single order by its database ID."""
        rows = self._rest("GET", "orders", params={"id": f"eq.{order_id}", "select": "*"})
        return rows[0] if rows else None

    def update_order_tracking(self, order_id: str, tracking_number: str, carrier: str, status: str = "SHIPPED"):
        """Update tracking info on an order."""
        r = requests.patch(
            f"{self.url}/rest/v1/orders",
            headers=self.headers,
            params={"id": f"eq.{order_id}"},
            json={"tracking_number": tracking_number, "tracking_carrier": carrier, "fulfillment_status": status},
            timeout=30,
        )
        r.raise_for_status()

    # ─── Daily Snapshots (trends) ────────────────────────────────────────────

    def upsert_snapshot(self, snap: dict):
        return self._rest("POST", "daily_snapshots", payload=snap,
                          headers_extra={"Prefer": "resolution=merge-duplicates,return=representation"})

    def get_snapshots(self, product_id: str = None, days: int = 30):
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        params = {"select": "*", "date": f"gte.{cutoff}", "order": "date.asc"}
        if product_id:
            params["product_id"] = f"eq.{product_id}"
        return self._rest("GET", "daily_snapshots", params=params)

    # ─── Sync Log ────────────────────────────────────────────────────────────

    def start_sync_log(self, sync_type: str) -> str:
        rows = self._rest("POST", "sync_log",
                          payload={"sync_type": sync_type, "status": "running",
                                   "started_at": datetime.now(timezone.utc).isoformat()})
        return rows[0]["id"]

    def finish_sync_log(self, log_id: str, status: str, items: int = 0, errors: str = None):
        r = requests.patch(
            f"{self.url}/rest/v1/sync_log",
            headers=self.headers,
            params={"id": f"eq.{log_id}"},
            json={"status": status, "items_synced": items,
                  "errors": errors, "completed_at": datetime.now(timezone.utc).isoformat()},
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
