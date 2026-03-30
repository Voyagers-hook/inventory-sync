import React, { useState, useCallback } from 'react';
import { Plus, Trash2, Search, RefreshCw, AlertTriangle, Link, Unlink, X, GitMerge, Undo2 } from 'lucide-react';
import type { Product, Inventory, Pricing } from '../types';
import {
  createProduct, createInventory, createPricing, deleteProduct,
  updateInventory, updatePricing, mergeProducts, updateProduct, undoLastMerge, getLastMergeSnapshot
} from '../utils/supabase';

interface ProductsProps {
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  onRefresh: () => void;
  initialLowStockFilter?: boolean;
  onFilterApplied?: () => void;
}

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
      // Save cost price
      await updateProduct(product.id, { cost_price: costPrice !== '' ? parseFloat(costPrice) : null });

      // Save platform prices (queues price sync)
      if (ssPricing && ssPrice !== '') await updatePricing(ssPricing.id, parseFloat(ssPrice));
      if (ebPricing && ebPrice !== '') await updatePricing(ebPricing.id, parseFloat(ebPrice));

      // Save stock (also queues a push to both platforms via supabase.ts)
      const newStock = Math.max(0, parseInt(stockValue) || 0);
      if (inv && newStock !== inv.total_stock) {
        await updateInventory(product.id, { total_stock: newStock });
      }

      setToast('Saved! Changes will push to platforms on next sync (or click Sync Now).');
      setTimeout(() => { onSave(); onClose(); }, 2000);
    } catch {
      setToast('Failed to save — please try again.');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [product.id, costPrice, ssPricing, ssPrice, ebPricing, ebPrice, inv, stockValue, onSave, onClose]);

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-md p-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-start justify-between p-5 border-b border-base-200">
          <div>
            <h3 className="font-bold text-base text-base-content">{product.name}</h3>
            <p className="text-xs text-base-content/40 font-mono mt-0.5">{product.sku}</p>
          </div>
          <button className="btn btn-ghost btn-sm btn-circle -mr-1 -mt-1" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="p-5 flex flex-col gap-4">
          {toast && (
            <div className={`rounded-xl px-4 py-3 text-sm font-medium ${toast.includes('Failed') ? 'bg-error/10 text-error border border-error/20' : 'bg-success/10 text-success border border-success/20'}`}>
              {toast}
            </div>
          )}

          {/* Platform badges */}
          <div className="flex items-center gap-2">
            {ssPricing && <span className="inline-flex px-2 py-1 rounded-lg text-xs font-semibold bg-indigo-50 text-indigo-600 border border-indigo-100">Squarespace</span>}
            {ebPricing && <span className="inline-flex px-2 py-1 rounded-lg text-xs font-semibold bg-yellow-50 text-yellow-600 border border-yellow-100">eBay</span>}
            {ssPricing && ebPricing && <span className="text-xs text-success font-medium ml-1">✓ Shared stock</span>}
          </div>

          {/* Stock — editable */}
          <div>
            <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wide block mb-2">
              Stock Level
              {inv && inv.total_stock <= (inv.low_stock_threshold || 5) && (
                <span className="ml-2 text-orange-500 font-normal normal-case">⚠ Low stock</span>
              )}
            </label>
            <div className="flex items-center gap-2 bg-base-200 rounded-xl px-4 py-3">
              <input
                type="number"
                className="input input-ghost flex-1 p-0 h-auto text-base font-bold focus:outline-none bg-transparent"
                placeholder="0"
                value={stockValue}
                onChange={e => setStockValue(e.target.value)}
                step="1"
                min="0"
              />
              <span className="text-base-content/40 text-sm">units</span>
            </div>
            <p className="text-xs text-base-content/30 mt-1 ml-1">Changing stock queues an update to both Squarespace and eBay</p>
          </div>

          {/* Cost Price */}
          <div>
            <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wide block mb-2">Cost Price</label>
            <div className="flex items-center gap-2 bg-base-200 rounded-xl px-4 py-3">
              <span className="text-base-content/50 font-medium">£</span>
              <input
                type="number"
                className="input input-ghost flex-1 p-0 h-auto text-base font-medium focus:outline-none bg-transparent"
                placeholder="0.00"
                value={costPrice}
                onChange={e => setCostPrice(e.target.value)}
                step="0.01"
                min="0"
              />
            </div>
            <p className="text-xs text-base-content/30 mt-1 ml-1">Used for margin calculations only — not synced to platforms</p>
          </div>

          {/* Squarespace Price */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wide">Squarespace Price</label>
              {!ssPricing && <span className="text-xs text-base-content/30 italic">not listed</span>}
            </div>
            <div className={`flex items-center gap-2 rounded-xl px-4 py-3 border ${ssPricing ? 'bg-indigo-50 border-indigo-100' : 'bg-base-200 border-base-300 opacity-50'}`}>
              <span className="text-indigo-400 font-medium">£</span>
              <input
                type="number"
                className="input input-ghost flex-1 p-0 h-auto text-base font-medium focus:outline-none bg-transparent text-indigo-800"
                placeholder={ssPricing ? "0.00" : "Not listed on Squarespace"}
                value={ssPrice}
                onChange={e => setSsPrice(e.target.value)}
                step="0.01"
                min="0"
                disabled={!ssPricing}
              />
            </div>
            {ssMargin !== null && (
              <div className="flex gap-3 mt-1.5 ml-1">
                <span className={`text-xs font-semibold ${ssMargin > 0 ? 'text-success' : 'text-error'}`}>{ssMargin.toFixed(0)}% margin</span>
                <span className="text-xs text-base-content/40">£{(ssPriceNum - cost).toFixed(2)} profit</span>
              </div>
            )}
          </div>

          {/* eBay Price */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wide">eBay Price</label>
              {!ebPricing && <span className="text-xs text-base-content/30 italic">not listed</span>}
            </div>
            <div className={`flex items-center gap-2 rounded-xl px-4 py-3 border ${ebPricing ? 'bg-yellow-50 border-yellow-100' : 'bg-base-200 border-base-300 opacity-50'}`}>
              <span className="text-yellow-500 font-medium">£</span>
              <input
                type="number"
                className="input input-ghost flex-1 p-0 h-auto text-base font-medium focus:outline-none bg-transparent text-yellow-800"
                placeholder={ebPricing ? "0.00" : "Not listed on eBay"}
                value={ebPrice}
                onChange={e => setEbPrice(e.target.value)}
                step="0.01"
                min="0"
                disabled={!ebPricing}
              />
            </div>
            {ebMargin !== null && (
              <div className="flex gap-3 mt-1.5 ml-1">
                <span className={`text-xs font-semibold ${ebMargin > 0 ? 'text-success' : 'text-error'}`}>{ebMargin.toFixed(0)}% margin</span>
                <span className="text-xs text-base-content/40">£{(ebPriceNum - cost).toFixed(2)} profit</span>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-base-200 bg-base-200/40">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary btn-sm gap-2" onClick={handleSave} disabled={saving}>
            <RefreshCw size={13} className={saving ? 'animate-spin' : ''} />
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

  const invA = inventory.find(i => i.product_id === productA.id);
  const invB = inventory.find(i => i.product_id === productB.id);
  const combinedStock = (invA?.total_stock ?? 0) + (invB?.total_stock ?? 0);
  const [stock, setStock] = useState(combinedStock);

  const finalSS = pricing.find(p => p.product_id === keepId && p.platform === 'squarespace')
    || pricing.find(p => p.product_id === removeId && p.platform === 'squarespace');
  const finalEB = pricing.find(p => p.product_id === keepId && p.platform === 'ebay')
    || pricing.find(p => p.product_id === removeId && p.platform === 'ebay');

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-xl p-0 overflow-hidden">
        <div className="flex items-center justify-between p-5 border-b border-base-200">
          <div className="flex items-center gap-2">
            <GitMerge size={18} className="text-primary" />
            <h3 className="font-bold text-base">Merge Products</h3>
          </div>
          <button className="btn btn-ghost btn-sm btn-circle" onClick={onClose}><X size={16} /></button>
        </div>

        <div className="p-5 flex flex-col gap-5">
          <p className="text-sm text-base-content/60">
            These two products will become <strong>one product</strong> with a single shared stock level. Both Squarespace and eBay prices are kept. Every sale on either platform reduces the same stock counter.
          </p>

          <div className="grid grid-cols-2 gap-3">
            {[productA, productB].map(prod => {
              const isKeep = prod.id === keepId;
              const inv = inventory.find(i => i.product_id === prod.id);
              const ssPr = pricing.find(p => p.product_id === prod.id && p.platform === 'squarespace');
              const ebPr = pricing.find(p => p.product_id === prod.id && p.platform === 'ebay');
              return (
                <button
                  key={prod.id}
                  onClick={() => setKeepId(prod.id)}
                  className={`rounded-xl border-2 p-3 text-left transition-all ${isKeep ? 'border-primary bg-primary/5' : 'border-base-300 bg-base-100 opacity-70'}`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className={`text-xs font-bold uppercase tracking-wide ${isKeep ? 'text-primary' : 'text-base-content/40'}`}>
                      {isKeep ? '★ Keep as master' : 'Will be removed'}
                    </span>
                  </div>
                  <p className="font-semibold text-sm text-base-content leading-tight mb-1">{prod.name}</p>
                  <p className="font-mono text-xs text-base-content/40 mb-2">{prod.sku}</p>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {ssPr && <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">SS £{Number(ssPr.price).toFixed(2)}</span>}
                    {ebPr && <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-yellow-50 text-yellow-600">eBay £{Number(ebPr.price).toFixed(2)}</span>}
                  </div>
                  <p className="text-xs text-base-content/50">Stock: <strong>{inv?.total_stock ?? 0}</strong></p>
                </button>
              );
            })}
          </div>

          <div className="bg-base-200 rounded-xl p-4">
            <p className="text-xs font-semibold text-base-content/50 uppercase tracking-wide mb-3">Result — merged product</p>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-base-content">{keepProd.name}</span>
                <span className="font-mono text-xs text-base-content/40">{keepProd.sku}</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {finalSS && <span className="px-2 py-1 rounded-lg text-xs font-semibold bg-indigo-50 text-indigo-600 border border-indigo-100">Squarespace £{Number(finalSS.price).toFixed(2)}</span>}
                {finalEB && <span className="px-2 py-1 rounded-lg text-xs font-semibold bg-yellow-50 text-yellow-600 border border-yellow-100">eBay £{Number(finalEB.price).toFixed(2)}</span>}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm text-base-content/60">Shared stock:</span>
                <input
                  type="number"
                  className="input input-bordered input-sm w-24 font-bold"
                  value={stock}
                  onChange={e => setStock(Math.max(0, parseInt(e.target.value) || 0))}
                  min={0}
                />
                <span className="text-xs text-base-content/40">(combined was {combinedStock})</span>
              </div>
            </div>
          </div>

          <p className="text-xs text-base-content/40 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            ⚠️ You can undo this merge afterwards if needed. <strong>{removeProd.name}</strong> will be permanently deleted and its platform listings transferred to the master product.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-base-200 bg-base-200/40">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary btn-sm gap-2"
            onClick={() => onMerge(keepId, removeId, stock)}
          >
            <GitMerge size={14} />
            Merge into one product
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

// ─── Main Products Component ──────────────────────────────────────────────────
export const Products: React.FC<ProductsProps> = ({ products, inventory, pricing, onRefresh, initialLowStockFilter, onFilterApplied }) => {
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

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const filtered = products.filter(p => {
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
      else if (next.size < 2) next.add(id);
      else {
        const [first] = next;
        next.delete(first);
        next.add(id);
      }
      return next;
    });
  }, []);

  const checkedProducts = products.filter(p => checkedIds.has(p.id));

  const handleAdd = useCallback(async () => {
    if (!newName.trim() || !newSku.trim()) return;
    setBusy(true);
    try {
      const prod = await createProduct({ name: newName.trim(), sku: newSku.trim(), description: '' });
      await createInventory({ product_id: prod.id, total_stock: 0, reserved_stock: 0, low_stock_threshold: 5 });
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
      await deleteProduct(id);
      setCheckedIds(prev => { const n = new Set(prev); n.delete(id); return n; });
      showToast(`Deleted ${name}`);
      onRefresh();
    } catch { showToast('Failed to delete'); }
    finally { setBusy(false); }
  }, [onRefresh]);

  const handleMerge = useCallback(async (keepId: string, removeId: string, stock: number) => {
    setBusy(true);
    try {
      await mergeProducts(keepId, removeId, stock);
      setShowMerge(false);
      setCheckedIds(new Set());
      showToast('Products merged! Stock update queued for both platforms.');
      onRefresh();
    } catch { showToast('Failed to merge — please try again.'); }
    finally { setBusy(false); }
  }, [onRefresh]);

  // Check for undoable merge on mount and after merges
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
    <div className="flex flex-col gap-4">
      {toast && (
        <div className="bg-success/10 border border-success/30 text-success rounded-xl px-4 py-3 text-sm font-medium">✓ {toast}</div>
      )}

      {lastMerge && (
        <div className="bg-warning/10 border border-warning/30 rounded-xl px-4 py-3 flex items-center justify-between">
          <span className="text-sm text-warning-content">
            <strong>Last merge:</strong> "{lastMerge.removedName}" was merged into "{lastMerge.keepName}"
          </span>
          <button
            className="btn btn-warning btn-sm gap-1.5"
            onClick={handleUndoMerge}
            disabled={busy}
          >
            <Undo2 size={14} /> Undo merge
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="flex items-center gap-2 bg-base-100 border border-base-300 rounded-xl px-3 py-2 flex-1 min-w-[180px] shadow-sm">
          <Search size={14} className="text-base-content/30 flex-shrink-0" />
          <input
            type="search"
            className="bg-transparent outline-none text-sm flex-1 text-base-content placeholder:text-base-content/30"
            placeholder="Search products or SKU..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <button
          className={`btn btn-sm gap-1.5 ${showLowStockOnly ? 'btn-error' : 'btn-ghost border border-base-300'}`}
          onClick={() => setShowLowStockOnly(v => !v)}
        >
          <AlertTriangle size={14} /> {showLowStockOnly ? 'Low Stock Only' : 'Low Stock'}
        </button>
        <button className="btn btn-primary btn-sm gap-1.5" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> Add
        </button>

      </div>

      <p className="text-xs text-base-content/40">
        {filtered.length} of {products.length} products · Click a row to edit prices &amp; stock · Tick checkboxes to merge duplicates
      </p>

      {filtered.length === 0 ? (
        <div className="bg-base-100 rounded-xl border border-base-300 p-12 text-center shadow-sm">
          <p className="text-base-content/40 text-sm">{products.length === 0 ? 'No products yet — run a sync to import.' : 'No products match your search.'}</p>
        </div>
      ) : (
        <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="table table-sm w-full">
              <thead>
                <tr className="bg-base-200/60 text-base-content/50 text-xs uppercase tracking-wide">
                  <th className="w-8">
                    {checkedIds.size > 0 && (
                      <button className="btn btn-ghost btn-xs" onClick={() => setCheckedIds(new Set())} title="Clear selection">
                        <X size={12} />
                      </button>
                    )}
                  </th>
                  <th className="font-semibold">Product</th>
                  <th className="font-semibold hidden sm:table-cell">SKU</th>
                  <th className="font-semibold text-center">Platforms</th>
                  <th className="font-semibold text-right">Stock</th>
                  <th className="font-semibold text-right hidden md:table-cell">SS Price</th>
                  <th className="font-semibold text-right hidden md:table-cell">eBay Price</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(p => {
                  const inv = inventory.find(i => i.product_id === p.id);
                  const ssPr = pricing.find(pr => pr.product_id === p.id && pr.platform === 'squarespace');
                  const ebPr = pricing.find(pr => pr.product_id === p.id && pr.platform === 'ebay');
                  const linked = isLinked(p.id, pricing);
                  const lowStock = inv && inv.total_stock <= (inv.low_stock_threshold || 5);
                  const isChecked = checkedIds.has(p.id);

                  return (
                    <tr
                      key={p.id}
                      className={`hover:bg-base-200/50 cursor-pointer transition-colors ${lowStock ? 'bg-orange-50/30' : ''} ${isChecked ? 'bg-primary/5 ring-1 ring-inset ring-primary/20' : ''}`}
                      onClick={() => setSelectedProduct(p)}
                    >
                      <td onClick={e => toggleCheck(p.id, e)} className="cursor-pointer">
                        <div className={`w-4 h-4 rounded border-2 flex items-center justify-center transition-all ${isChecked ? 'bg-primary border-primary' : 'border-base-300 hover:border-primary'}`}>
                          {isChecked && <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                        </div>
                      </td>
                      <td>
                        <div className="flex items-center gap-2">
                          {linked
                            ? <Link size={11} className="text-success flex-shrink-0" />
                            : <Unlink size={11} className="text-base-content/20 flex-shrink-0" />
                          }
                          <span className="text-sm font-medium text-base-content">{p.name}</span>
                        </div>
                      </td>
                      <td className="hidden sm:table-cell whitespace-nowrap">
                        <span className="font-mono text-xs text-base-content/50">{p.sku}</span>
                      </td>
                      <td className="text-center whitespace-nowrap">
                        <div className="flex justify-center gap-1">
                          {ssPr && <span className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">SS</span>}
                          {ebPr && <span className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-yellow-50 text-yellow-600">EB</span>}
                          {!ssPr && !ebPr && <span className="text-xs text-base-content/20">—</span>}
                        </div>
                      </td>
                      <td className="text-right whitespace-nowrap">
                        <span className={`text-sm font-semibold ${lowStock ? 'text-orange-500' : 'text-base-content'}`}>
                          {inv?.total_stock ?? 0}
                          {lowStock && <AlertTriangle size={10} className="inline ml-1 text-orange-400" />}
                        </span>
                      </td>
                      <td className="text-right hidden md:table-cell whitespace-nowrap">
                        <span className="text-sm text-base-content/70">
                          {ssPr ? `£${Number(ssPr.price).toFixed(2)}` : <span className="text-base-content/20">—</span>}
                        </span>
                      </td>
                      <td className="text-right hidden md:table-cell whitespace-nowrap">
                        <span className="text-sm text-base-content/70">
                          {ebPr ? `£${Number(ebPr.price).toFixed(2)}` : <span className="text-base-content/20">—</span>}
                        </span>
                      </td>
                      <td onClick={e => e.stopPropagation()}>
                        <button
                          className="btn btn-ghost btn-xs text-error opacity-40 hover:opacity-100"
                          onClick={() => handleDelete(p.id, p.name)}
                          disabled={busy}
                          title="Delete"
                        >
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Add Product Modal */}
      {showAdd && (
        <div className="modal modal-open">
          <div className="modal-box max-w-sm">
            <h3 className="font-bold text-lg mb-4">Add Product</h3>
            <div className="flex flex-col gap-3">
              <input className="input input-bordered" placeholder="Product name" value={newName} onChange={e => setNewName(e.target.value)} />
              <input className="input input-bordered" placeholder="SKU" value={newSku} onChange={e => setNewSku(e.target.value)} />
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowAdd(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleAdd} disabled={busy || !newName.trim() || !newSku.trim()}>Add Product</button>
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

      {/* Floating merge bar — appears whenever items are checked */}
      {checkedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-white border border-base-300 shadow-2xl rounded-2xl px-5 py-3 transition-all">
          <div className="flex items-center gap-2 text-sm font-medium text-base-content/70">
            <div className="flex gap-1">
              {checkedProducts.map(p => (
                <span key={p.id} className="inline-flex items-center gap-1 bg-primary/10 text-primary text-xs font-semibold rounded-lg px-2 py-1">
                  {p.name.length > 22 ? p.name.slice(0, 22) + '…' : p.name}
                  <button onClick={(e) => { e.stopPropagation(); toggleCheck(p.id, e as any); }} className="ml-0.5 hover:text-error transition-colors">
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          </div>
          {checkedIds.size === 1 && (
            <span className="text-xs text-base-content/40 border-l border-base-300 pl-3">Tick one more to merge</span>
          )}
          {checkedIds.size === 2 && (
            <button
              className="btn btn-primary btn-sm gap-1.5 border-l border-base-300 pl-3 rounded-l-none -mr-2 pr-4"
              onClick={() => setShowMerge(true)}
            >
              <GitMerge size={14} /> Merge into one
            </button>
          )}
          <button
            className="btn btn-ghost btn-xs text-base-content/40 hover:text-error"
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

