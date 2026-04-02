import { createClient } from '@supabase/supabase-js';
import type { Product, Inventory, Pricing, Order, SalesTrend, SyncLog, Setting } from '../types';

const SUPABASE_URL = 'https://czoppjnkjxmduldxlbqh.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN6b3Bwam5ranhtZHVsZHhsYnFoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQzODExNzksImV4cCI6MjA4OTk1NzE3OX0.ehTRhOHFn6JAX3lKeEK0Tff8km6Q-8c0tZyIIf0qdR0';

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

export async function fetchProducts(): Promise<Product[]> {
  const { data, error } = await supabase.from('products').select('*').order('name').limit(5000);
  if (error) throw error;
  return data || [];
}

export async function fetchInventory(): Promise<Inventory[]> {
  const { data, error } = await supabase.from('inventory').select('*').limit(5000);
  if (error) throw error;
  return data || [];
}

// Reads from channel_listings (the new platform_pricing equivalent).
// Returns Pricing objects with product_id = products.id (via variants join),
// so Products.tsx pricing.find(p => p.product_id === product.id) works correctly.
export async function fetchPricing(): Promise<Pricing[]> {
  const { data, error } = await supabase
    .from('channel_listings')
    .select('id, channel, channel_price, channel_product_id, channel_variant_id, last_synced_at, updated_at, variant_id, variants!inner(product_id)')
    .limit(5000);
  if (error) throw error;
  return (data || []).map((row: any) => ({
    id: row.id,
    product_id: row.variants.product_id,   // products.id — matches p.id in Products.tsx
    platform: row.channel as 'ebay' | 'squarespace',
    price: row.channel_price,
    platform_product_id: row.channel_product_id,
    platform_variant_id: row.channel_variant_id,
    last_synced_at: row.last_synced_at,
    updated_at: row.updated_at,
  }));
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

  // Mark all variants for this product as needs_sync = true.
  // The hourly sync engine reads this flag and pushes stock + price to eBay and Squarespace.
  if (data.total_stock !== undefined) {
    await supabase
      .from('variants')
      .update({ needs_sync: true, updated_at: new Date().toISOString() })
      .eq('product_id', productId);
  }
}

