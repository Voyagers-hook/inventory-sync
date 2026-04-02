/**
 * Supabase client utilities — v2
 *
 * KEY CHANGES vs v1:
 * - Products are fetched by joining variants + products; Product.id = variant_id
 * - Pricing is fetched from channel_listings (not platform_pricing)
 * - Inventory is fetched by variant_id
 * - updateInventory / updatePricing set variants.needs_sync = TRUE (no immediate push)
 * - mergeProducts: moves channel_listings + inventory to kept variant, then deletes removed
 * - No more settings-based stock_push_* queue
 *
 * SYNC BEHAVIOUR:
 *   Edit stock  → sets needs_sync = true  (synced on next hourly GitHub Actions run)
 *   Edit price  → sets needs_sync = true  (synced on next hourly GitHub Actions run)
 *   Merge       → sets needs_sync = true on kept variant
 *   Sync Now    → reads github_token from settings, dispatches sync-quick.yml workflow
 */

/// <reference types="vite/client" />
import { createClient } from '@supabase/supabase-js';
import type { Product, Inventory, Pricing, Order, SyncLog, SalesTrend, Setting } from '../types';

const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string) || 'https://czoppjnkjxmduldxlbqh.supabase.co';
const SUPABASE_ANON_KEY = (import.meta.env.VITE_SUPABASE_ANON_KEY as string) || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN6b3Bwam5ranhtZHVsZHhsYnFoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQzODExNzksImV4cCI6MjA4OTk1NzE3OX0.ehTRhOHFn6JAX3lKeEK0Tff8km6Q-8c0tZyIIf0qdR0';

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ─── Pagination helper ───────────────────────────────────────────────────────
// Supabase PostgREST returns max 1 000 rows per request.
// This helper pages through all rows so we never silently drop data.

async function fetchAll<T = any>(
  table: string,
  select = '*',
  opts?: { order?: string; ascending?: boolean; filters?: (q: any) => any },
): Promise<T[]> {
  const PAGE = 1000;
  let offset = 0;
  const all: T[] = [];
  // eslint-disable-next-line no-constant-condition
  while (true) {
    let q = supabase.from(table).select(select).range(offset, offset + PAGE - 1);
    if (opts?.order) q = q.order(opts.order, { ascending: opts.ascending ?? true });
    if (opts?.filters) q = opts.filters(q);
    const { data, error } = await q;
    if (error) throw error;
    const rows = (data ?? []) as T[];
    all.push(...rows);
    if (rows.length < PAGE) break;
    offset += PAGE;
  }
  return all;
}

// ─── Products ────────────────────────────────────────────────────────────────

/**
 * Fetch all products as a flat variant+product joined structure.
 * Product.id = variant_id (the operational key throughout the dashboard).
 * Uses LEFT JOIN so products without inventory/channel_listings still appear.
 */
export async function fetchProducts(): Promise<Product[]> {
  // PostgREST embedding: variants joined with parent products
  // Paginate in chunks of 1000 (Supabase hard cap per request)
  const PAGE = 1000;
  let offset = 0;
  const all: any[] = [];
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { data, error } = await supabase
      .from('variants')
      .select(`
        id,
        product_id,
        internal_sku,
        option1,
        option2,
        needs_sync,
        last_synced_at,
        created_at,
        updated_at,
        products (
          name,
          description,
          status,
          cost_price,
          active
        )
      `)
      .order('created_at', { ascending: false })
      .range(offset, offset + PAGE - 1);

    if (error) throw error;
    const rows = data ?? [];
    all.push(...rows);
    if (rows.length < PAGE) break;
    offset += PAGE;
  }

  return all.map((v: any) => ({
    id: v.id,                               // variant_id — primary key for UI
    product_id: v.product_id,
    sku: v.internal_sku ?? '',
    option1: v.option1,
    option2: v.option2,
    needs_sync: v.needs_sync ?? false,
    last_synced_at: v.last_synced_at,
    created_at: v.created_at,
    updated_at: v.updated_at,
    // From parent product
    name: v.products?.name ?? '(no name)',
    description: v.products?.description ?? '',
    status: v.products?.status ?? 'active',
    cost_price: v.products?.cost_price ?? null,
    active: v.products?.active ?? true,
  }));
}

