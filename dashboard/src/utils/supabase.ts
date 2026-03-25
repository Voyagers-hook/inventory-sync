import { createClient } from '@supabase/supabase-js';
import type { Product, Inventory, Pricing, Order, SalesTrend, SyncLog, Setting } from '../types';

const SUPABASE_URL = 'https://czoppjnkjxmduldxlbqh.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN6b3Bwam5ranhtZHVsZHhsYnFoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQzODExNzksImV4cCI6MjA4OTk1NzE3OX0.ehTRhOHFn6JAX3lKeEK0Tff8km6Q-8c0tZyIIf0qdR0';

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

export async function fetchProducts(): Promise<Product[]> {
  const { data, error } = await supabase.from('products').select('*').order('name');
  if (error) throw error;
  return data || [];
}

export async function fetchInventory(): Promise<Inventory[]> {
  const { data, error } = await supabase.from('inventory').select('*');
  if (error) throw error;
  return data || [];
}

export async function fetchPricing(): Promise<Pricing[]> {
  const { data, error } = await supabase.from('platform_pricing').select('*');
  if (error) throw error;
  return data || [];
}

export async function fetchOrders(): Promise<Order[]> {
  const { data, error } = await supabase.from('orders').select('*').order('ordered_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchSalesTrends(): Promise<SalesTrend[]> {
  const { data, error } = await supabase.from('sales_trends').select('*').order('date', { ascending: false });
  if (error) throw error;
  return data || [];
}

export async function fetchSyncLogs(): Promise<SyncLog[]> {
  const { data, error } = await supabase.from('sync_log').select('*').order('started_at', { ascending: false }).limit(10);
  if (error) throw error;
  return data || [];
}

export async function fetchSettings(): Promise<Setting[]> {
  const { data, error } = await supabase.from('settings').select('*');
  if (error) throw error;
  return data || [];
}

export async function updateSetting(key: string, value: string): Promise<void> {
  const { error } = await supabase.from('settings').upsert({ key, value, updated_at: new Date().toISOString() }, { onConflict: 'key' });
  if (error) throw error;
}

export async function updateInventory(productId: string, data: Partial<Inventory>): Promise<void> {
  const { error } = await supabase.from('inventory').update({ ...data, updated_at: new Date().toISOString() }).eq('product_id', productId);
  if (error) throw error;
}

export async function updatePricing(id: string, price: number): Promise<void> {
  const { error } = await supabase.from('platform_pricing').update({ price, updated_at: new Date().toISOString() }).eq('id', id);
  if (error) throw error;
}

export async function updateOrder(orderId: string, data: Partial<Order>): Promise<void> {
  const { error } = await supabase.from('orders').update(data).eq('id', orderId);
  if (error) throw error;
}

export async function updateProduct(id: string, data: Partial<Product>): Promise<void> {
  const { error } = await supabase.from('products').update({ ...data, updated_at: new Date().toISOString() }).eq('id', id);
  if (error) throw error;
}

export async function createProduct(data: Partial<Product>): Promise<Product> {
  const { data: result, error } = await supabase.from('products').insert({ ...data, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }).select().single();
  if (error) throw error;
  return result;
}

export async function createInventory(data: Partial<Inventory>): Promise<void> {
  const { error } = await supabase.from('inventory').insert({ ...data, updated_at: new Date().toISOString() });
  if (error) throw error;
}

export async function createPricing(data: Partial<Pricing>): Promise<void> {
  const { error } = await supabase.from('platform_pricing').insert({ ...data, updated_at: new Date().toISOString() });
  if (error) throw error;
}

export async function deleteProduct(id: string): Promise<void> {
  await supabase.from('platform_pricing').delete().eq('product_id', id);
  await supabase.from('inventory').delete().eq('product_id', id);
  await supabase.from('sales_trends').delete().eq('product_id', id);
  const { error } = await supabase.from('products').delete().eq('id', id);
  if (error) throw error;
}

export async function mergeProducts(keepId: string, removeId: string, keepStock: number): Promise<void> {
  // Get pricing for product to remove
  const { data: removedPricing } = await supabase.from('platform_pricing').select('*').eq('product_id', removeId);
  if (removedPricing) {
    for (const p of removedPricing) {
      const { data: existing } = await supabase.from('platform_pricing').select('*').eq('product_id', keepId).eq('platform', p.platform);
      if (!existing || existing.length === 0) {
        await supabase.from('platform_pricing').update({ product_id: keepId, updated_at: new Date().toISOString() }).eq('id', p.id);
      }
    }
  }
  // Move orders and trends
  await supabase.from('orders').update({ product_id: keepId }).eq('product_id', removeId);
  await supabase.from('sales_trends').update({ product_id: keepId }).eq('product_id', removeId);
  // Update stock
  await updateInventory(keepId, { total_stock: keepStock });
  // Delete old product
  await supabase.from('platform_pricing').delete().eq('product_id', removeId);
  await supabase.from('inventory').delete().eq('product_id', removeId);
  await supabase.from('products').delete().eq('id', removeId);
}
