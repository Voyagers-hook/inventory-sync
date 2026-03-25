"""
Squarespace Commerce API client.
Handles products, inventory, orders and price updates.
"""
import os
import uuid
import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.squarespace.com/1.0"


class SquarespaceClient:
    def __init__(self):
        self.api_key = os.environ["SQUARESPACE_API_KEY"]
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "VoyagersInventorySync/1.0",
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        r = requests.get(f"{BASE_URL}{path}", headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload, extra_headers=None):
        h = {**self.headers, **(extra_headers or {})}
        r = requests.post(f"{BASE_URL}{path}", headers=h, json=payload, timeout=30)
        r.raise_for_status()
        return r

    # ─── Products ───────────────────────────────────────────────────────────────

    def get_products(self):
        """Return flat list of all products (all pages)."""
        products, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            data = self._get("/commerce/products", params)
            products.extend(data.get("products", []))
            pagination = data.get("pagination", {})
            if not pagination.get("hasNextPage"):
                break
            cursor = pagination.get("nextPageCursor")
        return products

    # ─── Inventory ──────────────────────────────────────────────────────────────

    def get_inventory(self):
        """Return inventory list (variantId → quantity) for all variants."""
        inventory, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            data = self._get("/commerce/inventory", params)
            inventory.extend(data.get("inventory", []))
            pagination = data.get("pagination", {})
            if not pagination.get("hasNextPage"):
                break
            cursor = pagination.get("nextPageCursor")
        return inventory

    def set_variant_stocks(self, variant_updates: list) -> int:
        """Set exact stock levels for one or more variants.

        variant_updates: list of {"variantId": str, "quantity": int}

        Uses the Squarespace Inventory Adjustments API with setFiniteOperations.
        Requires Idempotency-Key header. Max 50 variants per call.
        Returns number of variants updated.
        """
        if not variant_updates:
            return 0

        total = 0
        # Process in batches of 50 (SS API limit)
        for i in range(0, len(variant_updates), 50):
            batch = variant_updates[i:i+50]
            payload = {
                "setFiniteOperations": [
                    {"variantId": upd["variantId"], "quantity": max(0, int(upd["quantity"]))}
                    for upd in batch
                ]
            }
            idempotency_key = str(uuid.uuid4())
            r = self._post(
                "/commerce/inventory/adjustments",
                payload,
                extra_headers={"Idempotency-Key": idempotency_key}
            )
            # 204 = success, no body
            total += len(batch)
            logger.info(f"SS stock set for {len(batch)} variant(s) (batch {i//50 + 1})")

        return total

    def set_variant_stock(self, variant_id: str, new_qty: int) -> bool:
        """Set exact stock level for a single variant."""
        try:
            self.set_variant_stocks([{"variantId": variant_id, "quantity": new_qty}])
            logger.info(f"SS stock set: variant {variant_id} → {new_qty}")
            return True
        except Exception as e:
            logger.error(f"SS stock set failed for variant {variant_id}: {e}")
            return False

    # ─── Orders ─────────────────────────────────────────────────────────────────

    def get_orders(self, modified_after: str = None):
        """Return all orders, optionally filtered by date range."""
        from datetime import datetime, timezone
        orders, cursor = [], None
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            if modified_after:
                params["modifiedAfter"] = modified_after
                params["modifiedBefore"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = self._get("/commerce/orders", params)
            orders.extend(data.get("result", []))
            pagination = data.get("pagination", {})
            if not pagination.get("hasNextPage"):
                break
            cursor = pagination.get("nextPageCursor")
        return orders

    # ─── Pricing ────────────────────────────────────────────────────────────────

    def update_variant_price(self, product_id: str, variant_id: str, price: float):
        """Update the base price of a single variant."""
        payload = {
            "variants": [{
                "id": variant_id,
                "pricing": {
                    "basePrice": {"currency": "GBP", "value": f"{price:.2f}"},
                    "onSale": False
                }
            }]
        }
        self._post(f"/commerce/products/{product_id}", payload)
        logger.info(f"SS price updated product {product_id} variant {variant_id} → £{price:.2f}")

    # ─── Fulfillment / Tracking ────────────────────────────────────────────────

    def update_order_fulfillment(self, order_id: str, tracking_number: str, carrier: str):
        """Mark a Squarespace order as shipped with tracking info.

        Uses correct Squarespace Commerce API format:
        POST /commerce/orders/{id}/fulfillments with top-level "shipments" array.
        Required fields: shipDate, carrierName, service, trackingNumber.
        """
        payload = {
            "shouldSendNotification": True,
            "shipments": [{
                "shipDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "carrierName": carrier if carrier else "Other",
                "service": "Standard Delivery",
                "trackingNumber": tracking_number,
            }]
        }
        r = requests.post(
            f"{BASE_URL}/commerce/orders/{order_id}/fulfillments",
            headers=self.headers, json=payload, timeout=30,
        )
        r.raise_for_status()
        logger.info(f"SS fulfillment created for order {order_id}: {carrier} {tracking_number}")