export async function createProduct(product: { name: string; sku: string; description?: string }): Promise<Product> {
  // 1. Insert products row
  const { data: prodData, error: prodError } = await supabase
    .from('products')
    .insert({
      name: product.name,
      sku: product.sku,
      description: product.description ?? '',
      status: 'active',
      active: true,
    })
    .select()
    .single();
  if (prodError) throw prodError;

  // 2. Insert variants row
  const { data: varData, error: varError } = await supabase
    .from('variants')
    .insert({
      product_id: prodData.id,
      internal_sku: product.sku,
      needs_sync: false,
    })
    .select()
    .single();
  if (varError) throw varError;

  return {
    id: varData.id,
    product_id: prodData.id,
    sku: varData.internal_sku,
    name: prodData.name,
    description: prodData.description,
    status: 'active',
    needs_sync: false,
    created_at: varData.created_at,
  };
}

export async function updateProduct(variantId: string, patch: Partial<{ cost_price: number | null; name: string; status: string }>) {
  // Get product_id from variant
  const { data: varData, error: varErr } = await supabase
    .from('variants')
    .select('product_id')
    .eq('id', variantId)
    .single();
  if (varErr) throw varErr;

  const { error } = await supabase
    .from('products')
    .update({ ...patch, updated_at: new Date().toISOString() })
    .eq('id', varData.product_id);
  if (error) throw error;
}

export async function deleteProduct(variantId: string) {
  // Delete channel_listings (cascades via FK but explicit for safety)
  await supabase.from('channel_listings').delete().eq('variant_id', variantId);
  // Delete inventory
  await supabase.from('inventory').delete().eq('variant_id', variantId);

  // Get product_id before deleting variant
  const { data: varData } = await supabase
    .from('variants')
    .select('product_id')
    .eq('id', variantId)
    .single();

  // Delete variant
  await supabase.from('variants').delete().eq('id', variantId);

  // Delete parent product if no variants remain
  if (varData?.product_id) {
    const { data: remaining } = await supabase
      .from('variants')
      .select('id')
      .eq('product_id', varData.product_id);
    if (!remaining || remaining.length === 0) {
      await supabase.from('products').delete().eq('id', varData.product_id);
    }
  }
}

// ─── Inventory ───────────────────────────────────────────────────────────────

/**
 * Fetch all inventory rows.
 * Returns with both variant_id and product_id set (for backward compat).
 */
export async function fetchInventory(): Promise<Inventory[]> {
  const raw = await fetchAll('inventory', '*');
  return raw.map((row: any) => {
    const variantId = row.variant_id ?? row.product_id;
    return {
      ...row,
      variant_id: variantId,
      product_id: variantId,
    };
  });
}

export async function createInventory(inv: {
  product_id: string;    // pass variant_id here
  variant_id?: string;
  total_stock: number;
  reserved_stock?: number;
  low_stock_threshold?: number;
}): Promise<void> {
  const variantId = inv.variant_id ?? inv.product_id;
  const { error } = await supabase.from('inventory').insert({
    variant_id: variantId,
    product_id: inv.product_id,  // legacy column kept
    total_stock: inv.total_stock ?? 0,
    reserved_stock: inv.reserved_stock ?? 0,
    low_stock_threshold: inv.low_stock_threshold ?? 5,
  });
  if (error) throw error;
}

/**
 * Update stock for a variant.
 * Does NOT immediately push to platforms — sets needs_sync=TRUE on the variant.
 * The hourly GitHub Actions sync will push the change.
 */
export async function updateInventory(
  variantId: string,
  patch: Partial<{ total_stock: number; reserved_stock: number; low_stock_threshold: number }>
): Promise<void> {
  // Update inventory row
  const { error } = await supabase
    .from('inventory')
    .update({ ...patch, updated_at: new Date().toISOString() })
    .eq('variant_id', variantId);
  if (error) throw error;

  // Mark variant needs_sync so hourly job pushes the new stock to platforms
  await markVariantNeedsSync(variantId);
}

