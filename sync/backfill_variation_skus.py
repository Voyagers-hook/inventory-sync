"""
One-time backfill: fetch variation SKUs from eBay Trading API and store in platform_pricing.
Requires EBAY_REFRESH_TOKEN, EBAY_APP_ID, EBAY_CERT_ID environment variables.
"""
import os, sys, re, json, time, requests
from collections import defaultdict

SKEY = os.environ["SUPABASE_SERVICE_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
REFRESH_TOKEN = os.environ["EBAY_REFRESH_TOKEN"]
CLIENT_ID = os.environ["EBAY_APP_ID"]
CLIENT_SECRET = os.environ["EBAY_CERT_ID"]

HEADERS_SB = {"Authorization": f"Bearer {SKEY}", "apikey": SKEY, "Content-Type": "application/json"}

def get_access_token():
    r = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN,
              "scope": "https://api.ebay.com/oauth/api_scope/sell.inventory"},
        auth=(CLIENT_ID, CLIENT_SECRET))
    data = r.json()
    t = data.get("access_token")
    if not t:
        print(f"ERROR getting token: {data}")
        sys.exit(1)
    return t

def trading_get_item(token, item_id):
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
</GetItemRequest>'''
    headers = {
        "X-EBAY-API-SITEID": "3", "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-IAF-TOKEN": token, "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem"
    }
    r = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml.encode(), timeout=15)
    return r.text

# Get all eBay variant pricing rows (where platform_variant_id is null for is_variant items)
# We identify variant items by their SKU format: ITEMID-vN or ITEMID (with multiple rows for same item)
resp = requests.get(f"{SUPABASE_URL}/rest/v1/platform_pricing",
    params={"platform": "eq.ebay", "select": "id,product_id,platform_product_id,platform_variant_id"},
    headers=HEADERS_SB)
all_rows = resp.json()
print(f"Total eBay pricing rows: {len(all_rows)}")

# Group by parent item ID
by_item = defaultdict(list)
for row in all_rows:
    pp = row.get("platform_product_id", "")
    if "|" in pp:  # Browse API format: v1|ITEMID|VARIANTID
        parts = pp.split("|")
        item_id = parts[1] if len(parts) > 1 else ""
    else:
        item_id = pp
    if item_id:
        by_item[item_id].append(row)

# Only process items with multiple variants (same item_id appearing in multiple rows)
variant_items = {k: v for k, v in by_item.items() if len(v) > 1}
print(f"Items with multiple variants: {len(variant_items)}")
# Also include single rows where platform_variant_id might need to be set (product SKU ends in -vN)
for item_id, rows in by_item.items():
    if len(rows) == 1:
        pp = rows[0].get("platform_product_id", "")
        # If the last segment of the Browse API ID suggests a variation, include it
        if pp.startswith("v1|") and pp.count("|") == 2:
            parts = pp.split("|")
            if parts[2] and parts[2] != "0":  # Has a non-zero variation ID
                variant_items[item_id] = rows

print(f"Total items to check: {len(variant_items)}")

token = get_access_token()
print("Got access token")

updated = 0
cleared = 0
skipped = 0
errors = 0

for item_id, item_rows in sorted(variant_items.items()):
    try:
        resp_text = trading_get_item(token, item_id)
        
        if "<Ack>Failure</Ack>" in resp_text:
            print(f"  SKIP {item_id}: Trading API failure")
            skipped += len(item_rows)
            continue
        
        # Extract variations with SKUs and specifics
        variations = re.findall(r'<Variation>(.*?)</Variation>', resp_text, re.DOTALL)
        
        if not variations:
            # Single listing - clear any old JSON aspects
            for row in item_rows:
                if row.get("platform_variant_id") is not None:
                    r = requests.patch(f"{SUPABASE_URL}/rest/v1/platform_pricing",
                        params={"id": f"eq.{row['id']}"},
                        headers=HEADERS_SB,
                        data=json.dumps({"platform_variant_id": None}))
                    cleared += 1
            continue
        
        print(f"  Item {item_id}: {len(variations)} variations, {len(item_rows)} DB rows")
        
        # Build: list of {sku, aspects}
        var_list = []
        for tv in variations:
            sku_m = re.search(r'<SKU>(.*?)</SKU>', tv)
            specs = dict(re.findall(r'<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value></NameValueList>', tv))
            var_list.append({"sku": sku_m.group(1) if sku_m else None, "aspects": specs})
        
        # Match each DB row to a variation
        # DB rows have platform_product_id = v1|ITEMID|VARIANT_NUMERIC_ID
        # The Browse API variant IDs correspond to the order from the API
        # Also try matching by existing platform_variant_id (old JSON aspects format)
        for row in item_rows:
            pp = row.get("platform_product_id", "")
            parts = pp.split("|")
            variant_num_id = parts[2] if len(parts) > 2 else None
            current_pvi = row.get("platform_variant_id")
            
            matched_sku = None
            
            # Strategy 1: match by existing JSON aspects
            if current_pvi:
                try:
                    current_aspects = json.loads(current_pvi) if isinstance(current_pvi, str) else current_pvi
                    if isinstance(current_aspects, dict) and current_aspects:
                        for vd in var_list:
                            # Check if variation aspects contain all current_aspects key-value pairs
                            if all(vd["aspects"].get(k) == v for k, v in current_aspects.items()):
                                matched_sku = vd["sku"]
                                break
                except:
                    # current_pvi might already be a plain SKU string
                    for vd in var_list:
                        if vd["sku"] == current_pvi:
                            matched_sku = current_pvi
                            break
            
            # Strategy 2: if only one variation left unmatched, assign it
            # (for items with exact count match)
            
            if matched_sku:
                r = requests.patch(f"{SUPABASE_URL}/rest/v1/platform_pricing",
                    params={"id": f"eq.{row['id']}"},
                    headers=HEADERS_SB,
                    data=json.dumps({"platform_variant_id": matched_sku}))
                if r.status_code in (200, 204):
                    print(f"    Row {row['id'][:8]}: {current_pvi!r} -> '{matched_sku}'")
                    updated += 1
                else:
                    print(f"    ERROR {row['id'][:8]}: HTTP {r.status_code}")
                    errors += 1
            else:
                print(f"    Could not match row {row['id'][:8]} (pp={pp}, pvi={current_pvi!r})")
                skipped += 1
        
        time.sleep(0.2)
        
    except Exception as e:
        print(f"  ERROR {item_id}: {e}")
        errors += 1

print(f"\nBackfill complete: {updated} updated, {cleared} cleared, {skipped} skipped, {errors} errors")
