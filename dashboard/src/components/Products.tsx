import React, { useState, useCallback, useEffect } from 'react';
import { Plus, Trash2, Search, RefreshCw, AlertTriangle, Link, Unlink, X, GitMerge, Undo2 } from 'lucide-react';
import type { Product, Inventory, Pricing } from '../types';
import {
  createProduct, createInventory, createPricing, deleteProduct,
  updateInventory, updatePricing, mergeProducts, updateProduct, undoLastMerge, getLastMergeSnapshot,
} from '../utils/supabase';

interface ProductsProps {
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  onRefresh: () => void;
  initialLowStockFilter?: boolean;
  onFilterApplied?: () => void;
}

// Helper: platforms listed for a product (product.id = variant_id; pricing.product_id = variant_id)
function getPlatforms(productId: string, pricing: Pricing[]) {
  return pricing.filter(p => p.product_id === productId).map(p => p.platform);
}

function isLinked(productId: string, pricing: Pricing[]): boolean {
  const platforms = getPlatforms(productId, pricing);
  return platforms.includes('squarespace') && platforms.includes('ebay');
}

// ─── Pricing Modal ───────────────────────────────────────────────────────────
const PricingModal: React.FC<{
  product: Product;
  inv?: Inventory;
  pricing: Pricing[];
  onClose: () => void;
  onSave: () => void;
}> = ({ product, inv, pricing, onClose, onSave }) => {

  // product.id = variant_id; pricing.product_id = variant_id — so lookup works directly
  const ssPricing = pricing.find(p => p.product_id === product.id && p.platform === 'squarespace');
  const ebPricing = pricing.find(p => p.product_id === product.id && p.platform === 'ebay');

  const [costPrice, setCostPrice] = useState(product.cost_price != null ? String(product.cost_price) : '');
  const [ssPrice, setSsPrice] = useState(ssPricing ? String(Number(ssPricing.price).toFixed(2)) : '');
  const [ebPrice, setEbPrice] = useState(ebPricing ? String(Number(ebPricing.price).toFixed(2)) : '');
  const [stockValue, setStockValue] = useState(inv ? String(inv.total_stock) : '0');
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState('');

  const cost = parseFloat(costPrice) || 0;
  const ssPriceNum = parseFloat(ssPrice) || 0;
  const ebPriceNum = parseFloat(ebPrice) || 0;
  const ssMargin = cost > 0 && ssPriceNum > 0 ? ((ssPriceNum - cost) / ssPriceNum * 100) : null;
  const ebMargin = cost > 0 && ebPriceNum > 0 ? ((ebPriceNum - cost) / ebPriceNum * 100) : null;

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      // Save cost price on parent product
      await updateProduct(product.id, { cost_price: costPrice !== '' ? parseFloat(costPrice) : null });

      // Save platform prices — marks needs_sync=true via updatePricing
      if (ssPricing && ssPrice !== '') await updatePricing(ssPricing.id, parseFloat(ssPrice));
      if (ebPricing && ebPrice !== '') await updatePricing(ebPricing.id, parseFloat(ebPrice));

      // Save stock — marks needs_sync=true via updateInventory
      const newStock = Math.max(0, parseInt(stockValue) || 0);
      if (inv && newStock !== inv.total_stock) {
        await updateInventory(product.id, { total_stock: newStock });
      }

      setToast('Saved! Changes will push to platforms on next sync (hourly, or click Sync Now).');
      setTimeout(() => { onSave(); onClose(); }, 2200);
    } catch {
      setToast('Failed to save — please try again.');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [product.id, costPrice, ssPricing, ssPrice, ebPricing, ebPrice, inv, stockValue, onSave, onClose]);

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-lg">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="font-bold text-lg">{product.name}</h3>
            <div className="text-xs text-base-content/50 font-mono mt-0.5">{product.sku}</div>
            {product.option1 && (
              <div className="text-xs text-base-content/60 mt-0.5">{product.option1}{product.option2 ? ` / ${product.option2}` : ''}</div>
            )}
          </div>
          <button className="btn btn-ghost btn-sm btn-circle" onClick={onClose}><X size={16} /></button>
        </div>

        {toast && (
          <div className="alert alert-info mb-4 text-sm py-2">
            <span>{toast}</span>
          </div>
        )}

        {/* Platform badges */}
        <div className="flex flex-wrap gap-1.5 mb-4">
          {ssPricing && <span className="badge badge-info badge-sm">Squarespace</span>}
          {ebPricing && <span className="badge badge-warning badge-sm">eBay</span>}
          {ssPricing && ebPricing && <span className="badge badge-success badge-sm">✓ Shared stock</span>}
          {product.needs_sync && <span className="badge badge-info badge-sm">⏳ Sync pending</span>}
        </div>

        {/* Stock */}
        <div className="form-control mb-3">
          <label className="label py-1">
            <span className="label-text font-medium">Stock Level</span>
            {inv && inv.total_stock <= (inv.low_stock_threshold || 5) && (
              <span className="label-text-alt text-warning flex items-center gap-1">
                <AlertTriangle size={12} /> Low stock
              </span>
            )}
          </label>
          <div className="input-group">
            <input
              type="number"
              className="input input-bordered w-full"
              value={stockValue}
              onChange={e => setStockValue(e.target.value)}
              step="1" min="0"
            />
            <span>units</span>
          </div>
          <label className="label py-1">
            <span className="label-text-alt text-base-content/50">
              Changing stock queues a sync to both Squarespace and eBay
            </span>
          </label>
        </div>

        {/* Cost Price */}
        <div className="form-control mb-3">
          <label className="label py-1">
            <span className="label-text font-medium">Cost Price</span>
          </label>
          <div className="input-group">
            <span>£</span>
            <input
              type="number"
              className="input input-bordered w-full"
              value={costPrice}
              onChange={e => setCostPrice(e.target.value)}
              step="0.01" min="0"
            />
          </div>
          <label className="label py-1">
            <span className="label-text-alt text-base-content/50">
              Used for margin calculations only — not synced to platforms
            </span>
          </label>
        </div>

        {/* Squarespace Price */}
        <div className="form-control mb-3">
          <label className="label py-1">
            <span className="label-text font-medium">Squarespace Price</span>
            {!ssPricing && <span className="label-text-alt text-base-content/40">not listed</span>}
          </label>
          <div className="input-group">
            <span>£</span>
            <input
              type="number"
              className="input input-bordered w-full"
              value={ssPrice}
              onChange={e => setSsPrice(e.target.value)}
              step="0.01" min="0"
              disabled={!ssPricing}
            />
          </div>
          {ssMargin !== null && (
            <label className="label py-1">
              <span className={`label-text-alt ${ssMargin > 0 ? 'text-success' : 'text-error'}`}>
                {ssMargin.toFixed(0)}% margin · £{(ssPriceNum - cost).toFixed(2)} profit
              </span>
            </label>
          )}
        </div>

        {/* eBay Price */}
        <div className="form-control mb-4">
          <label className="label py-1">
            <span className="label-text font-medium">eBay Price</span>
            {!ebPricing && <span className="label-text-alt text-base-content/40">not listed</span>}
          </label>
          <div className="input-group">
            <span>£</span>
            <input
              type="number"
              className="input input-bordered w-full"
              value={ebPrice}
              onChange={e => setEbPrice(e.target.value)}
              step="0.01" min="0"
              disabled={!ebPricing}
            />
          </div>
          {ebMargin !== null && (
            <label className="label py-1">
              <span className={`label-text-alt ${ebMargin > 0 ? 'text-success' : 'text-error'}`}>
                {ebMargin.toFixed(0)}% margin · £{(ebPriceNum - cost).toFixed(2)} profit
              </span>
            </label>
          )}
        </div>

        {/* Footer */}
        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save & Queue Sync'}
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