// ─── Channel Listings (new) / Pricing (legacy alias) ─────────────────────────

/**
 * Fetch all channel_listings, returned as Pricing[] for backward compat.
 * product_id = variant_id in the returned objects.
 */
export async function fetchPricing(): Promise<Pricing[]> {
  const raw = await fetchAll('channel_listings', '*');

  return raw.map((cl: any) => ({
    id: cl.id,
    product_id: cl.variant_id,       // map variant_id → product_id for compat
    platform: cl.channel,             // map channel → platform for compat
    price: cl.channel_price,          // map channel_price → price for compat
    currency: 'GBP',
    platform_product_id: cl.channel_product_id,
    platform_variant_id: cl.channel_variant_id,
    last_synced_at: cl.last_synced_at,
    updated_at: cl.updated_at,
  }));
}

export async function createPricing(pricing: {
  product_id: string;   // variant_id
  platform: string;
  price?: number;
  channel_sku?: string;
  platform_product_id?: string;
  platform_variant_id?: string;
}): Promise<void> {
  const { error } = await supabase.from('channel_listings').insert({
    variant_id: pricing.product_id,
    channel: pricing.platform,
    channel_price: pricing.price,
    channel_sku: pricing.channel_sku,
    channel_product_id: pricing.platform_product_id,
    channel_variant_id: pricing.platform_variant_id,
    updated_at: new Date().toISOString(),
  });
  if (error) throw error;

  // Mark variant needs_sync so hourly job pushes the new listing
  await markVariantNeedsSync(pricing.product_id);
}

/**
 * Update price for a channel listing.
 * Does NOT immediately push — sets needs_sync=TRUE on the variant.
 */
export async function updatePricing(listingId: string, price: number): Promise<void> {
  // First, get the variant_id so we can mark needs_sync
  const { data: existing, error: fetchErr } = await supabase
    .from('channel_listings')
    .select('variant_id')
    .eq('id', listingId)
    .single();
  if (fetchErr) throw fetchErr;

  // Update price (updated_at > last_synced_at flags it as changed)
  const { error } = await supabase
    .from('channel_listings')
    .update({ channel_price: price, updated_at: new Date().toISOString() })
    .eq('id', listingId);
  if (error) throw error;

  // Mark variant needs_sync
  if (existing?.variant_id) {
    await markVariantNeedsSync(existing.variant_id);
  }
}

// ─── needs_sync flag ─────────────────────────────────────────────────────────

/**
 * Mark a variant as needing sync on the next hourly run.
 * Called after any stock or price edit.
 * Does NOT immediately push to eBay or Squarespace.
 */
export async function markVariantNeedsSync(variantId: string): Promise<void> {
  const { error } = await supabase
    .from('variants')
    .update({ needs_sync: true, updated_at: new Date().toISOString() })
    .eq('id', variantId);
  if (error) throw error;
}

// ─── Merge / Linking ─────────────────────────────────────────────────────────

/**
 * Merge two variants into one.
 *
 * Correct merge behaviour:
 * 1. Save snapshot for undo
 * 2. Move all channel_listings from removeId → keepId
 * 3. Set inventory on kept variant to the specified stock value
 * 4. Remove inventory row for removed variant
 * 5. Remove the removed variant (and its parent product if now empty)
 * 6. Mark kept variant needs_sync = true (pushed on next hourly run)
 * 7. Add removed variant's SKU to merged_skus blocklist (prevents reimport)
 *
 * keepId and removeId are both variant_ids.
 */
