"""
eBay API client.
Uses:
 - Trading API (XML) for listing inventory (GetMyeBaySelling + GetItem for variations)
 - Sell Fulfillment API (REST) for orders and tracking
Handles OAuth2 token refresh automatically, including rotating refresh tokens.

FIX (2025): eBay rotates refresh tokens on each use. The old code discarded
the new refresh token returned by eBay, causing every sync after the first
to fail with 400. Now we:
  1. On init: prefer the refresh token stored in Supabase over the env var
     (the Supabase copy is always the latest rotated token).
  2. After each token refresh: save the new refresh token back to Supabase.
"""
import os
import base64
import requests
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ebay.com"
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
TRADING_URL = "https://api.ebay.com/ws/api.dll"
NS = "urn:ebay:apis:eBLBaseComponents"

SCOPES = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
)

class EbayClient:
    def __init__(self, db=None):
        self.app_id = os.environ["EBAY_APP_ID"]
        self.cert_id = os.environ["EBAY_CERT_ID"]
        self.dev_id = os.environ.get("EBAY_DEV_ID", "cd505b42-9374-4d5c-86a5-1e31f075a2f1")
        self.db = db
        self._access_token = None
        self._token_expiry = None

        # ── ROTATING REFRESH TOKEN FIX ────────────────────────────────────────
        # Prefer the refresh token stored in Supabase (always the latest rotated
        # copy) over the static env var. The env var only needs to be valid once
        # ever — after the first successful refresh, Supabase takes over.
        if db:
            stored_refresh = db.get_setting("ebay_refresh_token")
            if stored_refresh:
                self.refresh_tok = stored_refresh
                logger.info("Loaded eBay refresh token from Supabase (latest rotated copy)")
            else:
                self.refresh_tok = os.environ["EBAY_REFRESH_TOKEN"]
                logger.info("Loaded eBay refresh token from environment variable")
        else:
            self.refresh_tok = os.environ["EBAY_REFRESH_TOKEN"]

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
        if not r.ok:
            logger.error(f"eBay token exchange failed {r.status_code}: {r.text}")
            r.raise_for_status()
        resp = r.json()

        self._access_token = resp["access_token"].strip()
        expires_in = int(resp.get("expires_in", 7200))
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 120)
        logger.info("eBay access token refreshed, valid for ~2h")

        if self.db:
            self.db.set_setting("ebay_access_token", self._access_token)
            self.db.set_setting("ebay_token_expiry", self._token_expiry.isoformat())

            # ── ROTATING REFRESH TOKEN FIX ────────────────────────────────────
            # eBay rotates refresh tokens: each use returns a NEW refresh token.
            # Save it to Supabase so the next run uses the fresh one.
            new_refresh = resp.get("refresh_token")
            if new_refresh and new_refresh != self.refresh_tok:
                self.refresh_tok = new_refresh
                self.db.set_setting("ebay_refresh_token", new_refresh)
                logger.info("eBay refresh token rotated — new token saved to Supabase")
            elif new_refresh:
                logger.info("eBay refresh token unchanged this cycle")

    def _get_access_token(self):
        if self.db and not self._access_token:
            tok = self.db.get_setting("ebay_access_token")
            exp_s = self.db.get_setting("ebay_token_expiry")
            if tok and exp_s:
                expiry = datetime.fromisoformat(exp_s)
                if expiry > datetime.now(timezone.utc):
                    self._access_token = tok.strip()
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

    def _get_item_details(self, item_id):
        """Call GetItem to get full details including variations for a listing."""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken></eBayAuthToken></RequesterCredentials>
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
  <IncludeItemSpecifics>true</IncludeItemSpecifics>
  <IncludeVariations>true</IncludeVariations>
