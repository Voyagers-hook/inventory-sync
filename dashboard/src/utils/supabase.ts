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
  const { error } = await supabase
    .from('inventory')
    .update({ ...data, updated_at: new Date().toISOString() })
    .eq('product_id', productId);
  if (error) throw error;

  // If stock is being changed, queue a push to both platforms on next sync.
  if (data.total_stock !== undefined) {
    await supabase
      .from('settings')
      .upsert(
        { key: `stock_push_${productId}`, value: String(data.total_stock) },
        { onConflict: 'key' }
      );
  }
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
  // ── Save snapshot of BOTH products before merge (for undo) ──
  const { data: removedProduct } = await supabase.from('products').select('*').eq('id', removeId).single();
  const { data: removedPricing } = await supabase.from('platform_pricing').select('*').eq('product_id', removeId);
  const { data: removedInventory } = await supabase.from('inventory').select('*').eq('product_id', removeId).single();
  const { data: keepProduct } = await supabase.from('products').select('*').eq('id', keepId).single();
  const { data: keepPricing } = await supabase.from('platform_pricing').select('*').eq('product_id', keepId);
  const { data: keepInventory } = await supabase.from('inventory').select('*').eq('product_id', keepId).single();

  const snapshot = {
    timestamp: new Date().toISOString(),
    keepId,
    removeId,
    keepStock,
    removedProduct,
    removedPricing,
    removedInventory,
    keepProduct,
    keepPricing,
    keepInventory,
  };

  await supabase.from('settings').upsert(
    { key: 'last_merge_snapshot', value: JSON.stringify(snapshot), updated_at: new Date().toISOString() },
    { onConflict: 'key' }
  );

  // ── Transfer pricing from removed product to kept product ──
  if (removedPricing) {
    for (const p of removedPricing) {
      const { data: existing } = await supabase.from('platform_pricing').select('*').eq('product_id', keepId).eq('platform', p.platform);
      if (!existing || existing.length === 0) {
        await supabase.from('platform_pricing').update({ product_id: keepId, updated_at: new Date().toISOString() }).eq('id', p.id);
      }
    }
  }
  // Move orders and trends to kept product
  await supabase.from('orders').update({ product_id: keepId }).eq('product_id', removeId);
  await supabase.from('sales_trends').update({ product_id: keepId }).eq('product_id', removeId);
  // Update stock (this also queues a stock push to both platforms)
  await updateInventory(keepId, { total_stock: keepStock });
  // Delete removed product
  await supabase.from('platform_pricing').delete().eq('product_id', removeId);
  await supabase.from('inventory').delete().eq('product_id', removeId);
  await supabase.from('products').delete().eq('id', removeId);
}

/** Check if there's a merge that can be undone */
export async function getLastMergeSnapshot(): Promise<{ removedName: string; keepName: string; timestamp: string } | null> {
  const { data } = await supabase.from('settings').select('value').eq('key', 'last_merge_snapshot').single();
  if (!data?.value) return null;
  try {
    const snap = JSON.parse(data.value);
    return {
      removedName: snap.removedProduct?.name || 'Unknown',
      keepName: snap.keepProduct?.name || 'Unknown',
      timestamp: snap.timestamp,
    };
  } catch { return null; }
}