export async function mergeProducts(keepId: string, removeId: string, stock: number): Promise<void> {
  // ── Step 1: Save undo snapshot ──────────────────────────────────────────
  const [keepVariant, removeVariant] = await Promise.all([
    supabase.from('variants').select('*, products(name)').eq('id', keepId).single(),
    supabase.from('variants').select('*, products(name)').eq('id', removeId).single(),
  ]);

  const keepName = (keepVariant.data as any)?.products?.name ?? keepId;
  const removeName = (removeVariant.data as any)?.products?.name ?? removeId;
  const removeSku = (removeVariant.data as any)?.internal_sku ?? '';
  const removeProductId = (removeVariant.data as any)?.product_id;

  // Save undo snapshot in settings
  const snapshot = {
    keepId, removeId,
    keepName, removeName,
    removeSku,
    removeProductId,
    timestamp: new Date().toISOString(),
  };
  await supabase
    .from('settings')
    .upsert({ key: 'last_merge_snapshot', value: JSON.stringify(snapshot) }, { onConflict: 'key' });

  // ── Step 2: Move channel_listings from remove → keep ────────────────────
  // Fetch existing listings on BOTH variants to avoid duplicating a channel
  const { data: keepListings } = await supabase
    .from('channel_listings')
    .select('channel')
    .eq('variant_id', keepId);
  const keepChannels = new Set((keepListings || []).map((l: any) => l.channel));

  const { data: removeListings } = await supabase
    .from('channel_listings')
    .select('*')
    .eq('variant_id', removeId);

  for (const listing of (removeListings || [])) {
    if (keepChannels.has(listing.channel)) {
      // Kept variant already has a listing on this channel — just delete the duplicate
      await supabase.from('channel_listings').delete().eq('id', listing.id);
    } else {
      // Move listing to kept variant
      await supabase
        .from('channel_listings')
        .update({ variant_id: keepId, updated_at: new Date().toISOString() })
        .eq('id', listing.id);
      keepChannels.add(listing.channel);
    }
  }

  // ── Step 3: Set inventory on kept variant ────────────────────────────────
  const { data: keepInv } = await supabase
    .from('inventory')
    .select('id')
    .eq('variant_id', keepId)
    .single();

  if (keepInv) {
    await supabase
      .from('inventory')
      .update({ total_stock: stock, updated_at: new Date().toISOString() })
      .eq('id', keepInv.id);
  } else {
    // Create inventory row if missing
    const { data: keepVar } = await supabase
      .from('variants').select('product_id').eq('id', keepId).single();
    await supabase.from('inventory').insert({
      variant_id: keepId,
      product_id: (keepVar as any)?.product_id,
      total_stock: stock,
      reserved_stock: 0,
      low_stock_threshold: 5,
    });
  }

  // ── Step 4: Remove inventory for deleted variant ─────────────────────────
  await supabase.from('inventory').delete().eq('variant_id', removeId);

  // ── Step 5: Delete the removed variant (and parent product if empty) ─────
  await supabase.from('variants').delete().eq('id', removeId);
  if (removeProductId) {
    const { data: remaining } = await supabase
      .from('variants')
      .select('id')
      .eq('product_id', removeProductId);
    if (!remaining || remaining.length === 0) {
      await supabase.from('products').delete().eq('id', removeProductId);
    }
  }

  // ── Step 6: Mark kept variant needs_sync ────────────────────────────────
  await markVariantNeedsSync(keepId);

  // ── Step 7: Add removed SKU to merged_skus blocklist ─────────────────────
  if (removeSku) {
    const { data: raw } = await supabase
      .from('settings')
      .select('value')
      .eq('key', 'merged_skus')
      .single();
    let blocklist: string[] = [];
    try { blocklist = JSON.parse(raw?.value ?? '[]'); } catch { /* ignore */ }
    if (!blocklist.includes(removeSku)) {
      blocklist.push(removeSku);
      await supabase
        .from('settings')
        .upsert({ key: 'merged_skus', value: JSON.stringify(blocklist) }, { onConflict: 'key' });
    }
  }
}

// ─── Undo Merge ──────────────────────────────────────────────────────────────

export async function getLastMergeSnapshot(): Promise<{ removedName: string; keepName: string; timestamp: string } | null> {
  const { data } = await supabase
    .from('settings')
    .select('value')
    .eq('key', 'last_merge_snapshot')
    .single();
  if (!data?.value) return null;
  try {
    const snap = JSON.parse(data.value);
    if (!snap.removeId) return null;
    return { removedName: snap.removeName, keepName: snap.keepName, timestamp: snap.timestamp };
  } catch {
    return null;
  }
}

