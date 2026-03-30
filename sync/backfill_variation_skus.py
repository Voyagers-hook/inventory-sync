"""
Backfill variation SKUs: match each variant DB row to its Trading API variation SKU
by comparing product names to variation aspect values.
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
        params={"key": "eq.ebay_refresh_token", "select": "value"},
        headers=HEADERS_SB)
    rows = r.json()
    if rows and rows[0].get("value"):
        return rows[0]["value"].strip()
    return os.environ.get("EBAY_REFRESH_TOKEN", "").strip()

def get_access_token():
    refresh_token = get_db_refresh_token()
    r = requests.post("https://api.ebay.com/identity/v1/oauth2/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token,
              "scope": "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment"},
        auth=(CLIENT_ID, CLIENT_SECRET))
    data = r.json()
    t = data.get("access_token")
    if not t:
        print(f"ERROR getting token: {data}")
        sys.exit(1)
    # Save rotated token
    new_rt = data.get("refresh_token")
    if new_rt and new_rt != refresh_token:
        requests.patch(f"{SUPABASE_URL}/rest/v1/settings",
            params={"key": "eq.ebay_refresh_token"}, headers=HEADERS_SB, json={"value": new_rt})
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

# Load ALL eBay pricing rows
resp = requests.get(f"{SUPABASE_URL}/rest/v1/platform_pricing",
    params={"platform": "eq.ebay", "select": "id,product_id,platform_product_id,platform_variant_id"},
    headers=HEADERS_SB)
all_rows = resp.json()
print(f"Total eBay pricing rows: {len(all_rows)}")

# Load ALL products so we can look up names
resp2 = requests.get(f"{SUPABASE_URL}/rest/v1/products",
    params={"select": "id,name,sku"},
    headers=HEADERS_SB)
all_products = {p["id"]: p for p in resp2.json()}
print(f"Total products loaded: {len(all_products)}")

# Group pricing rows by parent item ID
by_item = defaultdict(list)
for row in all_rows:
    pp = row.get("platform_product_id", "")
    if "|" in pp:
        parts = pp.split("|")
        item_id = parts[1] if len(parts) > 1 else ""
    else:
        item_id = pp
    if item_id:
        by_item[item_id].append(row)

# Focus on items with multiple variants
variant_items = {k: v for k, v in by_item.items() if len(v) > 1}
print(f"Items with 2+ variants: {len(variant_items)}")

token = get_access_token()
print("Got access token")

updated = 0
skipped = 0
errors = 0

for item_id, item_rows in sorted(variant_items.items()):
    try:
        resp_text = trading_get_item(token, item_id)
        
        if "<Ack>Failure</Ack>" in resp_text:
            print(f"  SKIP {item_id}: Trading API failure")
            skipped += len(item_rows)
            continue
        
        variations = re.findall(r'<Variation>(.*?)</Variation>', resp_text, re.DOTALL)
        if not variations:
            skipped += len(item_rows)
            continue
        
        # Build list of {sku, aspects, all_values}
        var_list = []
        for tv in variations:
            sku_m = re.search(r'<SKU>(.*?)</SKU>', tv)
            specs = dict(re.findall(r'<NameValueList><Name>(.*?)</Name><Value>(.*?)</Value></NameValueList>', tv))
            sku = sku_m.group(1) if sku_m else None
            all_values = list(specs.values())  # ["XS"], ["Medium"], etc.
            var_list.append({"sku": sku, "aspects": specs, "values": all_values})
        
        print(f"  {item_id}: {len(variations)} vars, {len(item_rows)} rows")
        
        for row in item_rows:
            # Get the product name for this row
            prod = all_products.get(row["product_id"], {})
            prod_name = prod.get("name", "").lower()
            
            matched_sku = None
            best_match = None
            
            for vd in var_list:
                # Check if any of the variation's values appear at the end of the product name
                # Product names look like "Mikado Predator Snaps - XS" or "Item Name - Medium - Red"
                for val in vd["values"]:
                    val_lower = val.lower()
                    if prod_name.endswith(f"- {val_lower}") or prod_name.endswith(val_lower) or \
                       f"- {val_lower} -" in prod_name or f" {val_lower}" in prod_name.split(" - ")[-1:]:
                        best_match = vd
                        break
                if best_match:
                    break
                
                # Also try: product name contains the variation SKU
                if vd["sku"] and vd["sku"].lower() in prod_name:
                    best_match = vd
                    break
            
            if best_match:
                matched_sku = best_match["sku"]
            
            # Last resort: if only 1 variation and 1 row, just assign it
            if not matched_sku and len(item_rows) == 1 and len(var_list) == 1:
                matched_sku = var_list[0]["sku"]
            
            if matched_sku:
                r = requests.patch(f"{SUPABASE_URL}/rest/v1/platform_pricing",
                    params={"id": f"eq.{row['id']}"},
                    headers=HEADERS_SB,
                    data=json.dumps({"platform_variant_id": matched_sku}))
                if r.status_code in (200, 204):
                    print(f"    {prod.get('name','?')[:40]} -> SKU '{matched_sku}'")
                    updated += 1
                else:
                    print(f"    ERROR {row['id'][:8]}: HTTP {r.status_code}")
                    errors += 1
            else:
                print(f"    UNMATCHED: '{prod.get('name','?')[:40]}' vs {[v['values'] for v in var_list]}")
                skipped += 1
        
        time.sleep(0.2)
        
    except Exception as e:
        import traceback
        print(f"  ERROR {item_id}: {e}")
        traceback.print_exc()
        errors += 1

print(f"\nBackfill complete: {updated} updated, {skipped} skipped, {errors} errors")
