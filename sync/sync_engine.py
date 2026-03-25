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
        """Full initial import: pull ALL products from both Squarespace and eBay."""
        logger.info("Syncing product catalogue from both platforms...")
        synced = 0

        ss_products = self.ss.get_products()
        ss_inventory_raw = self.ss.get_inventory()
        ss_stock_map = {}
        for inv_item in ss_inventory_raw:
            vid = inv_item.get("variantId")
            if vid:
                ss_stock_map[vid] = inv_item.get("quantity", 0)

        for prod in ss_products:
            for variant in prod.get("variants", []):
                sku = variant.get("sku") or f"SS-{prod['id']}-{variant['id']}"
                existing = self.db.get_product_by_sku(sku)
                if not existing:
                    rows = self.db.upsert_product({
                        "name": prod.get("name", "Unnamed"),
                        "sku": sku,
                        "description": prod.get("description", ""),
                    })
                    existing = rows[0] if rows else self.db.get_product_by_sku(sku)
                    synced += 1
                if existing:
                    product_id = existing["id"]
                    stock_qty = ss_stock_map.get(variant["id"], 0)
                    self.db.upsert_inventory({"product_id": product_id, "total_stock": stock_qty})
                    price_val = variant.get("pricing", {}).get("basePrice", {}).get("value", "0")
                    self.db.upsert_price({
                        "product_id": product_id,
                        "platform": "squarespace",
                        "price": float(price_val),
                        "currency": "GBP",
                        "platform_product_id": prod["id"],
                        "platform_variant_id": variant["id"],
                    })

        logger.info(f"Squarespace catalogue: {synced} new products, {len(ss_products)} total")

        ebay_items = self.ebay.get_inventory_items()
        ebay_synced = 0
        for item in ebay_items:
            sku = item.get("sku")
            if not sku:
                continue
            existing = self.db.get_product_by_sku(sku)
            if not existing:
                name = item.get("product", {}).get("title", "Unnamed")
                rows = self.db.upsert_product({
                    "name": name,
                    "sku": sku,
                    "description": item.get("product", {}).get("description", ""),
                })
                existing = rows[0] if rows else self.db.get_product_by_sku(sku)
                ebay_synced += 1
            if existing:
                product_id = existing["id"]
                stock_qty = item.get("availability", {}).get(
                    "shipToLocationAvailability", {}).get("quantity", 0)
                self.db.upsert_inventory({"product_id": product_id, "total_stock": stock_qty})
                ebay_item_id = item.get("item_id", sku)
                price_val = item.get("price", 0.0)
                self.db.upsert_price({
                    "product_id": product_id,
                    "platform": "ebay",
                    "price": float(price_val),
                    "currency": "GBP",
                    "platform_product_id": ebay_item_id,
                })

        logger.info(f"eBay catalogue: {ebay_synced} new products, {len(ebay_items)} total")
        total_synced = synced + ebay_synced
        logger.info(f"Catalogue sync complete: {total_synced} new products added")
        return total_synced

    # ─── Order Processing ────────────────────────────────────────────────────

    def process_squarespace_orders(self, since: str = None):
        """Process new SS orders → deduct stock → push updated qty to eBay."""
        logger.info(f"Processing SS orders since {since}")
        orders = self.ss.get_orders(modified_after=since)
        processed = 0
        for order in orders:
            order_id = order.get("id")
            status = order.get("fulfillmentStatus", "PENDING")

            # Update status for already-imported orders
            if self.db.order_exists("squarespace", order_id):
                self.db.update_order_status(order_id, status)
                continue

            if status not in ("PENDING", "FULFILLED"):
                continue

            billing = order.get("billingAddress", {})
            shipping = order.get("shippingAddress", {}) or billing
            customer_name = f"{shipping.get('firstName', '')} {shipping.get('lastName', '')}".strip()
            customer_email = order.get("customerEmail", "")

            for line in order.get("lineItems", []):
                variant_id = line.get("variantId")
                qty_sold   = int(line.get("quantity", 1))
                sku        = line.get("sku") or f"SS-{line.get('productId')}-{variant_id}"
                price      = float(line.get("unitPricePaid", {}).get("value", 0))
                product    = self.db.get_product_by_sku(sku)
                if not product:
                    logger.warning(f"SKU {sku} not in DB, skipping")
                    continue
                self.db.insert_order({
                    "platform": "squarespace",
                    "platform_order_id": order_id,
                    "product_id": product["id"],
                    "sku": sku,
                    "quantity": qty_sold,
                    "unit_price": price,
                    "currency": "GBP",
                    "status": status,
                    "ordered_at": order.get("createdOn"),
                    "customer_name": customer_name,
                    "customer_email": customer_email,
                    "shipping_address_line1": shipping.get("address1", ""),
                    "shipping_address_line2": shipping.get("address2", ""),
                    "shipping_city": shipping.get("city", ""),
                    "shipping_county": shipping.get("state", ""),
                    "shipping_postcode": shipping.get("postalCode", ""),
                    "shipping_country": shipping.get("countryCode", ""),
                    "fulfillment_status": status,
                    "order_total": float(order.get("grandTotal", {}).get("value", 0)),
                    "item_name": line.get("productName", ""),
                    "order_number": order.get("orderNumber", ""),
                })
                inv_rows = self.db.get_inventory(product["id"])
                if inv_rows:
                    inv = inv_rows[0]
                    new_stock = max(0, inv["total_stock"] - qty_sold)
                    self.db.upsert_inventory({"id": inv["id"], "product_id": product["id"], "total_stock": new_stock})
                    ebay_pricing = self.db.get_platform_pricing_for_product(product["id"], "ebay")
                    if ebay_pricing:
                        ebay_sku = ebay_pricing[0].get("platform_product_id")
                        if ebay_sku:
                            try:
                                self.ebay.update_inventory_quantity(ebay_sku, new_stock)
                            except Exception as e:
                                logger.error(f"eBay stock update failed for {sku}: {e}")
            processed += 1
        logger.info(f"SS orders processed: {processed}")
        return processed

    def process_ebay_orders(self, since: str = None):
        """Process new eBay orders + reconcile statuses for last 90 days.
        Always looks back 90 days so fulfilled/cancelled orders get their status updated.
        """
        # Always look back 90 days for full status reconciliation
        reconcile_since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(f"Processing eBay orders (new since {since or 'start'}, reconciling last 90 days)")
        orders = self.ebay.get_orders(created_after=reconcile_since)
        processed = 0

        # ── Pass 1: Reconcile statuses of all already-imported orders ──
        for order in orders:
            order_id = order.get("orderId")
            if not self.db.order_exists("ebay", order_id):
                continue
            cancel_state = order.get("cancelStatus", {}).get("cancelState", "NONE_REQUESTED")
            if cancel_state == "CANCELLED":
                self.db.update_order_status(order_id, "CANCELLED")
                logger.info(f"Reconciled eBay order {order_id} → CANCELLED")
            else:
                ebay_status = order.get("orderFulfillmentStatus", "NOT_STARTED")
                self.db.update_order_status(order_id, ebay_status)

        # ── Pass 2: Import any new orders not yet in DB ──
        for order in orders:
            order_id = order.get("orderId")
            cancel_state = order.get("cancelStatus", {}).get("cancelState", "NONE_REQUESTED")
            if cancel_state == "CANCELLED":
                continue
            if self.db.order_exists("ebay", order_id):
                continue

            ship_to = order.get("fulfillmentStartInstructions", [{}])[0].get(
                "shippingStep", {}).get("shipTo", {})
            contact = ship_to.get("fullName", "")
            address = ship_to.get("contactAddress", {})
            buyer_info = order.get("buyer", {})

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
                    "sku": sku,
                    "quantity": qty_sold,
                    "unit_price": price,
                    "currency": "GBP",
                    "status": order.get("orderFulfillmentStatus", "NOT_STARTED"),
                    "ordered_at": order.get("creationDate"),
                    "customer_name": contact,
                    "customer_email": buyer_info.get("username", "") + "@ebay.com",
                    "shipping_address_line1": address.get("addressLine1", ""),
                    "shipping_address_line2": address.get("addressLine2", ""),
                    "shipping_city": address.get("city", ""),
                    "shipping_county": address.get("stateOrProvince", ""),
                    "shipping_postcode": address.get("postalCode", ""),
                    "shipping_country": address.get("countryCode", ""),
                    "fulfillment_status": order.get("orderFulfillmentStatus", "NOT_STARTED"),
                    "order_total": float(order.get("totalFeeBasisAmount", {}).get("value", 0)),
                    "item_name": line.get("title", ""),
                    "order_number": order.get("orderId", ""),
                })
                inv_rows = self.db.get_inventory(product["id"])
                if inv_rows:
                    inv = inv_rows[0]
                    new_stock = max(0, inv["total_stock"] - qty_sold)
                    self.db.upsert_inventory({"id": inv["id"], "product_id": product["id"], "total_stock": new_stock})
                    ss_pricing = self.db.get_platform_pricing_for_product(product["id"], "squarespace")
                    if ss_pricing:
                        ss_variant_id = ss_pricing[0].get("platform_variant_id")
                        if ss_variant_id:
                            try:
                                self.ss.set_variant_stock(ss_variant_id, new_stock)
                            except Exception as e:
                                logger.error(f"SS stock update failed for {sku}: {e}")
            processed += 1

        logger.info(f"eBay orders processed: {processed} new, statuses reconciled for all 90-day orders")
        return processed

    # ─── Price Sync ─────────────────────────────────────────────────────────

    def sync_pending_price_changes(self):
        pending = self.db.get_pending_price_changes()
        pushed = 0
        for row in pending:
            try:
                if row["platform"] == "squarespace":
                    pp_id = row.get("platform_product_id")
                    pv_id = row.get("platform_variant_id")
                    if pp_id and pv_id:
                        self.ss.update_variant_price(pp_id, pv_id, float(row["price"]))
                elif row["platform"] == "ebay":
                    ebay_item_id = row.get("platform_product_id")
                    if ebay_item_id:
                        self.ebay.update_offer_price(ebay_item_id, float(row["price"]))
                self.db.mark_price_synced(row["id"])
                pushed += 1
            except Exception as e:
                logger.error(f"Price sync failed for product {row['product_id']}: {e}")
        logger.info(f"Price changes pushed: {pushed}")
        return pushed

    # ─── Trend Snapshots ─────────────────────────────────────────────────────

    def update_daily_snapshots(self):
        """Tally today's sales per product/platform and upsert into sales_trends."""
        today = datetime.now(timezone.utc).date().isoformat()
        orders = self.db.get_orders(limit=500)
        tally = {}
        for o in orders:
            if not o.get("ordered_at"):
                continue
            if not o.get("product_id"):
                continue  # skip orders without a matched product
            sale_day = o["ordered_at"][:10]
            if sale_day != today:
                continue
            key = (o["product_id"], o["platform"])
            if key not in tally:
                tally[key] = {"units": 0, "revenue": 0.0}
            tally[key]["units"]   += o["quantity"]
            tally[key]["revenue"] += float(o.get("unit_price", 0)) * o["quantity"]
        for (product_id, platform), vals in tally.items():
            self.db.upsert_snapshot({
                "product_id": product_id,
                "date": today,
                "platform": platform,
                "units_sold": vals["units"],
                "revenue": round(vals["revenue"], 2),
            })
        logger.info(f"Daily snapshot updated: {len(tally)} product/platform entries")

    # ─── Full Sync ───────────────────────────────────────────────────────────

    def run_full_sync(self):
        total = 0
        product_count = self.db.count_products()
        if product_count == 0:
            logger.info("No products in DB — running initial catalogue import...")
            total += self.sync_product_catalogue()

        last = self.db.get_setting("last_full_sync")
        if last:
            since = last
        else:
            since = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")

        total += self.process_squarespace_orders(since)
        total += self.process_ebay_orders(since)
        total += self.sync_pending_price_changes()
        total += self.push_pending_tracking()  # Push any pending tracking on every sync
        self.update_daily_snapshots()
        self.db.set_setting("last_full_sync", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return total

    def push_pending_tracking(self) -> int:
        """Push tracking numbers from the dashboard to eBay/Squarespace."""
        orders = self.db.get_orders_needing_tracking_push()
        pushed = 0
        for order in orders:
            platform = (order.get("platform") or "").lower()
            platform_order_id = order.get("platform_order_id") or ""
            tracking_number = order.get("tracking_number") or ""
            carrier = order.get("tracking_carrier") or "Royal Mail"
            order_id = order.get("id")

            if not tracking_number or not platform_order_id:
                continue

            try:
                if "ebay" in platform:
                    self.ebay.create_shipping_fulfillment(
                        platform_order_id, tracking_number, carrier
                    )
                    logger.info(f"Pushed tracking to eBay order {platform_order_id}")
                elif "squarespace" in platform:
                    self.ss.update_order_fulfillment(
                        platform_order_id, tracking_number, carrier
                    )
                    logger.info(f"Pushed tracking to Squarespace order {platform_order_id}")
                self.db.mark_tracking_pushed(order_id)
                pushed += 1
            except Exception as e:
                logger.warning(f"Failed to push tracking for order {platform_order_id}: {e}")

        return pushed

    def run_quick_check(self):
        """Triggered on demand: push any pending tracking, then full sync if requested."""
        count = 0
        pushed = self.push_pending_tracking()
        if pushed:
            logger.info(f"Quick check: pushed {pushed} tracking number(s) to platforms")
            count += pushed

        if self.db.is_sync_requested():
            logger.info("Quick check: manual sync requested, running full sync...")
            self.db.clear_sync_request()
            count += self.run_full_sync()
        else:
            logger.info("Quick check: no manual sync requested")

        return count
