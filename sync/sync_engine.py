"""
Core sync logic.
Handles order processing, stock propagation, price sync, and trend snapshots.
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class SyncEngine:
    def __init__(self, db, ss, ebay):
        self.db   = db
        self.ss   = ss
        self.ebay = ebay

    # ─── Product Catalogue Sync ──────────────────────────────────────────────

    def sync_product_catalogue(self):
        """Pull products from Squarespace and ensure they exist in our DB."""
        logger.info("Syncing product catalogue from Squarespace...")
        ss_products = self.ss.get_products()
        synced = 0
        for prod in ss_products:
            for variant in prod.get("variants", []):
                sku = variant.get("sku") or f"SS-{prod['id']}-{variant['id']}"
                existing = self.db.get_product_by_sku(sku)
                if not existing:
                    self.db.upsert_product({
                        "name": prod.get("name", "Unnamed"),
                        "sku": sku,
                        "squarespace_product_id": prod["id"],
                        "squarespace_variant_id": variant["id"],
                        "description": prod.get("description", ""),
                    })
                    synced += 1
        logger.info(f"Catalogue sync: {synced} new products added, {len(ss_products)} total SS products")
        return synced

    # ─── Order Processing ────────────────────────────────────────────────────

    def process_squarespace_orders(self, since: str = None):
        """Process new SS orders → deduct stock → push updated qty to eBay."""
        logger.info(f"Processing SS orders since {since}")
        orders = self.ss.get_orders(modified_after=since)
        processed = 0
        for order in orders:
            order_id = order.get("id")
            if self.db.order_exists("squarespace", order_id):
                continue
            if order.get("fulfillmentStatus") not in ("PENDING", "FULFILLED"):
                continue
            for line in order.get("lineItems", []):
                variant_id = line.get("variantId")
                qty_sold   = int(line.get("quantity", 1))
                sku        = line.get("sku") or f"SS-{line.get('productId')}-{variant_id}"
                price      = float(line.get("unitPricePaid", {}).get("value", 0))
                # Find product in our DB
                product = self.db.get_product_by_sku(sku)
                if not product:
                    logger.warning(f"SKU {sku} not in DB, skipping")
                    continue
                # Record sale
                self.db.insert_order({
                    "platform": "squarespace",
                    "platform_order_id": order_id,
                    "product_id": product["id"],
                    "quantity": qty_sold,
                    "sale_price": price,
                    "currency": "GBP",
                    "sale_date": order.get("createdOn"),
                })
                # Update DB inventory
                inv_rows = self.db.get_inventory(product["id"])
                if inv_rows:
                    inv = inv_rows[0]
                    new_stock = max(0, inv["total_stock"] - qty_sold)
                    self.db.upsert_inventory({
                        "id": inv["id"],
                        "product_id": product["id"],
                        "total_stock": new_stock,
                        "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    })
                    # Push to eBay
                    if product.get("ebay_inventory_item_key"):
                        try:
                            self.ebay.update_inventory_quantity(
                                product["ebay_inventory_item_key"], new_stock)
                        except Exception as e:
                            logger.error(f"eBay stock update failed for {sku}: {e}")
            processed += 1
        logger.info(f"SS orders processed: {processed}")
        return processed

    def process_ebay_orders(self, since: str = None):
        """Process new eBay orders → deduct stock → push updated qty to Squarespace."""
        logger.info(f"Processing eBay orders since {since}")
        orders = self.ebay.get_orders(created_after=since)
        processed = 0
        for order in orders:
            order_id = order.get("orderId")
            if self.db.order_exists("ebay", order_id):
                continue
            for line in order.get("lineItems", []):
                sku      = line.get("sku", "")
                qty_sold = int(line.get("quantity", 1))
                price    = float(line.get("lineItemCost", {}).get("value", 0))
                product  = self.db.get_product_by_sku(sku)
                if not product:
                    logger.warning(f"eBay SKU {sku} not in DB, skipping")
                    continue
                self.db.insert_order({
                    "platform": "ebay",
                    "platform_order_id": order_id,
                    "product_id": product["id"],
                    "quantity": qty_sold,
                    "sale_price": price,
                    "currency": "GBP",
                    "sale_date": order.get("creationDate"),
                })
                inv_rows = self.db.get_inventory(product["id"])
                if inv_rows:
                    inv = inv_rows[0]
                    new_stock = max(0, inv["total_stock"] - qty_sold)
                    self.db.upsert_inventory({
                        "id": inv["id"],
                        "product_id": product["id"],
                        "total_stock": new_stock,
                        "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    })
                    # Push to Squarespace
                    if product.get("squarespace_variant_id"):
                        try:
                            self.ss.set_variant_stock(
                                product["squarespace_variant_id"], new_stock)
                        except Exception as e:
                            logger.error(f"SS stock update failed for {sku}: {e}")
            processed += 1
        logger.info(f"eBay orders processed: {processed}")
        return processed

    # ─── Price Sync ─────────────────────────────────────────────────────────

    def sync_pending_price_changes(self):
        """Push any price changes (made in dashboard) to the actual platforms."""
        pending = self.db.get_pending_price_changes()
        pushed = 0
        for row in pending:
            prod = self.db.get_products()
            prod_map = {p["id"]: p for p in prod}
            product = prod_map.get(row["product_id"])
            if not product:
                continue
            try:
                if row["platform"] == "squarespace" and product.get("squarespace_product_id"):
                    self.ss.update_variant_price(
                        product["squarespace_product_id"],
                        product["squarespace_variant_id"],
                        float(row["price"]),
                    )
                elif row["platform"] == "ebay" and product.get("ebay_inventory_item_key"):
                    offers = self.ebay.get_offers_for_sku(product["ebay_inventory_item_key"])
                    for offer in offers:
                        self.ebay.update_offer_price(offer["offerId"], float(row["price"]))
                self.db.mark_price_synced(row["id"])
                pushed += 1
            except Exception as e:
                logger.error(f"Price sync failed for product {row['product_id']}: {e}")
        logger.info(f"Price changes pushed: {pushed}")
        return pushed

    # ─── Trend Snapshots ─────────────────────────────────────────────────────

    def update_daily_snapshots(self):
        """Tally today's sales per product/platform and upsert into daily_snapshots."""
        today = datetime.now(timezone.utc).date().isoformat()
        orders = self.db.get_orders(limit=500)
        tally = {}  # (product_id, platform) → {units, revenue}
        for o in orders:
            if not o.get("sale_date"):
                continue
            sale_day = o["sale_date"][:10]
            if sale_day != today:
                continue
            key = (o["product_id"], o["platform"])
            if key not in tally:
                tally[key] = {"units": 0, "revenue": 0.0}
            tally[key]["units"]   += o["quantity"]
            tally[key]["revenue"] += float(o.get("sale_price", 0)) * o["quantity"]
        for (product_id, platform), vals in tally.items():
            inv_rows = self.db.get_inventory(product_id)
            stock = inv_rows[0]["total_stock"] if inv_rows else 0
            self.db.upsert_snapshot({
                "product_id": product_id,
                "date": today,
                "platform": platform,
                "units_sold": vals["units"],
                "revenue": round(vals["revenue"], 2),
                "ending_stock": stock,
            })
        logger.info(f"Daily snapshot updated: {len(tally)} product/platform entries")

    # ─── Full Sync ───────────────────────────────────────────────────────────

    def run_full_sync(self):
        since = self.db.get_setting("last_full_sync") or (
            datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        total = 0
        total += self.process_squarespace_orders(since)
        total += self.process_ebay_orders(since)
        total += self.sync_pending_price_changes()
        self.update_daily_snapshots()
        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).isoformat())
        return total

    def run_quick_check(self):
        """Runs every 5 min: only processes if manual sync was requested."""
        if not self.db.is_sync_requested():
            logger.info("Quick check: no sync requested, exiting")
            return 0
        logger.info("Quick check: manual sync requested, running...")
        self.db.clear_sync_request()
        return self.run_full_sync()
