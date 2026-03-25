"""
eBay REST API client.
Uses Sell Inventory API and Sell Fulfillment API.
Handles OAuth2 token refresh automatically.
"""
import os
import base64
import requests
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

BASE_URL     = "https://api.ebay.com"
TOKEN_URL    = "https://api.ebay.com/identity/v1/oauth2/token"
SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
)


class EbayClient:
    def __init__(self, db=None):
        self.app_id      = os.environ["EBAY_APP_ID"]
        self.cert_id     = os.environ["EBAY_CERT_ID"]
        self.refresh_tok = os.environ["EBAY_REFRESH_TOKEN"]
        self.db = db  # optional DB reference to cache access token
        self._access_token = None
        self._token_expiry = None

    # ─── OAuth ──────────────────────────────────────────────────────────────────

    def _basic_auth(self):
        creds = base64.b64encode(f"{self.app_id}:{self.cert_id}".encode()).decode()
        return f"Basic {creds}"

    def _fetch_new_access_token(self):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": self._basic_auth(),
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_tok,
            "scope": SCOPES,
        }
        r = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
        r.raise_for_status()
        resp = r.json()
        self._access_token = resp["access_token"]
        expires_in = int(resp.get("expires_in", 7200))
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 120)
        logger.info("eBay access token refreshed, valid for ~2h")
        # Persist expiry in DB so other runs can reuse
        if self.db:
            self.db.set_setting("ebay_access_token", self._access_token)
            self.db.set_setting("ebay_token_expiry", self._token_expiry.isoformat())

    def _get_access_token(self):
        # Try cached from DB first
        if self.db and not self._access_token:
            tok   = self.db.get_setting("ebay_access_token")
            exp_s = self.db.get_setting("ebay_token_expiry")
            if tok and exp_s:
                expiry = datetime.fromisoformat(exp_s)
                if expiry > datetime.now(timezone.utc):
                    self._access_token = tok
                    self._token_expiry = expiry
        # Refresh if missing or near expiry
        if not self._access_token or (self._token_expiry and datetime.now(timezone.utc) >= self._token_expiry):
            self._fetch_new_access_token()
        return self._access_token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ─── Inventory ──────────────────────────────────────────────────────────────

    def get_inventory_items(self):
        """Return all inventory items (all pages)."""
        items, offset, limit = [], 0, 100
        while True:
            r = requests.get(
                f"{BASE_URL}/sell/inventory/v1/inventory_item",
                headers=self._headers(),
                params={"limit": limit, "offset": offset},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("inventoryItems", []))
            total = data.get("total", 0)
            offset += limit
            if offset >= total:
                break
        return items

    def get_inventory_item(self, sku: str):
        """Get a single inventory item by SKU."""
        r = requests.get(
            f"{BASE_URL}/sell/inventory/v1/inventory_item/{sku}",
            headers=self._headers(), timeout=30,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def update_inventory_quantity(self, sku: str, quantity: int):
        """Update only the availability quantity of an existing inventory item."""
        existing = self.get_inventory_item(sku)
        if not existing:
            logger.warning(f"eBay SKU {sku} not found, skipping stock update")
            return
        # Merge new quantity into existing object
        existing.setdefault("availability", {})
        existing["availability"]["shipToLocationAvailability"] = {
            "quantity": max(0, quantity)
        }
        r = requests.put(
            f"{BASE_URL}/sell/inventory/v1/inventory_item/{sku}",
            headers=self._headers(), json=existing, timeout=30,
        )
        r.raise_for_status()
        logger.info(f"eBay stock updated SKU {sku} → {quantity}")

    # ─── Offers / Pricing ───────────────────────────────────────────────────────

    def get_offers_for_sku(self, sku: str):
        """Get all offers (listings) for a SKU."""
        r = requests.get(
            f"{BASE_URL}/sell/inventory/v1/offer",
            headers=self._headers(),
            params={"sku": sku},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("offers", [])

    def update_offer_price(self, offer_id: str, price: float):
        """Update the BIN price on a published offer."""
        r = requests.get(
            f"{BASE_URL}/sell/inventory/v1/offer/{offer_id}",
            headers=self._headers(), timeout=30,
        )
        r.raise_for_status()
        offer = r.json()
        offer.setdefault("pricingSummary", {})
        offer["pricingSummary"]["price"] = {"currency": "GBP", "value": f"{price:.2f}"}
        r2 = requests.put(
            f"{BASE_URL}/sell/inventory/v1/offer/{offer_id}",
            headers=self._headers(), json=offer, timeout=30,
        )
        r2.raise_for_status()
        logger.info(f"eBay offer {offer_id} price → £{price:.2f}")

    # ─── Orders ─────────────────────────────────────────────────────────────────

    def get_orders(self, created_after: str = None):
        """Return orders from Sell Fulfillment API.
        created_after: ISO8601 string e.g. '2024-01-15T10:00:00.000Z'
        """
        orders, offset, limit = [], 0, 50
        filter_str = None
        if created_after:
            filter_str = f"creationdate:[{created_after}..] ,orderfulfillmentstatus:{{NOT_STARTED|IN_PROGRESS}}"
        while True:
            params = {"limit": limit, "offset": offset}
            if filter_str:
                params["filter"] = filter_str
            r = requests.get(
                f"{BASE_URL}/sell/fulfillment/v1/order",
                headers=self._headers(), params=params, timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            orders.extend(data.get("orders", []))
            total = data.get("total", 0)
            offset += limit
            if offset >= total:
                break
        return orders

    # ─── Shipping Fulfillment ─────────────────────────────────────────────────

    def create_shipping_fulfillment(self, order_id: str, tracking_number: str, carrier: str, line_item_ids: list = None):
        """Create a shipping fulfillment with tracking for an eBay order."""
        # Get order to find line item IDs if not provided
        if not line_item_ids:
            r = requests.get(
                f"{BASE_URL}/sell/fulfillment/v1/order/{order_id}",
                headers=self._headers(), timeout=30,
            )
            r.raise_for_status()
            order_data = r.json()
            line_item_ids = [li["lineItemId"] for li in order_data.get("lineItems", [])]

        payload = {
            "lineItems": [{"lineItemId": lid, "quantity": 1} for lid in line_item_ids],
            "shippingCarrierCode": carrier,
            "trackingNumber": tracking_number,
        }
        r = requests.post(
            f"{BASE_URL}/sell/fulfillment/v1/order/{order_id}/shipping_fulfillment",
            headers=self._headers(), json=payload, timeout=30,
        )
        r.raise_for_status()
        logger.info(f"eBay tracking uploaded for order {order_id}: {carrier} {tracking_number}")
        return r.json()