export async function undoLastMerge(): Promise<string> {
  const { data } = await supabase
    .from('settings')
    .select('value')
    .eq('key', 'last_merge_snapshot')
    .single();
  if (!data?.value) throw new Error('No merge snapshot to undo');

  const snap = JSON.parse(data.value);
  const { keepId, removeId, removeSku, removeProductId, removeName, keepName } = snap;

  // 1. Recreate the product group (if needed) and the removed variant
  let newProductId = removeProductId;
  const { data: existingProd } = await supabase
    .from('products')
    .select('id')
    .eq('id', removeProductId)
    .single();

  if (!existingProd) {
    // Product was deleted — recreate it
    const { data: newProd, error } = await supabase
      .from('products')
      .insert({ name: removeName, sku: removeSku, status: 'active', active: true })
      .select()
      .single();
    if (error) throw error;
    newProductId = newProd.id;
  }

  // 2. Recreate the variant
  const { error: varErr } = await supabase
    .from('variants')
    .insert({ id: removeId, product_id: newProductId, internal_sku: removeSku, needs_sync: false })
    .select()
    .single();
  if (varErr) throw varErr;

  // 3. Move back channel_listings that originated from removeId
  // We can't know exactly which ones came from it (they were moved to keepId),
  // so we just note this limitation in the toast — user can re-merge if needed.
  // The blocklist entry should be removed so reimport works.

  // 4. Remove removeSku from merged_skus blocklist
  if (removeSku) {
    const { data: raw } = await supabase
      .from('settings')
      .select('value')
      .eq('key', 'merged_skus')
      .single();
    let blocklist: string[] = [];
    try { blocklist = JSON.parse(raw?.value ?? '[]'); } catch { /* ignore */ }
    blocklist = blocklist.filter((s: string) => s !== removeSku);
    await supabase
      .from('settings')
      .upsert({ key: 'merged_skus', value: JSON.stringify(blocklist) }, { onConflict: 'key' });
  }

  // 5. Clear snapshot
  await supabase
    .from('settings')
    .upsert({ key: 'last_merge_snapshot', value: null }, { onConflict: 'key' });

  // Suppress unused variable warning
  void keepId; void keepName;

  return removeName;
}

// ─── Orders ──────────────────────────────────────────────────────────────────

export async function fetchOrders(platform?: string, limit = 2000): Promise<Order[]> {
  // Paginate to respect Supabase 1000-row cap
  const PAGE = 1000;
  let offset = 0;
  const all: Order[] = [];
  while (all.length < limit) {
    const end = Math.min(offset + PAGE - 1, limit - 1);
    let q = supabase
      .from('orders')
      .select('*')
      .order('ordered_at', { ascending: false })
      .range(offset, end);
    if (platform) q = q.eq('platform', platform);
    const { data, error } = await q;
    if (error) throw error;
    const rows = (data ?? []) as Order[];
    all.push(...rows);
    if (rows.length < PAGE) break;
    offset += PAGE;
  }
  return all;
}

export async function updateOrderTracking(
  orderId: string,
  trackingNumber: string,
  carrier: string
): Promise<void> {
  const { error } = await supabase
    .from('orders')
    .update({ tracking_number: trackingNumber, tracking_carrier: carrier })
    .eq('id', orderId);
  if (error) throw error;
}

// ─── Sync ────────────────────────────────────────────────────────────────────

export async function fetchSyncLogs(limit = 20): Promise<SyncLog[]> {
  const { data, error } = await supabase
    .from('sync_log')
    .select('*')
    .order('started_at', { ascending: false })
    .limit(limit);
  if (error) throw error;
  return data || [];
}

export async function fetchSetting(key: string): Promise<string | null> {
  const { data } = await supabase
    .from('settings')
    .select('value')
    .eq('key', key)
    .single();
  return data?.value ?? null;
}

export async function setSetting(key: string, value: string): Promise<void> {
  const { error } = await supabase
    .from('settings')
    .upsert({ key, value }, { onConflict: 'key' });
  if (error) throw error;
}

