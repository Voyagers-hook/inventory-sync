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
            raise ValueError("No eBay refresh token in database")
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
            for i, v in enumerate(group_data.get("items", [])):
                avail = v.get("estimatedAvailabilities", [{}])[0]
                qty = avail.get("estimatedAvailableQuantity", 0) or 0
                aspects = {a["name"]: a["value"] for a in v.get("localizedAspects", [])}
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
    # Write operations (stubs for traditional seller)
    # ------------------------------------------------------------------

    def update_offer_price(self, item_id, price):
        logger.warning(
            "update_offer_price: traditional seller account, skipping (%s)", item_id
        )

    def update_inventory_quantity(self, item_id, quantity, variation_sku=None):
        logger.warning(
            "update_inventory_quantity: traditional seller account, skipping (%s)", item_id
        )