</GetItemRequest>"""
        root = self._trading_call("GetItem", xml)
        return root.find(f'{{{NS}}}Item')

    def get_inventory_items(self):
        """Return all active eBay listings via Trading API.
        For variation listings, returns one row per variant.
        Uses parallel GetItem calls (20 workers) to avoid timeouts.
        Returns list of dicts with: sku, item_id, title, price, qty, image_url
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Step 1: collect basic info for all listings across all pages
        raw_items = []
        page = 1
        total_pages = 1

        while page <= total_pages:
            xml = f"""<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken></eBayAuthToken></RequesterCredentials>
  <ActiveList>
    <Include>true</Include>
    <Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber></Pagination>
  </ActiveList>
  <OutputSelector>ActiveList.ItemArray.Item.ItemID</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.Title</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.QuantityAvailable</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.BuyItNowPrice</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.PictureDetails</OutputSelector>
  <OutputSelector>ActiveList.ItemArray.Item.ListingType</OutputSelector>
  <OutputSelector>ActiveList.PaginationResult</OutputSelector>
</GetMyeBaySellingRequest>"""
            root = self._trading_call("GetMyeBaySelling", xml)

            tp_el = root.find(f'.//{{{NS}}}TotalNumberOfPages')
            if tp_el is not None and tp_el.text:
                total_pages = int(tp_el.text)

            items = root.findall(f'.//{{{NS}}}Item')
            for item in items:
                def g(tag, _item=item):
                    el = _item.find(f'{{{NS}}}{tag}')
                    return el.text if el is not None else None
                item_id = g('ItemID')
                title = g('Title')
                price_el = item.find(f'{{{NS}}}BuyItNowPrice')
                parent_price = float(price_el.text) if price_el is not None else 0.0
                img_el = item.find(f'{{{NS}}}PictureDetails/{{{NS}}}GalleryURL')
                img = img_el.text if img_el is not None else None
                raw_items.append((item_id, title, parent_price, img))

            logger.info(f"eBay listings page {page}/{total_pages}: {len(items)} items fetched")
            page += 1

        logger.info(f"eBay: {len(raw_items)} listings found — fetching full details in parallel (20 workers)...")

        # Step 2: parallel GetItem for all listings to get variation data
        def fetch_full(item_id):
            try:
                return item_id, self._get_item_details(item_id)
            except Exception as e:
                logger.warning(f"GetItem failed for {item_id}: {e}")
                return item_id, None

        full_details = {}
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(fetch_full, iid): iid for iid, *_ in raw_items}
            for future in as_completed(futures):
                iid, detail = future.result()
                full_details[iid] = detail

        logger.info(f"eBay: parallel GetItem complete for {len(full_details)} listings")

        # Step 3: build final item list with variants expanded
        all_items = []
        for item_id, title, parent_price, img in raw_items:
            full_item = full_details.get(item_id)
            variations = []
            if full_item is not None:
                variations = full_item.findall(f'{{{NS}}}Variations/{{{NS}}}Variation')
                fi_price_el = full_item.find(f'{{{NS}}}BuyItNowPrice') or full_item.find(f'{{{NS}}}StartPrice')
                if fi_price_el is not None and not parent_price:
                    parent_price = float(fi_price_el.text)
                fi_img_el = full_item.find(f'{{{NS}}}PictureDetails/{{{NS}}}GalleryURL')
                if fi_img_el is not None and not img:
                    img = fi_img_el.text
                qty_el = full_item.find(f'{{{NS}}}QuantityAvailable') or full_item.find(f'{{{NS}}}Quantity')

            if variations:
                for var in variations:
                    var_sku_el = var.find(f'{{{NS}}}SKU')
                    var_sku = var_sku_el.text if var_sku_el is not None else None
                    var_qty_el = var.find(f'{{{NS}}}Quantity')
                    var_qty = int(var_qty_el.text) if var_qty_el is not None and var_qty_el.text else 0
                    var_price_el = var.find(f'{{{NS}}}StartPrice')
                    var_price = float(var_price_el.text) if var_price_el is not None else parent_price

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
                        "availability": {"shipToLocationAvailability": {"quantity": var_qty}},
                        "price": var_price,
                        "image_url": img,
                    })
            else:
                # No variations — single listing
                if full_item is not None:
                    qty_el = full_item.find(f'{{{NS}}}QuantityAvailable') or full_item.find(f'{{{NS}}}Quantity')
                    qty = int(qty_el.text) if qty_el is not None and qty_el.text else 0
                else:
                    qty = 0
                sku = f"EBAY-{item_id}"
                all_items.append({
                    "sku": sku,
                    "item_id": item_id,
                    "variation_sku": None,
                    "product": {"title": title},
                    "availability": {"shipToLocationAvailability": {"quantity": qty}},
                    "price": parent_price,
                    "image_url": img,
                })

        logger.info(f"eBay get_inventory_items: {len(all_items)} total items (including variants)")
        return all_items

    def get_inventory_item(self, sku: str):
        """Not used with Trading API flow — kept for compatibility."""
        return None

    def update_inventory_quantity(self, item_id: str, quantity: int, variation_sku: str = None):
        """Update stock quantity for a traditional eBay listing via ReviseInventoryStatus."""
        sku_tag = f"<SKU>{variation_sku}</SKU>" if variation_sku else ""
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken></eBayAuthToken></RequesterCredentials>
  <InventoryStatus>
    <ItemID>{item_id}</ItemID>
    {sku_tag}
    <Quantity>{max(0, quantity)}</Quantity>
  </InventoryStatus>
</ReviseInventoryStatusRequest>"""
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
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials><eBayAuthToken></eBayAuthToken></RequesterCredentials>
  <Item>
    <ItemID>{item_id}</ItemID>
    <StartPrice>{price:.2f}</StartPrice>
  </Item>
</ReviseItemRequest>"""
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
        """Return orders from Sell Fulfillment API."""
        orders, offset, limit = [], 0, 50
        while True:
            params = {"limit": limit, "offset": offset}
            if created_after:
                params["filter"] = f"creationdate:[{created_after}..]"
            r = requests.get(
                f"{BASE_URL}/sell/fulfillment/v1/order",
                headers=self._rest_headers(), params=params, timeout=30,
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
