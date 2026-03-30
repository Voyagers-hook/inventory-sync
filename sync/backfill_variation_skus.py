"""
Backfill variation SKUs using:
1. Browse API getItem → get variant aspects for each row
2. Trading API GetItem → get variation SKUs  
3. Match aspects to get SKU → store in platform_variant_id
"""
import os, sys, re, json, time, requests
from collections import defaultdict

SKEY = os.environ["SUPABASE_SERVICE_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
CLIENT_ID = os.environ["EBAY_APP_ID"]
CLIENT_SECRET = os.environ["EBAY_CERT_ID"]

HEADERS_SB = {"Authorization": f"Bearer {SKEY}", "apikey": SKEY, "Content-Type": "application/json"}

def get_db_refresh_token():
    r = requests.get(f"{SUPABASE_URL}/rest/v1/settings",
        params={"key": "eq.ebay_refresh_token", "select": "value"}, headers=HEADERS_SB)
    rows = r.json()
    if rows and rows[0].get("value"):
        return rows[0]["value"].strip()
    return os.environ.get("EBAY_REFRESH_TOKEN", "").strip()

def get_user_token():
    refresh_token = get_db_refresh_token()
    r = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token,
              "scope": "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment"},
        auth=(CLIENT_ID, CLIENT_SECRET))
    data = r.json()
    t = data.get("access_token")
    if not t:
        print(f"ERROR getting user token: {data}")
        sys.exit(1)
    new_rt = data.get("refresh_token")
    if new_rt and new_rt != refresh_token:
        requests.patch(f"{SUPABASE_URL}/rest/v1/settings",
            params={"key": "eq.ebay_refresh_token"}, headers=HEADERS_SB, json={"value": new_rt})
    return t

def get_app_token():
    r = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        auth=(CLIENT_ID, CLIENT_SECRET))
    return r.json().get("access_token")

def trading_get_variations(user_token, item_id):
    """Get variations dict: aspects_tuple -> sku"""
    xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>{item_id}</ItemID>
</GetItemRequest>'''
    headers = {
        "X-EBAY-API-SITEID": "3", "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-IAF-TOKEN": user_token, "Content-Type": "text/xml",
        "X-EBAY-API-CALL-NAME": "GetItem"
    }
    r = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml.encode(), timeout=15)
    variations = re.findall(r'<Variation>(.*?)</Variation>', r.text, re.DOTALL)
    result = {}
    for tv in variations:
        sku_m = re.search(r'<SKU>(.*?)</SKU>', tv)
        specs = dict(re.findall(r'<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value></NameValueList>', tv))
        if sku_m and specs:
            # Key by sorted aspect items for matching
            key = tuple(sorted(specs.items()))
            result[key] = sku_m.group(1)
    return result

def browse_get_aspects(app_token, item_id):
    """Get aspects for a specific Browse API item ID (v1|ITEMID|VARIANTID)"""
    encoded = item_id.replace("|", "%7C")
    r = requests.get(f"https://api.ebay.com/buy/browse/v1/item/{encoded}",
        headers={"Authorization": f"Bearer {app_token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY-GB"},
        timeout=15)
    if r.status_code == 200:
        data = r.json()
        return {a["name"]: a["value"] for a in data.get("localizedAspects", [])}
    return None

# Load all eBay pricing rows  
resp = requests.get(f"{SUPABASE_URL}/rest/v1/platform_pricing",
    params={"platform": "eq.ebay", "select": "id,product_id,platform_product_id,platform_variant_id"},
    headers=HEADERS_SB)
all_rows = resp.json()
print(f"Total eBay pricing rows: {len(all_rows)}")

# Group by parent item ID - only rows with Browse API format (v1|...)
by_item = defaultdict(list)
for row in all_rows:
    pp = row.get("platform_product_id", "")
    if pp.startswith("v1|"):
        parts = pp.split("|")
        if len(parts) == 3 and parts[2]:  # Has variant ID
            by_item[parts[1]].append(row)

print(f"Items with variant IDs in Browse format: {len(by_item)}")

user_token = get_user_token()
app_token = get_app_token()
print(f"Tokens obtained: user={'OK' if user_token else 'FAIL'}, app={'OK' if app_token else 'FAIL'}")

updated = 0
skipped = 0
errors = 0

for item_id, item_rows in sorted(by_item.items()):
    try:
        # Get Trading API variation SKUs for this item
        variations_map = trading_get_variations(user_token, item_id)
        if not variations_map:
            # Single listing, skip
            skipped += len(item_rows)
            continue
        
        print(f"  {item_id}: {len(variations_map)} Trading vars, {len(item_rows)} DB rows")
        
        for row in item_rows:
            pp = row.get("platform_product_id", "")
            
            # Get Browse API aspects for this specific variant
            browse_aspects = browse_get_aspects(app_token, pp)
            if not browse_aspects:
                print(f"    Browse API failed for {pp}")
                skipped += 1
                continue
            
            # Match Browse aspects to Trading API variations
            # Try exact match first, then partial match
            matched_sku = None
            
            # Exact match: browse aspects subset matches trading aspects
            for trading_key, sku in variations_map.items():
                trading_aspects = dict(trading_key)
                # Check if all trading aspect keys match browse aspect values
                if all(browse_aspects.get(k) == v for k, v in trading_aspects.items()):
                    matched_sku = sku
                    break
            
            if not matched_sku:
                # Partial match: at least one aspect value matches
                for trading_key, sku in variations_map.items():
                    trading_aspects = dict(trading_key)
                    matches = sum(1 for k, v in trading_aspects.items() if browse_aspects.get(k) == v)
                    if matches == len(trading_aspects):  # All trading aspects match
                        matched_sku = sku
                        break
            
            if matched_sku:
                r = requests.patch(f"{SUPABASE_URL}/rest/v1/platform_pricing",
                    params={"id": f"eq.{row['id']}"},
                    headers=HEADERS_SB,
                    data=json.dumps({"platform_variant_id": matched_sku}))
                if r.status_code in (200, 204):
                    variant_desc = {k: v for k, v in list(browse_aspects.items())[:2]}
                    print(f"    {row['id'][:8]}: {variant_desc} -> '{matched_sku}'")
                    updated += 1
                else:
                    print(f"    DB ERROR {row['id'][:8]}: {r.status_code}")
                    errors += 1
            else:
                print(f"    UNMATCHED {row['id'][:8]}: browse={dict(list(browse_aspects.items())[:3])}")
                skipped += 1
            
            time.sleep(0.1)  # Browse API rate limit
        
        time.sleep(0.2)
        
    except Exception as e:
        import traceback
        print(f"  ERROR {item_id}: {e}")
        traceback.print_exc()
        errors += 1

print(f"\nBackfill complete: {updated} updated, {skipped} skipped, {errors} errors")