// ─── Orders ───────────────────────────────────────────────────────────────────

export async function updateOrder(id: string, updates: Partial<import('../types').Order>): Promise<void> {
  const { error } = await supabase
    .from('orders')
    .update(updates)
    .eq('id', id);
  if (error) throw error;
}

// ─── Split Product (undo grouping) ──────────────────────────────────────────

/**
 * Split a multi-variant product into individual single-variant products.
 * Each variant gets its own product row, named after the variant's current name.
 * All variants are marked needs_sync so the next sync picks them up.
 */
export async function splitProduct(productId: string): Promise<number> {
  // 1. Find all variants under this product
  const { data: variants, error: vErr } = await supabase
    .from('variants')
    .select('id, internal_sku, option1, option2, name')
    .eq('product_id', productId);
  if (vErr) throw vErr;
  if (!variants || variants.length <= 1) return 0; // nothing to split

  // 2. Get the parent product name
  const { data: prod, error: pErr } = await supabase
    .from('products')
    .select('name')
    .eq('id', productId)
    .single();
  if (pErr) throw pErr;
  const baseName = prod?.name ?? 'Product';

  let moved = 0;
  // 3. For each variant after the first, create a new product and re-parent
  for (let i = 1; i < variants.length; i++) {
    const v = variants[i];
    const variantLabel = v.option1 || v.option2 || v.internal_sku || `Variant ${i + 1}`;
    const newName = `${baseName} - ${variantLabel}`;

    // Create new product
    const { data: newProd, error: npErr } = await supabase
      .from('products')
      .insert({ name: newName, status: 'active', active: true })
      .select()
      .single();
    if (npErr) throw npErr;

    // Re-parent variant
    const { error: upErr } = await supabase
      .from('variants')
      .update({ product_id: newProd.id, needs_sync: true })
      .eq('id', v.id);
    if (upErr) throw upErr;

    moved++;
  }

  // 4. Mark the remaining first variant as needs_sync too
  if (variants.length > 0) {
    await supabase
      .from('variants')
      .update({ needs_sync: true })
      .eq('id', variants[0].id);
  }

  return moved;
}

// ─── Settings (array fetch + update) ─────────────────────────────────────────

export async function fetchSettings(): Promise<Setting[]> {
  const { data, error } = await supabase
    .from('settings')
    .select('key, value');
  if (error) throw error;
  return (data || []).map((row: any) => ({
    key: row.key,
    value: row.value ?? '',
  }));
}

export async function updateSetting(key: string, value: string): Promise<void> {
  const { error } = await supabase
    .from('settings')
    .upsert({ key, value }, { onConflict: 'key' });
  if (error) throw error;
}

// ─── Sales Trends ─────────────────────────────────────────────────────────────

export async function fetchSalesTrends(): Promise<SalesTrend[]> {
  // sales_trends table removed — Trends component derives data from orders directly
  return [];
}

// ─── Quick Sync (GitHub Actions workflow_dispatch) ────────────────────────────
//
// Reads the GitHub PAT from the settings table at click-time (never compiled
// into the bundle). User pastes their token once via Settings → GitHub Token.
// Token needs repo + workflow scope on github.com/settings/tokens.

const GITHUB_REPO = 'Voyagers-hook/inventory-sync';

export async function triggerQuickSync(): Promise<{ ok: boolean; error?: string }> {
  // Read token from Supabase settings at runtime
  const token = await fetchSetting('github_token');
  if (!token) {
    console.error('[triggerQuickSync] No github_token found in settings table');
    return { ok: false, error: 'no_token' };
  }

  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/sync-quick.yml/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main' }),
      }
    );
    if (res.ok || res.status === 204) {
      return { ok: true };
    }
    const body = await res.text().catch(() => '');
    console.error(`[triggerQuickSync] GitHub API ${res.status}:`, body);
    return { ok: false, error: `github_${res.status}` };
  } catch (err) {
    console.error('[triggerQuickSync] fetch error:', err);
    return { ok: false, error: 'network_error' };
  }
}
