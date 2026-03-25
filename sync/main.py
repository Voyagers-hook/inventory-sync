"""
Entry point for the inventory sync.
Usage:
  python main.py --mode full       # Full hourly sync
  python main.py --mode quick      # Quick check (runs if manual_sync_requested flag set)
  python main.py --mode catalogue  # Sync product catalogue only (first-time setup)
  python main.py --mode import     # Alias for catalogue (full initial import from both platforms)
"""
import argparse
import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


def check_env():
    required = [
        "SQUARESPACE_API_KEY", "EBAY_APP_ID", "EBAY_CERT_ID",
        "EBAY_REFRESH_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


def run_migration():
    """Add any missing columns to the orders table. Safe to run repeatedly (IF NOT EXISTS)."""
    supabase_url = os.environ.get("SUPABASE_URL", "")
    db_password = os.environ.get("SUPABASE_DB_PASSWORD", "")
    if not db_password:
        logger.info("SUPABASE_DB_PASSWORD not set — skipping DB migration (columns may already exist)")
        return

    try:
        import psycopg2
        # Extract project ref from URL e.g. https://czoppjnkjxmduldxlbqh.supabase.co
        project_ref = supabase_url.replace("https://", "").split(".")[0]
        host = f"db.{project_ref}.supabase.co"
        conn = psycopg2.connect(
            host=host, port=5432, dbname="postgres",
            user="postgres", password=db_password, sslmode="require", connect_timeout=20,
        )
        cur = conn.cursor()
        migrations = [
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_pushed_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(200)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS tracking_carrier VARCHAR(100)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name VARCHAR(500)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_email VARCHAR(500)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_address_line1 TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_address_line2 TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_city VARCHAR(200)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_county VARCHAR(200)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_postcode VARCHAR(50)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_country VARCHAR(10)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS fulfillment_status VARCHAR(50)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_total NUMERIC(10,2)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS item_name TEXT",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_number VARCHAR(200)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS ordered_at TIMESTAMPTZ",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS unit_price NUMERIC(10,2)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS sku VARCHAR(200)",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status VARCHAR(50)",
            # sales_trends table
            "CREATE TABLE IF NOT EXISTS sales_trends (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), product_id UUID REFERENCES products(id) ON DELETE CASCADE, date DATE NOT NULL, platform VARCHAR(50) NOT NULL, units_sold INTEGER DEFAULT 0, revenue NUMERIC(10,2) DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT NOW())",
            # platform_pricing table
            "CREATE TABLE IF NOT EXISTS platform_pricing (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), product_id UUID REFERENCES products(id) ON DELETE CASCADE, platform VARCHAR(50) NOT NULL, price NUMERIC(10,2), currency VARCHAR(3) DEFAULT 'GBP', platform_product_id VARCHAR(200), platform_variant_id VARCHAR(200), last_synced_at TIMESTAMPTZ, updated_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE (product_id, platform))",
        ]
        for sql in migrations:
            try:
                cur.execute(sql)
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.debug(f"Migration skip: {str(e)[:80]}")
        cur.close()
        conn.close()
        logger.info("DB migration complete")
    except ImportError:
        logger.warning("psycopg2 not available — skipping migration")
    except Exception as e:
        logger.warning(f"DB migration failed (non-fatal): {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "quick", "catalogue", "import"], default="full")
    args = parser.parse_args()

    check_env()
    run_migration()

    from database import Database
    from squarespace_client import SquarespaceClient
    from ebay_client import EbayClient
    from sync_engine import SyncEngine

    db   = Database()
    ss   = SquarespaceClient()
    ebay = EbayClient(db=db)
    engine = SyncEngine(db=db, ss=ss, ebay=ebay)

    log_id = db.start_sync_log(args.mode)
    errors = None
    items  = 0

    try:
        if args.mode in ("catalogue", "import"):
            items = engine.sync_product_catalogue()
        elif args.mode == "full":
            items = engine.run_full_sync()
        elif args.mode == "quick":
            items = engine.run_quick_check()
        status = "completed"
    except Exception as e:
        logger.exception("Sync failed with unhandled exception")
        status = "failed"
        errors = str(e)

    db.finish_sync_log(log_id, status, items, errors)
    logger.info(f"Sync complete: {status}, {items} items processed")

    if status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
