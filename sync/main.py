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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "quick", "catalogue", "import"], default="full")
    args = parser.parse_args()

    check_env()

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
