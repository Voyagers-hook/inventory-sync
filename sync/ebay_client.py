"""
eBay client using Browse API (OAuth user token, sell.inventory + sell.fulfillment scopes).
Uses Browse API for reading, Trading API for stock/price writes.
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
    "https://api.ebay.com/oauth/api_scope"
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
    # Listing retrieval — Full catalogue (used only for initial import or Sync Now)
    # ------------------------------------------------------------------

    def _get_all_summary_items(self):
        """Get ALL active seller listings via Trading API GetMyeBaySelling.
        Uses the ActiveList section which returns every active listing regardless
        of end date — fixes the bug where GTC listings mid-renewal were skipped.
        Returns items in Browse API-compatible format for _expand_item.
        Only used for initial import or full catalogue refresh.
        """
        import re as _re

        items = []
        page_number = 1
        total_pages = 1

        while page_number <= total_pages:
            xml_body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
                '<ActiveList>'
                '<Include>true</Include>'
                '<IncludeVariations>true</IncludeVariations>'
                '<Pagination>'
                '<EntriesPerPage>200</EntriesPerPage>'
                f'<PageNumber>{page_number}</PageNumber>'
                '</Pagination>'
                '</ActiveList>'
                '</GetMyeBaySellingRequest>'
            )
            try:
                resp_text = self._trading_api_call("GetMyeBaySelling", xml_body)
            except Exception as e:
                logger.error("GetMyeBaySelling page %d failed: %s", page_number, e)
                break

            # Total pages is inside <ActiveList><PaginationResult>
            tp_match = _re.search(
                r'<ActiveList>.*?<TotalNumberOfPages>(\d+)</TotalNumberOfPages>',
                resp_text, _re.DOTALL
            )
            if tp_match:
                total_pages = int(tp_match.group(1))

            item_blocks = _re.findall(r'<Item>(.*?)</Item>', resp_text, _re.DOTALL)
            page_items = 0
            for block in item_blocks:
                item_id_m = _re.search(r'<ItemID>(\d+)</ItemID>', block)
                title_m = _re.search(r'<Title>(.*?)</Title>', block)
                if not item_id_m:
                    continue
                legacy_id = item_id_m.group(1)
                title = title_m.group(1) if title_m else ""
                browse_id = f"v1|{legacy_id}|0"
                items.append({
                    "itemId": browse_id,
                    "legacyItemId": legacy_id,
                    "title": title,
                })
                page_items += 1

            logger.info("GetMyeBaySelling page %d/%d: %d items (total so far: %d)",
                        page_number, total_pages, page_items, len(items))
            page_number += 1

        logger.info("GetMyeBaySelling returned %d total active listing entries", len(items))
        return items

    def get_new_listings(self, since_timestamp):
        """Get only listings NEWLY CREATED since since_timestamp using StartTimeFrom filter.
        Much faster than full catalogue — only returns listings listed after that timestamp.
        Used by incremental hourly sync.
        """
        import re as _re
        from datetime import datetime, timezone, timedelta

        items = []
        page_number = 1
        total_pages = 1

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        while page_number <= total_pages:
            xml_body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<GetSellerListRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
                f'<StartTimeFrom>{since_timestamp}</StartTimeFrom>'
                f'<StartTimeTo>{now_str}</StartTimeTo>'
                '<GranularityLevel>Coarse</GranularityLevel>'
                '<IncludeVariations>true</IncludeVariations>'
                '<Pagination>'
                '<EntriesPerPage>200</EntriesPerPage>'
                f'<PageNumber>{page_number}</PageNumber>'
                '</Pagination>'
                '</GetSellerListRequest>'
            )
            try:
                resp_text = self._trading_api_call("GetSellerList", xml_body)
            except Exception as e:
                logger.error("GetSellerList (new listings) page %d failed: %s", page_number, e)
                break

            tp_match = _re.search(r'<TotalNumberOfPages>(\d+)</TotalNumberOfPages>', resp_text)
            if tp_match:
                total_pages = int(tp_match.group(1))

            item_blocks = _re.findall(r'<Item>(.*?)</Item>', resp_text, _re.DOTALL)
            for block in item_blocks:
                item_id_m = _re.search(r'<ItemID>(\d+)</ItemID>', block)
                title_m = _re.search(r'<Title>(.*?)</Title>', block)
                if not item_id_m:
                    continue
                legacy_id = item_id_m.group(1)
                title = title_m.group(1) if title_m else ""
                browse_id = f"v1|{legacy_id}|0"
                items.append({
                    "itemId": browse_id,
                    "legacyItemId": legacy_id,
                    "title": title,
                })

            logger.info("GetSellerList (new) page %d/%d: %d items", page_number, total_pages, len(item_blocks))
            page_number += 1

        logger.info("GetSellerList new listings since %s: %d found", since_timestamp, len(items))
        return items

    def _expand_item_via_trading(self, raw_item, seen_groups, lock):
        """Fallback for items where Browse API returns 404 — use Trading API GetItem."""
        import re as _re
        legacy_id = raw_item.get("legacyItemId", "")
        if not legacy_id:
            return [{
                "sku": raw_item.get("itemId", ""),
                "title": raw_item.get("title", ""),
                "price": 0.0, "quantity": 0,
                "item_id": raw_item.get("itemId", ""),
                "legacy_item_id": "", "group_id": None, "aspects": {}, "is_variant": False,
            }]
        try:
            xml_body = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
                f'<ItemID>{legacy_id}</ItemID>'
                '<DetailLevel>ReturnAll</DetailLevel>'
                '<IncludeVariations>true</IncludeVariations>'
                '</GetItemRequest>'
            )
            resp_text = self._trading_api_call("GetItem", xml_body)
        except Exception as e:
            logger.warning("Trading API fallback also failed for %s: %s — using basic info", legacy_id, e)
            return [{
                "sku": legacy_id, "title": raw_item.get("title", ""),
                "price": 0.0, "quantity": 0,
                "item_id": raw_item.get("itemId", ""),
                "legacy_item_id": legacy_id, "group_id": None, "aspects": {}, "is_variant": False,
            }]

        title_m = _re.search(r'<Title>(.*?)</Title>', resp_text)
        title = title_m.group(1) if title_m else raw_item.get("title", "")
        sku_m = _re.search(r'<SKU>(.*?)</SKU>', resp_text)
        sku = sku_m.group(1) if sku_m else legacy_id
        price_m = _re.search(r'<CurrentPrice[^>]*>([\d.]+)</CurrentPrice>', resp_text)
        price = float(price_m.group(1)) if price_m else 0.0

        variations = _re.findall(r'<Variation>(.*?)</Variation>', resp_text, _re.DOTALL)
        if variations:
            group_id = f"legacy_{legacy_id}"
            with lock:
                if group_id in seen_groups:
                    return []
                seen_groups.add(group_id)
            entries = []
            for i, var in enumerate(variations):
                var_sku_m = _re.search(r'<SKU>(.*?)</SKU>', var)
                var_qty_m = _re.search(r'<Quantity>(\d+)</Quantity>', var)
                var_qty_sold_m = _re.search(r'<QuantitySold>(\d+)</QuantitySold>', var)
                var_price_m = _re.search(r'<StartPrice[^>]*>([\d.]+)</StartPrice>', var)
                var_sku = var_sku_m.group(1) if var_sku_m else f"{legacy_id}-v{i}"
                var_qty = max(0, (int(var_qty_m.group(1)) if var_qty_m else 0)
                              - (int(var_qty_sold_m.group(1)) if var_qty_sold_m else 0))
                var_price = float(var_price_m.group(1)) if var_price_m else price
                specs = dict(_re.findall(
                    r'<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value></NameValueList>', var))
                entries.append({
                    "sku": var_sku or f"{legacy_id}-v{i}", "title": title,
                    "price": var_price, "quantity": var_qty,
                    "item_id": f"v1|{legacy_id}|0", "legacy_item_id": legacy_id,
                    "group_id": group_id, "aspects": specs,
                    "is_variant": True, "variation_sku": var_sku,
                })
            return entries
        else:
            qty_m = _re.search(r'<Quantity>(\d+)</Quantity>', resp_text)
            qty_sold_m = _re.search(r'<QuantitySold>(\d+)</QuantitySold>', resp_text)
            qty = max(0, (int(qty_m.group(1)) if qty_m else 0)
                      - (int(qty_sold_m.group(1)) if qty_sold_m else 0))
            return [{
                "sku": sku or legacy_id, "title": title,
                "price": price, "quantity": qty,
                "item_id": raw_item.get("itemId", ""),
                "legacy_item_id": legacy_id, "group_id": None, "aspects": {}, "is_variant": False,
            }]

    def _expand_item(self, raw_item, seen_groups, lock):
        """Expand a single item into variant entries. Returns list of dicts."""
        try:
            item_id = raw_item.get("itemId", "")
            detail = self._get(
                f"https://api.ebay.com/buy/browse/v1/item/{urllib.parse.quote(item_id)}"
            )
        except Exception as e:
            logger.warning("Could not fetch item %s via Browse API — falling back to Trading API (%s)", raw_item.get("itemId"), e)
            return self._expand_item_via_trading(raw_item, seen_groups, lock)

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
            _all_items = group_data.get("items", [])
            _asp_maps = [{a["name"]: a["value"] for a in v.get("localizedAspects", [])} for v in _all_items]
            _all_names = {n for m in _asp_maps for n in m}
            _varying = {n for n in _all_names if len({m.get(n, "") for m in _asp_maps}) > 1}

            _trading_aspects_map = {}
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
                            sku_match = _re.search(r'<SKU>(.*?)</SKU>', tv)
                            if specs or sku_match:
                                _trading_aspects_map[idx] = specs
                                if sku_match:
                                    _trading_aspects_map[idx]["_sku"] = sku_match.group(1)
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
                trading_sku = _trading_aspects_map.get(i, {}).get("_sku") if _trading_aspects_map else None
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
                    "variation_sku": trading_sku,
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
        Used only for initial import or full catalogue refresh (Sync Now).
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

        logger.info("Expanded to %d total variant/item entries", len(results))
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

    def update_offer_price(self, item_id, price, variation_sku=None):
        """Push price update to eBay via Trading API ReviseItem."""
        if item_id and str(item_id).startswith("v1|"):
            parts = str(item_id).split("|")
            legacy_id = parts[1] if len(parts) > 1 else item_id
        else:
            legacy_id = str(item_id)

        if not legacy_id:
            logger.warning("update_offer_price: no valid item ID")
            return

        variation_xml = ""
        item_price_xml = ""
        if variation_sku:
            sku_str = None
            try:
                parsed = json.loads(variation_sku) if isinstance(variation_sku, str) else variation_sku
                if isinstance(parsed, dict):
                    logger.warning("update_offer_price: platform_variant_id in old JSON format for %s, skipping", legacy_id)
                    return
                else:
                    sku_str = str(variation_sku).strip()
            except (json.JSONDecodeError, TypeError):
                sku_str = str(variation_sku).strip()

            if sku_str:
                variation_xml = (
                    "<Variations><Variation>"
                    f"<SKU>{sku_str}</SKU>"
                    f"<StartPrice currencyID='GBP'>{price:.2f}</StartPrice>"
                    "</Variation></Variations>"
                )

        if not variation_xml:
            item_price_xml = f"<StartPrice currencyID='GBP'>{price:.2f}</StartPrice>"

        xml_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<ReviseItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<Item>"
            f"<ItemID>{legacy_id}</ItemID>"
            f"{item_price_xml}"
            f"{variation_xml}"
            "</Item>"
            "</ReviseItemRequest>"
        )

        resp_text = self._trading_api_call("ReviseItem", xml_body)

        if "<Ack>Failure</Ack>" in resp_text:
            logger.error("ReviseItem price Failure for %s: %s", legacy_id, resp_text[:500])
            raise RuntimeError(f"ReviseItem price failed for {legacy_id}")
        elif "<Ack>Warning</Ack>" in resp_text:
            logger.warning("ReviseItem price Warning for %s: %s", legacy_id, resp_text[:300])
            logger.info("Price pushed to eBay item %s: £%.2f (with warning)", legacy_id, price)
        else:
            logger.info("Price pushed to eBay item %s: £%.2f", legacy_id, price)

    def update_inventory_quantity(self, item_id, quantity, variation_sku=None):
        """Push stock update to eBay via Trading API ReviseInventoryStatus."""
        if item_id and str(item_id).startswith("v1|"):
            parts = str(item_id).split("|")
            legacy_id = parts[1] if len(parts) > 1 else item_id
        else:
            legacy_id = str(item_id)

        if not legacy_id:
            logger.warning("update_inventory_quantity: no valid item ID")
            return

        variation_xml = ""
        if variation_sku:
            sku_str = None
            try:
                parsed = json.loads(variation_sku) if isinstance(variation_sku, str) else variation_sku
                if isinstance(parsed, dict):
                    logger.warning("platform_variant_id is in old JSON aspects format for item %s - "
                                   "needs re-sync to get variation SKU. Stock push skipped for this item.", legacy_id)
                    return
                else:
                    sku_str = str(variation_sku).strip()
            except (json.JSONDecodeError, TypeError):
                sku_str = str(variation_sku).strip()

            if sku_str:
                variation_xml = f"<SKU>{sku_str}</SKU>"

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
