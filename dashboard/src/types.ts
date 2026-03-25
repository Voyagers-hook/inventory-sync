export interface Product {
  id: string;
  name: string;
  sku: string;
  description: string;
  category: string | null;
  image_url: string | null;
  cost_price: number | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Inventory {
  id: string;
  product_id: string;
  total_stock: number;
  reserved_stock: number;
  low_stock_threshold: number;
  location: string | null;
  updated_at: string;
}

export interface Pricing {
  id: string;
  product_id: string;
  platform: string;
  price: number;
  currency: string;
  platform_product_id: string | null;
  platform_variant_id: string | null;
  last_synced_at: string | null;
  updated_at: string;
}

export interface Order {
  id: string;
  platform: string;
  platform_order_id: string;
  product_id: string;
  sku: string;
  quantity: number;
  unit_price: number;
  total_price: number;
  currency: string;
  status: string;
  ordered_at: string;
  synced_at: string | null;
  customer_name: string;
  customer_email: string;
  shipping_address_line1: string;
  shipping_address_line2: string;
  shipping_city: string;
  shipping_county: string;
  shipping_postcode: string;
  shipping_country: string;
  tracking_number: string;
  tracking_carrier: string;
  fulfillment_status: string;
  order_total: number;
  item_name: string;
  order_number: string;
}

export interface SalesTrend {
  id: string;
  product_id: string;
  platform: string;
  date: string;
  units_sold: number;
  revenue: number;
  updated_at: string;
}

export interface SyncLog {
  id: string;
  sync_type: string;
  status: string;
  source: string;
  details: string;
  started_at: string;
  completed_at: string;
  error_message: string;
}

export interface Setting {
  key: string;
  value: string;
  updated_at: string;
}

export interface ProductWithDetails extends Product {
  inventory?: Inventory;
  pricing?: Pricing[];
}

export type TabName = 'dashboard' | 'orders' | 'products' | 'sales' | 'trends' | 'settings';
