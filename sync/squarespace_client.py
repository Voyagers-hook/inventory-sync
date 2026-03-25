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

    def get_all_inventory_map(self) -> dict:
        """Fetch ALL inventory pages and return {variantId: quantity} dict.
        Used for efficient bulk stock sync — call this once, then look up variants locally.
        """
        inv_map = {}
        cursor = None
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/commerce/inventory", params)
            for item in data.get("inventory", []):
                vid = item.get("variantId")
                if vid:
                    inv_map[vid] = item.get("quantity", 0)
            pagination = data.get("pagination", {})
            if not pagination.get("hasNextPage"):
                break
            cursor = pagination.get("nextPageCursor")
        logger.info(f"SS inventory map built: {len(inv_map)} variants")
        return inv_map

    def set_multiple_variant_stocks(self, updates: list, inventory_map: dict = None) -> int:
        """Batch update stock levels for multiple variants in a single API call.

        updates: list of {"variant_id": str, "new_qty": int}
        inventory_map: optional pre-fetched {variantId: qty} dict (fetched here if not provided)

        Returns number of variants actually adjusted.
        """
        if not updates:
            return 0
        if inventory_map is None:
            inventory_map = self.get_all_inventory_map()
        adjustments = []
        for upd in updates:
            vid = upd["variant_id"]
            new_qty = upd["new_qty"]
            current_qty = inventory_map.get(vid, 0)
            delta = new_qty - current_qty
            if delta != 0:
                adjustments.append({"variantId": vid, "quantityDelta": delta})
                logger.info(f"SS stock queued: variant {vid} {current_qty} → {new_qty} (delta {delta:+d})")
        if adjustments:
            payload = {"adjustments": adjustments}
            self._post("/commerce/inventory/adjustments", payload)
            logger.info(f"SS batch stock update sent: {len(adjustments)} variants adjusted")
        else:
            logger.info("SS batch stock update: all variants already at correct levels")
        return len(adjustments)

    def set_variant_stock(self, variant_id: str, new_qty: int, inventory_map: dict = None):
        """Set absolute stock level for a single variant.
        Pass inventory_map if you already have it (avoids re-fetching all inventory).
        """
        if inventory_map is None:
            inventory_map = self.get_all_inventory_map()
        current_qty = inventory_map.get(variant_id, 0)
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
        
        Uses the correct Squarespace Commerce API format:
        POST /commerce/orders/{id}/fulfillments with a top-level "shipments" array.
        Required fields: shipDate, carrierName, service, trackingNumber.
        """
        from datetime import datetime, timezone
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