// ─── Merge Modal ─────────────────────────────────────────────────────────────
const MergeModal: React.FC<{
  productA: Product;
  productB: Product;
  inventory: Inventory[];
  pricing: Pricing[];
  onClose: () => void;
  onMerge: (keepId: string, removeId: string, stock: number) => void;
}> = ({ productA, productB, inventory, pricing, onClose, onMerge }) => {
  const [keepId, setKeepId] = useState(productA.id);
  const removeId = keepId === productA.id ? productB.id : productA.id;

  const keepProd = keepId === productA.id ? productA : productB;
  const removeProd = keepId === productA.id ? productB : productA;

  // inventory.product_id is normalised to variant_id by fetchInventory()
  const invA = inventory.find(i => i.product_id === productA.id);
  const invB = inventory.find(i => i.product_id === productB.id);
  const combinedStock = (invA?.total_stock ?? 0) + (invB?.total_stock ?? 0);
  const [stock, setStock] = useState(combinedStock);

  // pricing.product_id = variant_id
  const finalSS = pricing.find(p => p.product_id === keepId && p.platform === 'squarespace')
    || pricing.find(p => p.product_id === removeId && p.platform === 'squarespace');
  const finalEB = pricing.find(p => p.product_id === keepId && p.platform === 'ebay')
    || pricing.find(p => p.product_id === removeId && p.platform === 'ebay');

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-bold text-lg flex items-center gap-2">
            <GitMerge size={20} /> Merge Products
          </h3>
          <button className="btn btn-ghost btn-sm btn-circle" onClick={onClose}><X size={16} /></button>
        </div>

        <p className="text-sm text-base-content/70 mb-4">
          These two products will become <strong>one product</strong> with a single shared stock
          level. Both Squarespace and eBay prices are kept. Every sale on either platform reduces
          the same stock counter.
        </p>

        <div className="grid grid-cols-2 gap-3 mb-4">
          {[productA, productB].map(prod => {
            const isKeep = prod.id === keepId;
            const inv = inventory.find(i => i.product_id === prod.id);
            const ssPr = pricing.find(p => p.product_id === prod.id && p.platform === 'squarespace');
            const ebPr = pricing.find(p => p.product_id === prod.id && p.platform === 'ebay');
            return (
              <button
                key={prod.id}
                onClick={() => setKeepId(prod.id)}
                className={`rounded-xl border-2 p-3 text-left transition-all ${
                  isKeep ? 'border-primary bg-primary/5' : 'border-base-300 bg-base-100 opacity-70'
                }`}
              >
                <div className="text-xs font-semibold mb-1 text-base-content/50">
                  {isKeep ? '★ Keep as master' : 'Will be removed'}
                </div>
                <div className="font-medium text-sm leading-tight mb-1">{prod.name}</div>
                <div className="font-mono text-xs text-base-content/40 mb-2">{prod.sku}</div>
                <div className="flex flex-wrap gap-1">
                  {ssPr && <span className="badge badge-info badge-xs">SS £{Number(ssPr.price).toFixed(2)}</span>}
                  {ebPr && <span className="badge badge-warning badge-xs">eBay £{Number(ebPr.price).toFixed(2)}</span>}
                </div>
                <div className="text-xs text-base-content/50 mt-1">
                  Stock: <strong>{inv?.total_stock ?? 0}</strong>
                </div>
              </button>
            );
          })}
        </div>

        <div className="bg-base-200 rounded-xl p-3 mb-4">
          <div className="text-xs font-semibold text-base-content/50 mb-1">Result — merged product</div>
          <div className="font-medium">{keepProd.name}</div>
          <div className="font-mono text-xs text-base-content/40 mb-2">{keepProd.sku}</div>
          <div className="flex flex-wrap gap-1 mb-2">
            {finalSS && <span className="badge badge-info badge-sm">Squarespace £{Number(finalSS.price).toFixed(2)}</span>}
            {finalEB && <span className="badge badge-warning badge-sm">eBay £{Number(finalEB.price).toFixed(2)}</span>}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">Shared stock:</span>
            <input
              type="number"
              className="input input-bordered input-sm w-24"
              value={stock}
              onChange={e => setStock(Math.max(0, parseInt(e.target.value) || 0))}
              min={0}
            />
            <span className="text-xs text-base-content/50">(combined was {combinedStock})</span>
          </div>
        </div>

        <div className="alert alert-warning text-sm py-2 mb-4">
          <AlertTriangle size={16} />
          <span>
            You can undo this merge afterwards if needed.{' '}
            <strong>{removeProd.name}</strong> will be permanently deleted and its
            platform listings transferred to the master product.
          </span>
        </div>

        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary gap-2"
            onClick={() => onMerge(keepId, removeId, stock)}
          >
            <GitMerge size={16} />
            Merge into one product
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

// ─── Main Products Component ──────────────────────────────────────────────────
export const Products: React.FC<ProductsProps> = ({
  products, inventory, pricing, onRefresh, initialLowStockFilter, onFilterApplied,
}) => {
  const [search, setSearch] = useState('');
  const [showLowStockOnly, setShowLowStockOnly] = useState(!!initialLowStockFilter);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [lastMerge, setLastMerge] = useState<{ removedName: string; keepName: string; timestamp: string } | null>(null);
  const [newName, setNewName] = useState('');
  const [newSku, setNewSku] = useState('');
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());

  React.useEffect(() => {
    if (initialLowStockFilter) {
      setShowLowStockOnly(true);
      onFilterApplied?.();
    }
  }, [initialLowStockFilter, onFilterApplied]);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 4000); };

  // Filter products — uses LEFT JOIN so every product shows even if inventory/pricing missing
  const filtered = products.filter(p => {
    // inventory.product_id is normalised to variant_id (= p.id) in fetchInventory()
    const inv = inventory.find(i => i.product_id === p.id);
    const threshold = inv?.low_stock_threshold ?? 5;
    if (showLowStockOnly && (!inv || inv.total_stock > threshold)) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return p.name?.toLowerCase().includes(q) || p.sku?.toLowerCase().includes(q);
  });

  const toggleCheck = useCallback((id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setCheckedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const checkedProducts = products.filter(p => checkedIds.has(p.id));

  const handleAdd = useCallback(async () => {
    if (!newName.trim() || !newSku.trim()) return;
    setBusy(true);
    try {
      // createProduct returns Product where id = variant_id
      const prod = await createProduct({ name: newName.trim(), sku: newSku.trim(), description: '' });
      await createInventory({ product_id: prod.id, variant_id: prod.id, total_stock: 0, reserved_stock: 0, low_stock_threshold: 5 });
      setShowAdd(false);
      setNewName(''); setNewSku('');
      showToast('Product added!');
      onRefresh();
    } catch { showToast('Failed to add product'); }
    finally { setBusy(false); }
  }, [newName, newSku, onRefresh]);

  const handleDelete = useCallback(async (id: string, name: string) => {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await deleteProduct(id);  // id = variant_id
      setCheckedIds(prev => { const n = new Set(prev); n.delete(id); return n; });
      showToast(`Deleted ${name}`);
      onRefresh();
    } catch { showToast('Failed to delete'); }
    finally { setBusy(false); }
  }, [onRefresh]);

  const handleBulkDelete = useCallback(async () => {
    const count = checkedIds.size;
    if (!confirm(`Delete ${count} selected product${count > 1 ? 's' : ''}? This cannot be undone.`)) return;
    setBusy(true);
    try {
      for (const id of checkedIds) {
        await deleteProduct(id);
      }
      setCheckedIds(new Set());
      showToast(`Deleted ${count} product${count > 1 ? 's' : ''}`);
      onRefresh();
    } catch { showToast('Failed to delete some products'); }
    finally { setBusy(false); }
  }, [checkedIds, onRefresh]);

  const handleMerge = useCallback(async (keepId: string, removeId: string, stock: number) => {
    setBusy(true);
    try {
      await mergeProducts(keepId, removeId, stock);
      setShowMerge(false);
      setCheckedIds(new Set());
      showToast('Products merged! Stock + price update queued for both platforms.');
      onRefresh();
    } catch { showToast('Failed to merge — please try again.'); }
    finally { setBusy(false); }
  }, [onRefresh]);

  useEffect(() => {
    getLastMergeSnapshot().then(setLastMerge).catch(() => setLastMerge(null));
  }, [products]);

  const handleUndoMerge = useCallback(async () => {
    if (!confirm('Undo the last merge? This will restore the deleted product and separate the two products.')) return;
    setBusy(true);
    try {
      const name = await undoLastMerge();
      setLastMerge(null);
      showToast(`Merge undone! "${name}" has been restored.`);
      onRefresh();
    } catch (e: any) { showToast('Failed to undo merge: ' + (e.message || e)); }
    finally { setBusy(false); }
  }, [onRefresh]);

  return (
    <div className="space-y-4">
      {toast && (
        <div className="alert alert-success py-2 text-sm">
          <span>✓ {toast}</span>
        </div>
      )}

      {lastMerge && (
        <div className="alert alert-info py-2 text-sm flex items-center justify-between gap-2">
          <span>
            <strong>Last merge:</strong> &ldquo;{lastMerge.removedName}&rdquo; merged into &ldquo;{lastMerge.keepName}&rdquo;
          </span>
          <div className="flex items-center gap-1 flex-shrink-0">
            <button className="btn btn-ghost btn-xs gap-1" onClick={handleUndoMerge} disabled={busy}>
              <Undo2 size={12} /> Undo
            </button>
            <button
              className="btn btn-ghost btn-xs btn-circle"
              onClick={() => setLastMerge(null)}
              title="Dismiss"
            >
              <X size={12} />
            </button>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap gap-2">
        <div className="input-group flex-1 min-w-48">
          <span><Search size={16} /></span>
          <input
            className="input input-bordered w-full"
            placeholder="Search name or SKU…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <button
          className={`btn btn-sm ${showLowStockOnly ? 'btn-warning' : 'btn-ghost'}`}
          onClick={() => setShowLowStockOnly(v => !v)}
        >
          {showLowStockOnly ? 'Low Stock Only' : 'Low Stock'}
        </button>
        <button className="btn btn-sm btn-primary gap-1" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> Add
        </button>
      </div>

      <div className="text-xs text-base-content/50">
        {filtered.length} of {products.length} products · Click a row to edit prices &amp; stock · Tick checkboxes to merge duplicates
      </div>

      {/* Product table */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-base-content/40">
          {products.length === 0
            ? 'No products yet — run a sync to import.'
            : 'No products match your search.'}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-base-200">
          <table className="table table-sm w-full">
            <thead>
              <tr className="bg-base-200 text-xs">
                <th className="w-8">
                  <input
                    type="checkbox"
                    className={`checkbox checkbox-xs ${
                      checkedIds.size > 0 ? 'checkbox-primary' : ''
                    }`}
                    checked={checkedIds.size === filtered.length && filtered.length > 0}
                    onChange={() => {
                      if (checkedIds.size === filtered.length) {
                        setCheckedIds(new Set());
                      } else {
                        setCheckedIds(new Set(filtered.map(p => p.id)));
                      }
                    }}
                  />
                </th>
                <th>Product</th>
                <th>SKU</th>
                <th>Platforms</th>
                <th>Stock</th>
                <th>SS Price</th>
                <th>eBay Price</th>
                <th className="w-8"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(p => {
                // inventory.product_id normalised to variant_id in fetchInventory()
                const inv = inventory.find(i => i.product_id === p.id);
                // pricing.product_id = variant_id = p.id
                const ssPr = pricing.find(pr => pr.product_id === p.id && pr.platform === 'squarespace');
                const ebPr = pricing.find(pr => pr.product_id === p.id && pr.platform === 'ebay');
                const linked = isLinked(p.id, pricing);
                const lowStock = inv && inv.total_stock <= (inv.low_stock_threshold || 5);
                const isChecked = checkedIds.has(p.id);

                return (
                  <tr
                    key={p.id}
                    className={`cursor-pointer hover:bg-base-200 transition-colors ${isChecked ? 'bg-primary/5' : ''}`}
                    onClick={() => setSelectedProduct(p)}
                  >
                    <td onClick={e => toggleCheck(p.id, e)} className="cursor-pointer">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-xs checkbox-primary pointer-events-none"
                        checked={isChecked}
                        onChange={() => {}}
                      />
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        {linked
                          ? <Link size={12} className="text-success flex-shrink-0" />
                          : <Unlink size={12} className="text-base-content/20 flex-shrink-0" />
                        }
                        <span className="font-medium text-sm leading-tight">{p.name}</span>
                      </div>
                      {p.option1 && (
                        <div className="text-xs text-base-content/50 ml-4 mt-0.5">{p.option1}{p.option2 ? ` / ${p.option2}` : ''}</div>
                      )}
                    </td>
                    <td className="font-mono text-xs text-base-content/50">{p.sku}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {ssPr && <span className="badge badge-info badge-xs">SS</span>}
                        {ebPr && <span className="badge badge-warning badge-xs">EB</span>}
                        {!ssPr && !ebPr && <span className="text-base-content/30 text-xs">—</span>}
                      </div>
                    </td>
                    <td>
                      <span className={lowStock ? 'text-warning font-medium' : ''}>
                        {inv?.total_stock ?? 0}
                      </span>
                      {lowStock && <AlertTriangle size={12} className="inline ml-1 text-warning" />}
                      {p.needs_sync && <span className="text-xs text-info ml-1" title="Sync pending">⏳</span>}
                    </td>
                    <td className="text-sm">{ssPr ? `£${Number(ssPr.price).toFixed(2)}` : <span className="text-base-content/30">—</span>}</td>
                    <td className="text-sm">{ebPr ? `£${Number(ebPr.price).toFixed(2)}` : <span className="text-base-content/30">—</span>}</td>
                    <td onClick={e => e.stopPropagation()}>
                      <button
                        className="btn btn-ghost btn-xs btn-circle text-error hover:bg-error/10"
                        onClick={() => handleDelete(p.id, p.name)}
                        disabled={busy}
                        title="Delete"
                      >
                        <Trash2 size={12} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Product Modal */}
      {showAdd && (
        <div className="modal modal-open">
          <div className="modal-box max-w-sm">
            <h3 className="font-bold text-lg mb-4">Add Product</h3>
            <div className="form-control mb-3">
              <label className="label py-1"><span className="label-text">Product Name</span></label>
              <input
                className="input input-bordered"
                placeholder="e.g. Blue Widget"
                value={newName}
                onChange={e => setNewName(e.target.value)}
              />
            </div>
            <div className="form-control mb-4">
              <label className="label py-1"><span className="label-text">SKU</span></label>
              <input
                className="input input-bordered font-mono"
                placeholder="e.g. BW-001"
                value={newSku}
                onChange={e => setNewSku(e.target.value)}
              />
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowAdd(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleAdd} disabled={busy || !newName.trim() || !newSku.trim()}>
                Add Product
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowAdd(false)} />
        </div>
      )}

      {/* Pricing / Stock Modal */}
      {selectedProduct && (
        <PricingModal
          product={selectedProduct}
          inv={inventory.find(i => i.product_id === selectedProduct.id)}
          pricing={pricing}
          onClose={() => setSelectedProduct(null)}
          onSave={onRefresh}
        />
      )}

      {/* Merge Modal */}
      {showMerge && checkedProducts.length === 2 && (
        <MergeModal
          productA={checkedProducts[0]}
          productB={checkedProducts[1]}
          inventory={inventory}
          pricing={pricing}
          onClose={() => setShowMerge(false)}
          onMerge={handleMerge}
        />
      )}

      {/* Floating selection bar */}
      {checkedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-base-100 border border-base-300 shadow-xl rounded-full px-4 py-2 flex items-center gap-3">
          <span className="text-sm font-medium">{checkedIds.size} selected</span>
          <button
            className="btn btn-error btn-sm rounded-full"
            onClick={handleBulkDelete}
            disabled={busy}
          >
            Delete{checkedIds.size > 1 ? ` (${checkedIds.size})` : ''}
          </button>
          {checkedIds.size === 2 && (
            <button
              className="btn btn-primary btn-sm rounded-full gap-1"
              onClick={() => setShowMerge(true)}
              disabled={busy}
            >
              <GitMerge size={14} /> Merge
            </button>
          )}
          <button
            className="btn btn-ghost btn-sm btn-circle"
            onClick={() => setCheckedIds(new Set())}
            title="Clear selection"
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
};
