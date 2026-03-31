"""
eBay Competitor Price Scanner
Scans eBay for competitor prices on all eBay-listed products.
Stores results in the settings table as competitor_price_{product_id} keys.
"""

import os, json, time, urllib.request, urllib.parse, base64, sys
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://czoppjnkjxmduldxlbqh.supabase.co")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# eBay credentials
EBAY_CLIENT_ID = os.environ["EBAY_CLIENT_ID"]
EBAY_CLIENT_SECRET = os.environ["EBAY_CLIENT_SECRET"]

HEADERS = {
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "apikey": SUPABASE_KEY,
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

def supa_get(path):
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}", headers={
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
    })
    return json.loads(urllib.request.urlopen(req).read())

def supa_upsert(table, data):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=json.dumps(data).encode(),
        headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        method="POST"
    )
    urllib.request.urlopen(req)

def get_ebay_app_token():
    """Get eBay application token (client credentials grant) for Browse API"""
    creds = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope"
    }).encode()
    req = urllib.request.Request(
        "https://api.ebay.com/identity/v1/oauth2/token",
        data=data,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["access_token"]

def search_ebay(token, query, limit=5):
    """Search eBay Browse API for items matching query"""
    # Clean up query - remove very long titles, use key words
    words = query.split()
    if len(words) > 8:
        search_query = " ".join(words[:8])
    else:
        search_query = query
    
    encoded_q = urllib.parse.quote(search_query)
    url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?q={encoded_q}&limit={limit}&filter=deliveryCountry:GB"
    
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY-GB",
        "Content-Type": "application/json"
    })
    
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        return resp.get("itemSummaries", [])
    except urllib.error.HTTPError as e:
        print(f"  Search failed for '{search_query}': {e.code}")
        return []

def get_our_seller_id():
    return "voyagershookfishingco"

def scan_product(token, product_id, title, our_price, our_seller):
    """Scan eBay for competitor prices on a single product"""
    items = search_ebay(token, title)
    
    if not items:
        return {
            "status": "no_results",
            "cheapest_price": None,
            "cheapest_title": None,
            "cheapest_url": None,
            "cheapest_seller": None,
            "price_difference": None,
            "our_price": our_price,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }
    
    # Filter out our own listings
    competitor_items = []
    for item in items:
        seller = item.get("seller", {}).get("username", "").lower()
        if seller != our_seller.lower():
            price_str = item.get("price", {}).get("value")
            if price_str:
                competitor_items.append({
                    "price": float(price_str),
                    "title": item.get("title", ""),
                    "url": item.get("itemWebUrl", ""),
                    "seller": seller,
                    "condition": item.get("condition", ""),
                    "item_id": item.get("itemId", "")
                })
    
    if not competitor_items:
        return {
            "status": "no_competitors",
            "cheapest_price": None,
            "cheapest_title": None,
            "cheapest_url": None,
            "cheapest_seller": None,
            "price_difference": None,
            "our_price": our_price,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }
    
    # Find cheapest competitor
    cheapest = min(competitor_items, key=lambda x: x["price"])
    
    if our_price and our_price > 0:
        diff = round(our_price - cheapest["price"], 2)
        if diff > 0.5:
            status = "undercut"  # competitor is cheaper
        elif diff < -0.5:
            status = "cheapest"  # we're cheaper
        else:
            status = "close"  # within 50p
    else:
        diff = None
        status = "unknown"
    
    return {
        "status": status,
        "cheapest_price": cheapest["price"],
        "cheapest_title": cheapest["title"],
        "cheapest_url": cheapest["url"],
        "cheapest_seller": cheapest["seller"],
        "price_difference": diff,
        "our_price": our_price,
        "competitors_found": len(competitor_items),
        "checked_at": datetime.now(timezone.utc).isoformat()
    }

def scan_single_product(product_id):
    """Scan a single product (for on-demand checks from dashboard)"""
    print(f"On-demand scan for product {product_id}")
    
    # Get product details
    products = supa_get(f"products?id=eq.{urllib.parse.quote(product_id)}&select=id,name")
    if not products:
        print(f"Product {product_id} not found")
        return
    
    product = products[0]
    
    # Get our eBay price
    pricing = supa_get(f"platform_pricing?product_id=eq.{urllib.parse.quote(product_id)}&platform=eq.ebay&select=price")
    our_price = float(pricing[0]["price"]) if pricing else 0
    
    token = get_ebay_app_token()
    result = scan_product(token, product_id, product["name"], our_price, get_our_seller_id())
    
    # Save result
    supa_upsert("settings", {
        "key": f"competitor_price_{product_id}",
        "value": json.dumps(result)
    })
    
    print(f"  {product['name']}: {result['status']} (ours: £{our_price}, cheapest: £{result.get('cheapest_price', 'N/A')})")
    return result

def scan_all():
    """Full daily scan of all eBay products"""
    print(f"Starting full competitor price scan at {datetime.now(timezone.utc).isoformat()}")
    
    # Get all eBay products (SKU not starting with SQ)
    products = supa_get("products?sku=not.like.SQ*&select=id,name,sku")
    print(f"Found {len(products)} eBay products to scan")
    
    # Get all eBay pricing
    pricing = supa_get("platform_pricing?platform=eq.ebay&select=product_id,price")
    price_map = {p["product_id"]: float(p["price"]) for p in pricing}
    
    token = get_ebay_app_token()
    our_seller = get_our_seller_id()
    
    stats = {"cheapest": 0, "undercut": 0, "close": 0, "no_results": 0, "no_competitors": 0, "unknown": 0, "errors": 0}
    
    for i, product in enumerate(products):
        pid = product["id"]
        title = product["name"]
        our_price = price_map.get(pid, 0)
        
        try:
            result = scan_product(token, pid, title, our_price, our_seller)
            
            # Save result
            supa_upsert("settings", {
                "key": f"competitor_price_{pid}",
                "value": json.dumps(result)
            })
            
            status = result["status"]
            stats[status] = stats.get(status, 0) + 1
            
            symbol = {"cheapest": "🟢", "close": "🟡", "undercut": "🔴", "no_results": "⚪", "no_competitors": "⚪"}.get(status, "❓")
            print(f"  [{i+1}/{len(products)}] {symbol} {title[:50]}: {status} (ours: £{our_price}, cheapest: £{result.get('cheapest_price', 'N/A')})")
            
            # Rate limit - eBay Browse API allows 5000 calls/day
            # With ~300 products, we're well within limits
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  [{i+1}/{len(products)}] ❌ {title[:50]}: ERROR - {e}")
            stats["errors"] += 1
            time.sleep(1)
    
    # Save scan summary
    supa_upsert("settings", {
        "key": "competitor_scan_summary",
        "value": json.dumps({
            "last_scan": datetime.now(timezone.utc).isoformat(),
            "total_scanned": len(products),
            "stats": stats
        })
    })
    
    print(f"\nScan complete!")
    print(f"  🟢 Cheapest: {stats['cheapest']}")
    print(f"  🟡 Close: {stats['close']}")
    print(f"  🔴 Undercut: {stats['undercut']}")
    print(f"  ⚪ No results/competitors: {stats['no_results'] + stats['no_competitors']}")
    print(f"  ❌ Errors: {stats['errors']}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--product":
        # On-demand single product scan
        product_id = sys.argv[2]
        scan_single_product(product_id)
    else:
        # Full daily scan
        scan_all()
