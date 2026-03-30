"""
eBay client using Browse API (OAuth user token, sell.inventory + sell.fulfillment scopes).
Replaces Trading API (which requires api_scope not available to this app).
"""
import os
import urllib.request
import urllib.parse
import urllib.error
import base64
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

SELLER = "voyagershookfishingco"
MARKETPLACE = "EBAY-GB"

SCOPES = (
    "https://api.ebay.com/oauth/api_scope "
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
)


class EbayClient:
    def __init__(self, db):
        self.db = db
        self._access_token = None
        self._token_expiry = 0
        self._client_id = os.environ.get("EBAY_APP_ID", "").strip()
        self._client_secret = os.environ.get("EBAY_CERT_ID", "").strip()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _refresh_access_token(self):
        """Exchange the stored refresh token for a new access token.
        eBay rotates refresh tokens, so we always save the new one."""
        refresh_token = self.db.get_setting("ebay_refresh_token")
        if not refresh_token:
            refresh_token = os.environ.get("EBAY_REFRESH_TOKEN", "").strip()
        if not refresh_token:
            raise ValueError("No eBay refresh token in database or environment")
        refresh_token = refresh_token.strip()

        creds = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPES,
        }).encode()

        req = urllib.request.Request(
            "https://api.ebay.com/identity/v1/oauth2/token",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {creds}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"Token refresh failed {e.code}: {body}")

        new_access = resp["access_token"].strip()
        expires_in = int(resp.get("expires_in", 7200))
        new_refresh = resp.get("refresh_token", "").strip()

        self.db.set_setting("ebay_access_token", new_access)
        self.db.set_setting("ebay_token_expiry", str(int(time.time()) + expires_in - 300))
        if new_refresh:
            self.db.set_setting("ebay_refresh_token", new_refresh)
            logger.info("Saved rotated eBay refresh token")

        self._access_token = new_access
        self._token_expiry = int(time.time()) + expires_in - 300
        logger.info("eBay access token refreshed, expires in %ds", expires_in)
        return new_access

    def _get_access_token(self):
        """Return a valid access token, refreshing automatically if expired."""
        now = int(time.time())

        if self._access_token and now < self._token_expiry:
            return self._access_token

        stored = self.db.get_setting("ebay_access_token")
        expiry_str = self.db.get_setting("ebay_token_expiry")
        expiry = 0
        if expiry_str:
            expiry_str = expiry_str.strip()
            try:
                expiry = int(expiry_str)
            except ValueError:
                # Stored as ISO datetime string e.g. "2026-03-29T13:09:19.636903+00:00"
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(expiry_str)
                    expiry = int(dt.timestamp())
                except Exception:
                    expiry = 0

        if stored and now < expiry:
            self._access_token = stored.strip()
            self._token_expiry = expiry
            return self._access_token

        logger.info("eBay token expired or missing — auto-refreshing")
        return self._refresh_access_token()

    def _get(self, url, retry_auth=True):
        """Authenticated GET to eBay Browse API with automatic token refresh on 401."""
        token = self._get_access_token()
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry_auth:
                logger.info("401 received — force-refreshing token and retrying")
                self._access_token = None
                self._token_expiry = 0
                return self._get(url, retry_auth=False)
            body = e.read().decode()
            raise RuntimeError(f"eBay API {e.code}: {body[:300]}")

    # ------------------------------------------------------------------
    # Listing retrieval
    # ------------------------------------------------------------------

    def _get_inventory_api_items(self):
        """
        Try eBay Sell Inventory API to get ALL inventory items including zero-stock.
        Only works for managed listings. Returns [] gracefully if not available.
        """
        items = []
        offset = 0
        limit = 200
        try:
            while True:
                url = f"https://api.ebay.com/sell/inventory/v1/inventory_item?limit={limit}&offset={offset}"
                resp = self._get(url)
                page = resp.get("inventoryItems", [])
                if not page:
                    break
                items.extend(page)
                total = int(resp.get("total", 0))
                offset += limit
                if offset >= total:
                    break
            logger.info("Inventory API returned %d items", len(items))
        except Exception as e:
            logger.info("Inventory API not available (traditional listings): %s", e)
            return []
        return items

    def _get_all_summary_items(self):
        """Paginate through all seller listing summaries."""
        items = []
        offset = 0
        limit = 200

        while True:
            url = (
                f"https://api.ebay.com/buy/browse/v1/item_summary/search"
                f"?q=fishing&filter=sellers%3A%7B{SELLER}%7D&limit={limit}&offset={offset}"
            )
            try:
                resp = self._get(url)
            except Exception as e:
                logger.error("Browse search failed at offset %d: %s", offset, e)
                break

            page = resp.get("itemSummaries", [])
            if not page:
                break
            items.extend(page)

            total = int(resp.get("total", 0))
            offset += limit
            if offset >= total:
                break

        logger.info("Browse API returned %d listing entries", len(items))
        return items

    def _expand_item(self, raw_item, seen_groups, lock):
        """Expand a single item into variant entries. Returns list of dicts."""
        try:
            item_id = raw_item.get("itemId", "")
            detail = self._get(
                f"https://api.ebay.com/buy/browse/v1/item/{urllib.parse.quote(item_id)}"
            )
        except Exception as e:
            logger.warning("Could not fetch item %s: %s", raw_item.get("itemId"), e)
            return []

        group_info = detail.get("primaryItemGroup")
        if group_info:
            group_id = group_info.get("itemGroupId", "")
            with lock:
                if group_id in seen_groups:
                    return []
                seen_groups.add(group_id)
            try:
                group_data = self._get(
                    f"https://api.ebay.com/buy/browse/v1/item/get_items_by_item_group"
                    f"?item_group_id={group_id}"
                )
            except Exception as e:
                logger.warning("Could not fetch group %s: %s", group_id, e)
                return []

            entries = []
            # Determine which aspects actually vary between variants
            # (excludes brand, country, fishing type etc. that are the same on all variants)
            _all_items = group_data.get("items", [])
            _asp_maps = [{a["name"]: a["value"] for a in v.get("localizedAspects", [])} for v in _all_items]
            _all_names = {n for m in _asp_maps for n in m}
            _varying = {n for n in _all_names if len({m.get(n, "") for m in _asp_maps}) > 1}

            # If Browse API returned no aspects, fall back to Trading API GetItem
            # to get variation specifics (needed for stock push VariationSpecifics)
            _trading_aspects_map = {}  # maps index → aspects dict
            if not _varying and _all_items:
                try:
                    first_legacy = _all_items[0].get("legacyItemId", "")
                    if first_legacy:
                        xml_body = (
                            '<?xml version="1.0" encoding="utf-8"?>'
                            '<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">' +
                            f'<ItemID>{first_legacy}</ItemID>'
                            '<DetailLevel>ReturnAll</DetailLevel>'
                            '</GetItemRequest>'
                        )
                        resp_text = self._trading_api_call("GetItem", xml_body)
                        import re as _re
                        trading_vars = _re.findall(r'<Variation>(.*?)</Variation>', resp_text, _re.DOTALL)
                        for idx, tv in enumerate(trading_vars):
                            specs = dict(_re.findall(
                                r'<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value></NameValueList>', tv))
                            if specs:
                                _trading_aspects_map[idx] = specs
                        if _trading_aspects_map:
                            logger.info("Used Trading API for aspects on item %s", first_legacy)
                except Exception as _e:
                    logger.debug("Trading API aspects fallback failed: %s", _e)
            for i, v in enumerate(_all_items):
                avail = v.get("estimatedAvailabilities", [{}])[0]
                qty = avail.get("estimatedAvailableQuantity", 0) or 0
                _raw_asp = {a["name"]: a["value"] for a in v.get("localizedAspects", [])}
                if _varying:
                    aspects = {k: val for k, val in _raw_asp.items() if k in _varying}
                elif _trading_aspects_map.get(i):
                    aspects = _trading_aspects_map[i]
                else:
                    aspects = _raw_asp
                legacy_id = v.get("legacyItemId", "")
                sku = v.get("sku") or f"{legacy_id or group_id}-v{i}"
                entries.append({
                    "sku": sku,
                    "title": v.get("title", raw_item.get("title", "")),
                    "price": float((v.get("price") or {}).get("value", 0) or 0),
                    "quantity": int(qty),
                    "item_id": v.get("itemId", ""),
                    "legacy_item_id": legacy_id,
                    "group_id": group_id,
                    "aspects": aspects,
                    "is_variant": True,
                })
            return entries
        else:
            avail = detail.get("estimatedAvailabilities", [{}])[0]
            qty = avail.get("estimatedAvailableQuantity", 0) or 0
            aspects = {a["name"]: a["value"] for a in detail.get("localizedAspects", [])}
            legacy_id = detail.get("legacyItemId", raw_item.get("legacyItemId", ""))
            sku = detail.get("sku") or legacy_id or raw_item.get("itemId", "")
            return [{
                "sku": sku,
                "title": detail.get("title", raw_item.get("title", "")),
                "price": float((detail.get("price") or {}).get("value", 0) or 0),
                "quantity": int(qty),
                "item_id": raw_item.get("itemId", ""),
                "legacy_item_id": legacy_id,
                "group_id": None,
                "aspects": aspects,
                "is_variant": False,
            }]

    def get_inventory_items(self):
        """
        Return all active eBay listings, one entry per variant (or single item).
        Merges Browse API (in-stock) with Inventory API (zero-stock managed listings).
        Each entry: {sku, title, price, quantity, item_id, legacy_item_id, group_id, aspects, is_variant}
        """
        raw_items = self._get_all_summary_items()
        if not raw_items:
            logger.warning("No eBay listings found")
            return []

        results = []
        seen_groups = set()
        lock = threading.Lock()

        def process(raw):
            return self._expand_item(raw, seen_groups, lock)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process, item) for item in raw_items]
            for future in as_completed(futures):
                try:
                    entries = future.result()
                    if entries:
                        results.extend(entries)
                except Exception as e:
                    logger.warning("Item expansion error: %s", e)

        logger.info("Expanded to %d total variant/item entries from Browse API", len(results))

        # Supplement with Inventory API to catch zero-stock managed listings
        inv_items = self._get_inventory_api_items()
        if inv_items:
            browse_skus = {r["sku"] for r in results}
            for inv in inv_items:
                sku = inv.get("sku", "")
                if not sku or sku in browse_skus:
                    continue  # Already have this item in-stock from Browse API
                product = inv.get("product", {})
                offers = inv.get("offers", [])
                price = 0.0
                if offers:
                    price = float(offers[0].get("pricingSummary", {}).get("price", {}).get("value", 0) or 0)
                results.append({
                    "sku": sku,
                    "title": product.get("title", sku),
                    "price": price,
                    "quantity": 0,
                    "item_id": sku,
                    "legacy_item_id": "",
                    "group_id": None,
                    "aspects": {},
                    "is_variant": False,
                })
            logger.info("After Inventory API merge: %d total entries", len(results))

        return results

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def get_orders(self, created_after=None, days_back=30):
        """Fetch recent orders via Fulfillment API."""
        from datetime import datetime, timedelta, timezone

        if created_after:
            since = created_after
        else:
            since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )

        token = self._get_access_token()
        orders = []
        offset = 0
        limit = 200

        while True:
            url = (
                f"https://api.ebay.com/sell/fulfillment/v1/order"
                f"?filter=creationdate%3A%5B{since}..%5D&limit={limit}&offset={offset}"
            )
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    resp = json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    token = self._refresh_access_token()
                    continue
                logger.error("Orders API error: %s", e)
                break

            orders.extend(resp.get("orders", []))
            total = resp.get("total", 0)
            offset += limit
            if offset >= total:
                break

        logger.info("Fetched %d eBay orders since %s", len(orders), since)
        return orders

    # ------------------------------------------------------------------
    # Write operations (Trading API)
    # ------------------------------------------------------------------

    def _trading_api_call(self, call_name, xml_body):
        """POST an XML request to eBay Trading API using OAuth token."""
        token = self._get_access_token()
        data = xml_body.encode("utf-8")
        req = urllib.request.Request(
            "https://api.ebay.com/ws/api.dll",
            data=data,
            headers={
                "X-EBAY-API-SITEID": "3",
                "X-EBAY-API-CALL-NAME": call_name,
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-IAF-TOKEN": token,
                "Content-Type": "text/xml",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.info("Trading API 401 — refreshing token and retrying")
                self._access_token = None
                self._token_expiry = 0
                token = self._get_access_token()
                req.headers["X-EBAY-API-IAF-TOKEN"] = token
                # Retry once
                req2 = urllib.request.Request(
                    "https://api.ebay.com/ws/api.dll",
                    data=data,
                    headers={
                        "X-EBAY-API-SITEID": "3",
                        "X-EBAY-API-CALL-NAME": call_name,
                        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                        "X-EBAY-API-IAF-TOKEN": token,
                        "Content-Type": "text/xml",
                    },
                )
                with urllib.request.urlopen(req2, timeout=30) as r:
                    return r.read().decode("utf-8")
            body = e.read().decode()
            raise RuntimeError(f"Trading API {call_name} failed {e.code}: {body[:300]}")

    def update_offer_price(self, item_id, price):
        logger.warning(
            "update_offer_price: traditional seller account, skipping (%s)", item_id
        )

    def update_inventory_quantity(self, item_id, quantity, variation_sku=None):
        """Push stock update to eBay via Trading API ReviseInventoryStatus."""
        # Parse legacy item ID from Browse API format: v1|ITEMID|VARIATIONID
        if item_id and str(item_id).startswith("v1|"):
            parts = str(item_id).split("|")
            legacy_id = parts[1] if len(parts) > 1 else item_id
        else:
            legacy_id = str(item_id)

        if not legacy_id:
            logger.warning("update_inventory_quantity: no valid item ID")
            return

        # Parse variation aspects from JSON stored in platform_variant_id
        aspects = {}
        if variation_sku:
            try:
                if isinstance(variation_sku, dict):
                    aspects = variation_sku  # Already a dict (from Supabase JSON column)
                else:
                    aspects = json.loads(variation_sku)  # JSON string fallback
            except (json.JSONDecodeError, TypeError):
                pass  # Not JSON aspects, no variation specifics

        # Build VariationSpecifics XML fragment
        variation_xml = ""
        if aspects:
            nvl = "".join(
                f"<NameValueList><Name>{k}</Name><Value>{v}</Value></NameValueList>"
                for k, v in aspects.items()
            )
            variation_xml = f"<VariationSpecifics>{nvl}</VariationSpecifics>"

        xml_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<InventoryStatus>"
            f"<ItemID>{legacy_id}</ItemID>"
            f"<Quantity>{quantity}</Quantity>"
            f"{variation_xml}"
            "</InventoryStatus>"
            "</ReviseInventoryStatusRequest>"
        )

        resp_text = self._trading_api_call("ReviseInventoryStatus", xml_body)

        if "<Ack>Failure</Ack>" in resp_text:
            logger.error("ReviseInventoryStatus Failure for %s: %s", legacy_id, resp_text[:500])
            raise RuntimeError(f"ReviseInventoryStatus failed for {legacy_id}")
        elif "<Ack>Warning</Ack>" in resp_text:
            logger.warning("ReviseInventoryStatus Warning for %s: %s", legacy_id, resp_text[:300])
            logger.info("Stock pushed to eBay item %s: qty=%s (with warning)", legacy_id, quantity)
        else:
            logger.info("Stock pushed to eBay item %s: qty=%s", legacy_id, quantity)



