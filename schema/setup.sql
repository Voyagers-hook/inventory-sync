-- ============================================================
-- Voyagers Hook — Inventory Sync Database Schema
-- Run this entire file in Supabase SQL Editor to set up your DB
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Products (master catalogue) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      VARCHAR(500)  NOT NULL,
    sku                       VARCHAR(200)  UNIQUE,
    squarespace_product_id    VARCHAR(200),
    squarespace_variant_id    VARCHAR(200),
    ebay_listing_id           VARCHAR(200),
    ebay_inventory_item_key   VARCHAR(200),  -- usually the SKU on eBay side
    description               TEXT,
    category                  VARCHAR(200),
    image_url                 TEXT,
    active                    BOOLEAN DEFAULT TRUE,
    created_at                TIMESTAMPTZ DEFAULT NOW(),
    updated_at                TIMESTAMPTZ DEFAULT NOW()
);

-- ── Inventory ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          UUID UNIQUE REFERENCES products(id) ON DELETE CASCADE,
    total_stock         INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER DEFAULT 3,
    last_synced_at      TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── Pricing (per platform) ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pricing (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID REFERENCES products(id) ON DELETE CASCADE,
    platform       VARCHAR(50) NOT NULL CHECK (platform IN ('squarespace','ebay')),
    price          NUMERIC(10,2) NOT NULL,
    currency       VARCHAR(3) DEFAULT 'GBP',
    sync_pending   BOOLEAN DEFAULT FALSE,   -- set to TRUE when dashboard changes price
    updated_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (product_id, platform)
);

-- ── Orders / Sales ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform            VARCHAR(50) NOT NULL,
    platform_order_id   VARCHAR(200),
    product_id          UUID REFERENCES products(id) ON DELETE SET NULL,
    quantity            INTEGER NOT NULL DEFAULT 1,
    sale_price          NUMERIC(10,2),
    currency            VARCHAR(3) DEFAULT 'GBP',
    sale_date           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (platform, platform_order_id)
);

-- ── Daily Snapshots (trend data) ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_snapshots (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   UUID REFERENCES products(id) ON DELETE CASCADE,
    date         DATE NOT NULL,
    platform     VARCHAR(50) NOT NULL,
    units_sold   INTEGER DEFAULT 0,
    revenue      NUMERIC(10,2) DEFAULT 0,
    ending_stock INTEGER,
    UNIQUE (product_id, date, platform)
);

-- ── Sync Log ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_type     VARCHAR(50),
    status        VARCHAR(50),
    items_synced  INTEGER DEFAULT 0,
    errors        TEXT,
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

-- ── Settings (key-value store) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pre-seed required settings
INSERT INTO settings (key, value) VALUES
    ('last_full_sync',           NULL),
    ('manual_sync_requested',    'false'),
    ('ebay_access_token',        NULL),
    ('ebay_token_expiry',        NULL),
    ('low_stock_alert_email',    NULL),
    ('low_stock_threshold',      '3')
ON CONFLICT (key) DO NOTHING;

-- ── Row Level Security (disable for service_role key access) ───────────────────
ALTER TABLE products        ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory       ENABLE ROW LEVEL SECURITY;
ALTER TABLE pricing         ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE sync_log        ENABLE ROW LEVEL SECURITY;
ALTER TABLE settings        ENABLE ROW LEVEL SECURITY;

-- Allow full access via service_role (used by sync script and dashboard)
CREATE POLICY "service_role_all" ON products        FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON inventory       FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON pricing         FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON orders          FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON daily_snapshots FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON sync_log        FOR ALL USING (TRUE);
CREATE POLICY "service_role_all" ON settings        FOR ALL USING (TRUE);

-- Done!
SELECT 'Schema created successfully' AS status;
