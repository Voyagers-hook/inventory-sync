// ─── Core domain types — v2 ──────────────────────────────────────────────────
//
// Products: id = variant_id (the operational key for stock + pricing).
//           product_id = the parent products.id (grouping key for multi-variant products).
//
// The dashboard treats each variant as a "product row" in the UI.
// For the current single-variant-per-product setup this is 1:1, but the
// structure is ready for multi-variant products.

export interface Product {
  id: string;              // variants.id  (variant_id — used as primary key throughout UI)
  product_id: string;      // products.id  (the parent product group)
  name: string;            // products.name
  sku: string;             // variants.internal_sku
  option1?: string;        // variants.option1 (e.g. "Size: M")
  option2?: string;        // variants.option2 (e.g. "Colour: Red")
  description?: string;
  cost_price?: number | null;
  status: string;          // "active" | "archived"
  needs_sync: boolean;     // variants.needs_sync
  last_synced_at?: string | null;
  created_at: string;
  updated_at?: string;
  // Legacy aliases kept for backward compat during migration
  active?: boolean;
}

export interface Variant {
  id: string;
  product_id: string;
  internal_sku: string;
  option1?: string;
  option2?: string;
  needs_sync: boolean;
  last_synced_at?: string | null;
  created_at: string;
  updated_at?: string;
}

export interface Inventory {
  id: string;
  variant_id: string;      // primary FK
  product_id?: string;     // legacy (kept for backward compat)
  total_stock: number;
  reserved_stock?: number;
  low_stock_threshold?: number;
  updated_at?: string;
}

// channel_listings row — one row per platform listing per variant
export interface ChannelListing {
  id: string;
  variant_id: string;
  channel: 'ebay' | 'squarespace';
  channel_sku?: string;
  channel_price?: number | null;
  channel_product_id?: string;
  channel_variant_id?: string;
  last_synced_at?: string | null;
  updated_at?: string;
}

// Legacy alias — Pricing maps to channel_listings for backward compat.
// product_id here is always variant_id.
// platform here is always channel.
export interface Pricing {
  id: string;
  product_id: string;       // = variant_id
  platform: 'ebay' | 'squarespace';  // = channel
  price?: number | null;
  currency?: string;
  platform_product_id?: string;    // = channel_product_id
  platform_variant_id?: string;    // = channel_variant_id
  last_synced_at?: string | null;
  updated_at?: string;
}

export interface Order {
  id: string;
  platform: string;
  platform_order_id: string;
  product_id?: string;
  sku?: string;
  quantity: number;
  unit_price: number;
  total_price?: number;
  currency?: string;
  status: string;
  fulfillment_status?: string;
  ordered_at: string;
  synced_at?: string;
  customer_name?: string;
  customer_email?: string;
  shipping_address_line1?: string;
  shipping_address_line2?: string;
  shipping_city?: string;
  shipping_county?: string;
  shipping_postcode?: string;
  shipping_country?: string;
  tracking_number?: string;
  tracking_carrier?: string;
  order_total?: number;
  item_name?: string;
  order_number?: string;
}

export interface SalesTrend {
  id?: string;
  product_id?: string;
  platform: string;
  date: string;
  units_sold: number;
  revenue: number;
}

export interface Setting {
  key: string;
  value: string;
}

export interface SyncLog {
  id: string;
  sync_type: string;
  status: string;
  source?: string;
  details?: string;
  started_at: string;
  completed_at?: string;
  error_message?: string;
}