// Update price on a channel_listing row (id = channel_listings.id).
// Also marks the associated variant as needs_sync so the sync engine pushes it to eBay/SS.
export async function updatePricing(id: string, price: number): Promise<void> {
  const now = new Date().toISOString();
  const { data: cl, error } = await supabase
    .from('channel_listings')
    .update({ channel_price: price, updated_at: now })
    .eq('id', id)
    .select('variant_id')
    .single();
  if (error) throw error;

  // Mark variant needs_sync so the platform price gets pushed on next sync
  if (cl?.variant_id) {
    await supabase
      .from('variants')
      .update({ needs_sync: true, updated_at: now })
      .eq('id', cl.variant_id);
  }
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

// Add a new platform listing for a product.
// Finds (or creates) the product's variant, then inserts a channel_listing row.
export async function createPricing(data: Partial<Pricing>): Promise<void> {
  // Find existing variant for this product
  const { data: existingVariants } = await supabase
    .from('variants')
    .select('id')
    .eq('product_id', data.product_id!)
    .limit(1);

  let variantId: string;
  if (existingVariants && existingVariants.length > 0) {
    variantId = existingVariants[0].id;
  } else {
    // Create a variant for this product
    const now = new Date().toISOString();
    const { data: newVar, error: varErr } = await supabase
      .from('variants')
      .insert({ product_id: data.product_id!, internal_sku: data.product_id!, needs_sync: false, created_at: now, updated_at: now })
      .select()
      .single();
    if (varErr) throw varErr;
    variantId = newVar.id;
  }

  const { error } = await supabase.from('channel_listings').insert({
    variant_id: variantId,
    channel: data.platform,
    channel_price: data.price,
    channel_product_id: data.platform_product_id,
    channel_variant_id: data.platform_variant_id,
    updated_at: new Date().toISOString(),
  });
  if (error) throw error;
}

export async function deleteProduct(id: string): Promise<void> {
  // Get all variants for this product then cascade-delete channel_listings
  const { data: productVariants } = await supabase.from('variants').select('id').eq('product_id', id);
  if (productVariants && productVariants.length > 0) {
    const varIds = productVariants.map((v: any) => v.id);
    await supabase.from('channel_listings').delete().in('variant_id', varIds);
    await supabase.from('variants').delete().eq('product_id', id);
  }
  await supabase.from('inventory').delete().eq('product_id', id);
  await supabase.from('sales_trends').delete().eq('product_id', id);
  const { error } = await supabase.from('products').delete().eq('id', id);
  if (error) throw error;
}

export async function mergeProducts(keepId: string, removeId: string, keepStock: number): Promise<void> {
  // ── Gather all data for snapshot (needed for undo) ──
  const { data: removedProduct } = await supabase.from('products').select('*').eq('id', removeId).single();
  const { data: removedVariants } = await supabase.from('variants').select('*').eq('product_id', removeId);
  const removedVariantIds = (removedVariants || []).map((v: any) => v.id);
  const { data: removedChannelListings } = removedVariantIds.length > 0
    ? await supabase.from('channel_listings').select('*').in('variant_id', removedVariantIds)
    : { data: [] as any[] };
  const { data: removedInventory } = await supabase.from('inventory').select('*').eq('product_id', removeId).single();

  const { data: keepProduct } = await supabase.from('products').select('*').eq('id', keepId).single();
  const { data: keepVariants } = await supabase.from('variants').select('*').eq('product_id', keepId);
  const keepVariantIds = (keepVariants || []).map((v: any) => v.id);
  const { data: keepChannelListings } = keepVariantIds.length > 0
    ? await supabase.from('channel_listings').select('*').in('variant_id', keepVariantIds)
    : { data: [] as any[] };
  const { data: keepInventory } = await supabase.from('inventory').select('*').eq('product_id', keepId).single();

  // Save undo snapshot
  const snapshot = {
    timestamp: new Date().toISOString(),
    keepId, removeId, keepStock,
    removedProduct, removedVariants, removedChannelListings, removedInventory,
    keepProduct, keepVariants, keepChannelListings, keepInventory,
  };
  await supabase.from('settings').upsert(
    { key: 'last_merge_snapshot', value: JSON.stringify(snapshot), updated_at: new Date().toISOString() },
    { onConflict: 'key' }
  );

  // ── Transfer channel_listings from removed product to kept product ──
  // Use the first variant of the keep product as the target
  const keepVariantId = keepVariantIds[0];
  if (removedChannelListings && keepVariantId) {
    const keepChannels = new Set((keepChannelListings || []).map((cl: any) => cl.channel));
    for (const cl of removedChannelListings) {
      if (!keepChannels.has(cl.channel)) {
        // This channel isn't on the keep product yet — transfer it
        await supabase.from('channel_listings')
          .update({ variant_id: keepVariantId, updated_at: new Date().toISOString() })
          .eq('id', cl.id);
      }
      // If keep product already has this channel, just drop the duplicate
    }
  }

  // Move orders and sales trends to kept product
  await supabase.from('orders').update({ product_id: keepId }).eq('product_id', removeId);
  await supabase.from('sales_trends').update({ product_id: keepId }).eq('product_id', removeId);

  // Update stock on kept product (also triggers needs_sync push to platforms)
  await updateInventory(keepId, { total_stock: keepStock });

  // Delete removed product's remaining channel_listings, variants, inventory, product row
  if (removedVariantIds.length > 0) {
    await supabase.from('channel_listings').delete().in('variant_id', removedVariantIds);
    await supabase.from('variants').delete().eq('product_id', removeId);
  }
  await supabase.from('inventory').delete().eq('product_id', removeId);
  await supabase.from('products').delete().eq('id', removeId);

  // ── Add removed product's SKUs to merged_skus blocklist ──
  // Uses variants.internal_sku (reliable) + products.sku (legacy fallback)
  const skusToBlock: string[] = [];
  for (const v of (removedVariants || [])) {
    if (v.internal_sku) skusToBlock.push(v.internal_sku);
  }
  if (removedProduct?.sku) skusToBlock.push(removedProduct.sku);

  if (skusToBlock.length > 0) {
    const { data: existing } = await supabase.from('settings').select('value').eq('key', 'merged_skus').single();
    const skus: string[] = existing?.value ? JSON.parse(existing.value) : [];
    for (const sku of skusToBlock) {
      if (!skus.includes(sku)) skus.push(sku);
    }
    await supabase.from('settings').upsert(
      { key: 'merged_skus', value: JSON.stringify(skus), updated_at: new Date().toISOString() },
      { onConflict: 'key' }
    );
  }
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
  const { keepId, removeId, removedProduct, removedVariants, removedChannelListings, removedInventory, keepInventory } = snap;

  // 1. Re-create the removed product
  const { error: prodErr } = await supabase.from('products').insert({
    id: removedProduct.id,
    sku: removedProduct.sku,
    name: removedProduct.name,
    description: removedProduct.description || '',
    category: removedProduct.category,
    image_url: removedProduct.image_url,
    active: removedProduct.active,
    status: removedProduct.status || 'active',
    cost_price: removedProduct.cost_price,
    created_at: removedProduct.created_at,
    updated_at: new Date().toISOString(),
  });
  if (prodErr) throw prodErr;

  // 2. Re-create variants for the removed product
  if (removedVariants) {
    for (const v of removedVariants) {
      await supabase.from('variants').insert({
        id: v.id,
        product_id: removeId,
        internal_sku: v.internal_sku,
        option1: v.option1,
        option2: v.option2,
        needs_sync: false,
        created_at: v.created_at,
        updated_at: new Date().toISOString(),
      });
    }
  }

  // 3. Move transferred channel_listings back to the removed product's variants
  if (removedChannelListings) {
    for (const cl of removedChannelListings) {
      // Check if this channel_listing still exists (was transferred to keep product)
      const { data: onKept } = await supabase.from('channel_listings').select('id').eq('id', cl.id);
      if (onKept && onKept.length > 0) {
        // Move it back to the original variant
        await supabase.from('channel_listings')
          .update({ variant_id: cl.variant_id, updated_at: new Date().toISOString() })
          .eq('id', cl.id);
      } else {
        // Row was deleted — re-create it
        await supabase.from('channel_listings').insert({
          variant_id: cl.variant_id,
          channel: cl.channel,
          channel_price: cl.channel_price,
          channel_product_id: cl.channel_product_id,
          channel_variant_id: cl.channel_variant_id,
          updated_at: new Date().toISOString(),
        });
      }
    }
  }

  // 4. Re-create inventory for removed product
  if (removedInventory) {
    await supabase.from('inventory').insert({
      product_id: removeId,
      variant_id: removedVariants?.[0]?.id || null,
      total_stock: removedInventory.total_stock,
      reserved_stock: removedInventory.reserved_stock,
      low_stock_threshold: removedInventory.low_stock_threshold,
      updated_at: new Date().toISOString(),
    });
  }

  // 5. Restore kept product's original stock
  if (keepInventory) {
    await supabase.from('inventory')
      .update({ total_stock: keepInventory.total_stock, updated_at: new Date().toISOString() })
      .eq('product_id', keepId);
  }

  // 6. Remove SKUs from the merged_skus blocklist
  const skusToRemove: string[] = [];
  for (const v of (removedVariants || [])) {
    if (v.internal_sku) skusToRemove.push(v.internal_sku);
  }
  if (removedProduct?.sku) skusToRemove.push(removedProduct.sku);

  if (skusToRemove.length > 0) {
    const { data: msData } = await supabase.from('settings').select('value').eq('key', 'merged_skus').single();
    if (msData?.value) {
      const skus: string[] = JSON.parse(msData.value).filter((s: string) => !skusToRemove.includes(s));
      await supabase.from('settings').upsert(
        { key: 'merged_skus', value: JSON.stringify(skus), updated_at: new Date().toISOString() },
        { onConflict: 'key' }
      );
    }
  }

  // 7. Clear the snapshot
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