/** Undo the last merge — restore the removed product and revert pricing */
export async function undoLastMerge(): Promise<string> {
  const { data } = await supabase.from('settings').select('value').eq('key', 'last_merge_snapshot').single();
  if (!data?.value) throw new Error('No merge to undo');

  const snap = JSON.parse(data.value);
  const { keepId, removeId, removedProduct, removedPricing, removedInventory, keepPricing, keepInventory } = snap;

  // 1. Re-create the removed product
  const { error: prodErr } = await supabase.from('products').insert({
    id: removedProduct.id,
    sku: removedProduct.sku,
    name: removedProduct.name,
    description: removedProduct.description || '',
    category: removedProduct.category,
    image_url: removedProduct.image_url,
    active: removedProduct.active,
    cost_price: removedProduct.cost_price,
    created_at: removedProduct.created_at,
    updated_at: new Date().toISOString(),
  });
  if (prodErr) throw prodErr;

  // 2. Re-create inventory for removed product
  if (removedInventory) {
    await supabase.from('inventory').insert({
      product_id: removeId,
      total_stock: removedInventory.total_stock,
      reserved_stock: removedInventory.reserved_stock,
      low_stock_threshold: removedInventory.low_stock_threshold,
      location: removedInventory.location,
      updated_at: new Date().toISOString(),
    });
  }

  // 3. Move transferred pricing rows back to the removed product
  // Pricing that was originally on the removed product and got moved to the kept product
  if (removedPricing) {
    for (const rp of removedPricing) {
      // Check if this pricing row still exists on the kept product (it was transferred there)
      const { data: onKept } = await supabase.from('platform_pricing')
        .select('*')
        .eq('product_id', keepId)
        .eq('id', rp.id);
      if (onKept && onKept.length > 0) {
        // Move it back
        await supabase.from('platform_pricing').update({ product_id: removeId, updated_at: new Date().toISOString() }).eq('id', rp.id);
      } else {
        // Row was deleted (kept product already had that platform) — re-create it
        await supabase.from('platform_pricing').insert({
          product_id: removeId,
          platform: rp.platform,
          price: rp.price,
          currency: rp.currency,
          platform_product_id: rp.platform_product_id,
          platform_variant_id: rp.platform_variant_id,
          last_synced_at: rp.last_synced_at,
          updated_at: new Date().toISOString(),
        });
      }
    }
  }

  // 4. Restore the kept product's original inventory
  if (keepInventory) {
    await supabase.from('inventory')
      .update({ total_stock: keepInventory.total_stock, updated_at: new Date().toISOString() })
      .eq('product_id', keepId);
  }

  // 5. Move orders/trends back if they were originally on the removed product
  await supabase.from('orders').update({ product_id: removeId }).eq('product_id', keepId);
  await supabase.from('sales_trends').update({ product_id: removeId }).eq('product_id', keepId);

  // 6. Clear the snapshot
  await supabase.from('settings').delete().eq('key', 'last_merge_snapshot');

  return removedProduct.name;
}

// ─── GitHub Actions Trigger ───────────────────────────────────────────────────

const GITHUB_TOKEN = import.meta.env.VITE_GITHUB_TOKEN || '';
const GITHUB_REPO = import.meta.env.VITE_GITHUB_REPO || 'Voyagers-hook/inventory-sync';

/**
 * Triggers the Quick Sync workflow on GitHub Actions immediately.
 * Returns true if the trigger was accepted (HTTP 204), false otherwise.
 */
export async function triggerQuickSync(): Promise<boolean> {
  if (!GITHUB_TOKEN) {
    console.warn('VITE_GITHUB_TOKEN not set — cannot trigger sync');
    return false;
  }
  try {
    const resp = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/sync-quick.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: 'application/vnd.github+json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main' }),
      }
    );
    return resp.status === 204;
  } catch (e) {
    console.error('Failed to trigger quick sync:', e);
    return false;
  }
}

// ─── Competitor Price Monitoring ──────────────────────────────────────────────

export async function getCompetitorPrices(): Promise<Record<string, any>> {
  const { data } = await supabase
    .from('settings')
    .select('key, value')
    .like('key', 'competitor_price_%');
  
  const map: Record<string, any> = {};
  if (data) {
    for (const row of data) {
      const productId = row.key.replace('competitor_price_', '');
      try {
        map[productId] = JSON.parse(row.value);
      } catch { }
    }
  }
  return map;
}

export async function triggerCompetitorCheck(productId: string): Promise<boolean> {
  const token = import.meta.env.VITE_GITHUB_TOKEN || '';
  if (!token) return false;
  
  const resp = await fetch(
    'https://api.github.com/repos/Voyagers-hook/inventory-sync/actions/workflows/price-scan-daily.yml/dispatches',
    {
      method: 'POST',
      headers: {
        'Authorization': `token ${token}`,
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        ref: 'main',
        inputs: { product_id: productId }
      })
    }
  );
  return resp.ok || resp.status === 204;
}
