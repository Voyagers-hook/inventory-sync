"""
Squarespace Commerce API client.
Handles products, inventory, orders and price updates.
"""
import os
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

    def _post(self, path, payload):
        r = requests.post(f"{BASE_URL}{path}", headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

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

    def set_variant_stock(self, variant_id: str, new_qty: int):
        """Set absolute stock level for a variant using an adjustment delta."""
        # Fetch current to calculate delta
        data = self._get("/commerce/inventory")
        current_qty = 0
        for item in data.get("inventory", []):
            if item.get("variantId") == variant_id:
                current_qty = item.get("quantity", 0)
                break
        delta = new_qty - current_qty
        if delta == 0:
            logger.info(f"SS stock unchanged for variant {variant_id}")
            return
        payload = {"adjustments": [{"variantId": variant_id, "quantityDelta": delta}]}
        self._post("/commerce/inventory/adjustments", payload)
        logger.info(f"SS stock adjusted variant {variant_id}: {current_qty} → {new_qty} (delta {delta:+d})")

    # ─── Orders ─────────────────────────────────────────────────────────────────

    def get_orders(self, modified_after: str = None):
        """Return all orders, optionally filtered by date range.
        Squarespace requires both modifiedAfter AND modifiedBefore together."""
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
        
        Squarespace requires line item IDs in the fulfillment request.
        We fetch the order first to get those IDs.
        """
        # Fetch order to get line item IDs
        order_r = requests.get(
            f"{BASE_URL}/commerce/orders/{order_id}",
            headers=self.headers, timeout=30,
        )
        order_r.raise_for_status()
        order_data = order_r.json()
        line_items = [
            {"lineItemId": item["id"], "quantity": item.get("quantity", 1)}
            for item in order_data.get("lineItems", [])
        ]
        if not line_items:
            logger.warning(f"No line items found for SS order {order_id}, skipping fulfillment")
            return
        payload = {
            "shouldSendNotification": True,
            "fulfillments": [{
                "lineItems": line_items,
                "shipment": {
                    "trackingNumber": tracking_number,
                    "carrierName": carrier,
                    "service": "Standard",
                }
            }]
        }
        r = requests.post(
            f"{BASE_URL}/commerce/orders/{order_id}/fulfillments",
            headers=self.headers, json=payload, timeout=30,
        )
        r.raise_for_status()
        logger.info(f"SS fulfillment created for order {order_id}: {carrier} {tracking_number}")
