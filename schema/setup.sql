-- ============================================================
-- Voyagers Hook — Inventory Sync Database Schema (v2)
-- Supports: variants, channel_listings, merge/mapping, needs_sync
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Products (parent groups) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(500) NOT NULL,
    sku         VARCHAR(200),
    description TEXT,
    category    VARCHAR(200),
    image_url   TEXT,
    cost_price  NUMERIC(10,2),
    status      VARCHAR(50) DEFAULT 'active',
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Variants (individual SKUs under a product) ────────────────────────────
CREATE TABLE IF NOT EXISTS variants (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    internal_sku   VARCHAR(200),
    option1        VARCHAR(200),
    option2        VARCHAR(200),
    needs_sync     BOOLEAN DEFAULT FALSE,
    last_synced_at TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast SKU lookups (not UNIQUE — same SKU can exist on different platforms)
CREATE INDEX IF NOT EXISTS idx_variants_internal_sku ON variants(internal_sku);

-- ── Inventory (stock levels per variant) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id          UUID REFERENCES variants(id) ON DELETE CASCADE,
    product_id          UUID REFERENCES products(id) ON DELETE CASCADE,
    total_stock         INTEGER NOT NULL DEFAULT 0,
    reserved_stock      INTEGER DEFAULT 0,
    low_stock_threshold INTEGER DEFAULT 5,
    location            VARCHAR(200),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (variant_id)
);

-- ── Channel Listings (per-platform links for each variant) ─────────────────
CREATE TABLE IF NOT EXISTS channel_listings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_id          UUID NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    channel             VARCHAR(50) NOT NULL CHECK (channel IN ('ebay', 'squarespace')),
    channel_sku         VARCHAR(200),
    channel_price       NUMERIC(10,2),
    channel_product_id  VARCHAR(500),
    channel_variant_id  VARCHAR(500),
    last_synced_at      TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (variant_id, channel)
);

-- ── Orders ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform              VARCHAR(50) NOT NULL,
    platform_order_id     VARCHAR(200),
    product_id            UUID REFERENCES products(id) ON DELETE SET NULL,
    sku                   VARCHAR(200),
    quantity              INTEGER NOT NULL DEFAULT 1,
    unit_price            NUMERIC(10,2),
    total_price           NUMERIC(10,2),
    currency              VARCHAR(3) DEFAULT 'GBP',
    status                VARCHAR(100),
    ordered_at            TIMESTAMPTZ,
    synced_at             TIMESTAMPTZ DEFAULT NOW(),
    item_name             TEXT,
    order_number          VARCHAR(200),
    customer_name         VARCHAR(500),
    customer_email        VARCHAR(500),
    shipping_address_line1 TEXT,
    shipping_address_line2 TEXT,
    shipping_city         VARCHAR(200),
    shipping_county       VARCHAR(200),
    shipping_postcode     VARCHAR(50),
    shipping_country      VARCHAR(100),
    tracking_number       VARCHAR(200),
    tracking_carrier      VARCHAR(200),
    fulfillment_status    VARCHAR(100) DEFAULT 'PENDING',
    order_total           NUMERIC(10,2),
    UNIQUE (platform, platform_order_id, sku)
);

-- ── Sales Trends ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sales_trends (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  UUID REFERENCES products(id) ON DELETE CASCADE,
    date        DATE NOT NULL,
    platform    VARCHAR(50) NOT NULL,
    units_sold  INTEGER DEFAULT 0,
    revenue     NUMERIC(10,2) DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (product_id, date, platform)
);

-- ── Sync Log ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_type     VARCHAR(50),
    status        VARCHAR(50),
    items_synced  INTEGER DEFAULT 0,
    errors        TEXT,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

-- ── Settings (key-value store) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO settings (key, value) VALUES
    ('last_full_sync',        NULL),
    ('last_catalogue_sync',   NULL),
    ('last_quick_sync_at',    NULL),
    ('ebay_access_token',     NULL),
    ('ebay_token_expiry',     NULL),
    ('low_stock_threshold',   '5')
ON CONFLICT (key) DO NOTHING;

-- ── Row Level Security ─────────────────────────────────────────────────────
ALTER TABLE products        ENABLE ROW LEVEL SECURITY;
ALTER TABLE variants        ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory       ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_trends    ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_log        ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings        ENABLE ROW LEVEL SECURITY;

-- Allow full access (single-user system, service_role used by sync)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'products') THEN
    CREATE POLICY "allow_all" ON products        FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'variants') THEN
    CREATE POLICY "allow_all" ON variants        FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'inventory') THEN
    CREATE POLICY "allow_all" ON inventory       FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'channel_listings') THEN
    CREATE POLICY "allow_all" ON channel_listings FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'orders') THEN
    CREATE POLICY "allow_all" ON orders          FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'sales_trends') THEN
    CREATE POLICY "allow_all" ON sales_trends    FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'sync_log') THEN
    CREATE POLICY "allow_all" ON sync_log        FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'allow_all' AND tablename = 'settings') THEN
    CREATE POLICY "allow_all" ON settings        FOR ALL USING (TRUE) WITH CHECK (TRUE);
  END IF;
END $$;

SELECT 'Schema v2 created successfully' AS status;
