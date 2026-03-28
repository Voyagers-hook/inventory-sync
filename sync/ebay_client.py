"""
eBay API client.
Uses:
  - Trading API (XML) for listing inventory (GetMyeBaySelling)
  - Sell Fulfillment API (REST) for orders and tracking
Handles OAuth2 token refresh automatically.
"""
import os
import base64
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

BASE_URL  = "https://api.ebay.com"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TRADING_URL = "https://api.ebay.com/ws/api.dll"
NS = "urn:ebay:apis:eBLBaseComponents"

SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
)


class EbayClient:
    def __init__(self, db=None):
        self.app_id      = os.environ["EBAY_APP_ID"]
        self.cert_id     = os.environ["EBAY_CERT_ID"]
        self.dev_id      = os.environ.get("EBAY_DEV_ID", "cd505b42-9374-4d5c-86a5-1e31f075a2f1")
        self.refresh_tok = os.environ["EBAY_REFRESH_TOKEN"]
        self.db = db
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
        if self.db:
            self.db.set_setting("ebay_access_token", self._access_token)
            self.db.set_setting("ebay_token_expiry", self._token_expiry.isoformat())

    def _get_access_token(self):
        if self.db and not self._access_token:
            tok   = self.db.get_setting("ebay_access_token")
            exp_s = self.db.get_setting("ebay_token_expiry")
            if tok and exp_s:
                expiry = datetime.fromisoformat(exp_s)
                if expiry > datetime.now(timezone.utc):
                    self._access_token = tok
                    self._token_expiry = expiry
        if not self._access_token or (self._token_expiry and datetime.now(timezone.utc) >= self._token_expiry):
            self._fetch_new_access_token()
        return self._access_token

    def _rest_headers(self):
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _trading_headers(self, call_name):
        return {
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-APP-NAME": self.app_id,
            "X-EBAY-API-DEV-NAME": self.dev_id,
            "X-EBAY-API-CERT-NAME": self.cert_id,
            "X-EBAY-API-SITEID": "3",  # UK site
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "Content-Type": "text/xml",
            "X-EBAY-API-IAF-TOKEN": self._get_access_token(),
        }

    def _trading_call(self, call_name, xml_body):
        r = requests.post(TRADING_URL,
            headers=self._trading_headers(call_name),
            data=xml_body, timeout=60)
        r.raise_for_status()
        return ET.fromstring(r.text)

    # ─── Inventory (Trading API) ─────────────────────────────────────────────

    def get_inventory_items(self):
        """Return all active eBay listings via Trading API (GetMyeBaySelling).
        Returns list of dicts with: sku, item_id, title, price, qty, image_url
        """
        all_items = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>200</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>'''
            root = self._trading_call("GetMyeBaySelling", xml)

            # Get total pages from response
            tp_el = root.find(f'.//{{{NS}}}TotalNumberOfPages')
            if tp_el is not None and tp_el.text:
                total_pages = int(tp_el.text)

            items = root.findall(f'.//{{{NS}}}Item')
            for item in items:
                def g(tag):
                    el = item.find(f'{{{NS}}}{tag}')
                    return el.text if el is not None else None

                item_id = g('ItemID')
                title = g('Title')
                price_el = item.find(f'{{{NS}}}BuyItNowPrice')
                parent_price = float(price_el.text) if price_el is not None else 0.0
                img_el = item.find(f'{{{NS}}}PictureDetails/{{{NS}}}GalleryURL')
                img = img_el.text if img_el is not None else None

                # Check for variation listings
                variations = item.findall(f'{{{NS}}}Variations/{{{NS}}}Variation')
                if variations:
                    for var in variations:
                        var_sku_el = var.find(f'{{{NS}}}SKU')
                        var_sku = var_sku_el.text if var_sku_el is not None else None
                        var_qty_el = var.find(f'{{{NS}}}Quantity')
                        var_qty = int(var_qty_el.text) if var_qty_el is not None and var_qty_el.text else 0
                        var_price_el = var.find(f'{{{NS}}}StartPrice')
                        var_price = float(var_price_el.text) if var_price_el is not None else parent_price

                        # Build variant label from specifics (e.g. "7m" or "Large / Red")
                        attrs = []
                        for nvl in var.findall(f'{{{NS}}}VariationSpecifics/{{{NS}}}NameValueList'):
                            val_el = nvl.find(f'{{{NS}}}Value')
                            if val_el is not None and val_el.text:
                                attrs.append(val_el.text)
                        attr_str = ' / '.join(attrs) if attrs else (var_sku or 'Variant')

                        safe_var_sku = (var_sku or attr_str).replace(' ', '_').replace('/', '-')[:40]
                        sku = f"EBAY-{item_id}-{safe_var_sku}"

                        all_items.append({
                            "sku": sku,
                            "item_id": item_id,
                            "variation_sku": var_sku,
                            "product": {"title": f"{title} - {attr_str}"},
                            "availability": {
                                "shipToLocationAvailability": {"quantity": var_qty}
                            },
                            "price": var_price,
                            "image_url": img,
                        })
                else:
                    # Regular single listing
                    qty_str = g('QuantityAvailable') or g('Quantity')
                    qty = int(qty_str) if qty_str else 0
                    sku = f"EBAY-{item_id}"
                    all_items.append({
                        "sku": sku,
                        "item_id": item_id,
                        "variation_sku": None,
                        "product": {"title": title},
                        "availability": {
                            "shipToLocationAvailability": {"quantity": qty}
                        },
                        "price": parent_price,
                        "image_url": img,
                    })

            logger.info(f"eBay listings page {page}/{total_pages}: {len(items)} items")
            page += 1

        logger.info(f"eBay GetMyeBaySelling: {len(all_items)} active listings")
        return all_items

    def get_inventory_item(self, sku: str):
        """Not used with Trading API flow — kept for compatibility."""
        return None

    def update_inventory_quantity(self, item_id: str, quantity: int, variation_sku: str = None):
        """Update stock quantity for a traditional eBay listing via ReviseInventoryStatus."""
        sku_tag = f"<SKU>{variation_sku}</SKU>" if variation_sku else ""
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <InventoryStatus>
    <ItemID>{item_id}</ItemID>
    {sku_tag}
    <Quantity>{max(0, quantity)}</Quantity>
  </InventoryStatus>
</ReviseInventoryStatusRequest>'''
        root = self._trading_call("ReviseInventoryStatus", xml)
        ack = root.find(f'{{{NS}}}Ack')
        if ack is not None and ack.text in ('Success', 'Warning'):
            logger.info(f"eBay stock updated item {item_id} → {quantity}")
        else:
            errors = root.findall(f'.//{{{NS}}}Errors/{{{NS}}}LongMessage')
            msg = "; ".join(e.text for e in errors if e.text)
            raise Exception(f"ReviseInventoryStatus failed: {msg}")

    # ─── Offers / Pricing (Trading API) ─────────────────────────────────────

    def get_offers_for_sku(self, sku: str):
        """Not used with Trading API flow — pricing comes from GetMyeBaySelling."""
        return []

    def update_offer_price(self, item_id: str, price: float):
        """Update the BIN price on a traditional eBay listing via ReviseItem."""
        xml = f'''<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <Item>
    <ItemID>{item_id}</ItemID>
    <StartPrice>{price:.2f}</StartPrice>
  </Item>
</ReviseItemRequest>'''
        root = self._trading_call("ReviseItem", xml)
        ack = root.find(f'{{{NS}}}Ack')
        if ack is not None and ack.text in ('Success', 'Warning'):
            logger.info(f"eBay price updated item {item_id} → £{price:.2f}")
        else:
            errors = root.findall(f'.//{{{NS}}}Errors/{{{NS}}}LongMessage')
            msg = "; ".join(e.text for e in errors if e.text)
            raise Exception(f"ReviseItem failed: {msg}")

    # ─── Orders ─────────────────────────────────────────────────────────────────

    def get_orders(self, created_after: str = None):
        """Return orders from Sell Fulfillment API.
        created_after: ISO8601 string e.g. '2024-01-15T10:00:00.000Z'
        """
        orders, offset, limit = [], 0, 50
        while True:
            params = {"limit": limit, "offset": offset}
            if created_after:
                params["filter"] = f"creationdate:[{created_after}..]"
            r = requests.get(
                f"{BASE_URL}/sell/fulfillment/v1/order",
                headers=self._rest_headers(), params=params, timeout=30,
            )
            if not r.ok:
                print(f'eBay token error response: {r.text}')
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
        if not line_item_ids:
            r = requests.get(
                f"{BASE_URL}/sell/fulfillment/v1/order/{order_id}",
                headers=self._rest_headers(), timeout=30,
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
            headers=self._rest_headers(), json=payload, timeout=30,
        )
        r.raise_for_status()
        logger.info(f"eBay tracking uploaded for order {order_id}: {carrier} {tracking_number}")
        return r.json()
