import React, { useState, useCallback } from 'react';
import { Plus, Trash2, Search, RefreshCw, Edit3, AlertTriangle, Link, Unlink, X } from 'lucide-react';
import type { Product, Inventory, Pricing } from '../types';
import {
  createProduct, createInventory, createPricing, deleteProduct,
  updateInventory, updatePricing, mergeProducts, updateProduct
} from '../utils/supabase';

interface ProductsProps {
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  onRefresh: () => void;
}

function isLinked(productId: string, pricing: Pricing[]): boolean {
  const platforms = pricing.filter(p => p.product_id === productId).map(p => p.platform);
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
      await updateProduct(product.id, { cost_price: costPrice !== '' ? parseFloat(costPrice) : null });
      if (ssPricing && ssPrice !== '') await updatePricing(ssPricing.id, parseFloat(ssPrice));
      if (ebPricing && ebPrice !== '') await updatePricing(ebPricing.id, parseFloat(ebPrice));
      setToast('Saved! Prices will sync to platforms on next run.');
      setTimeout(() => { onSave(); onClose(); }, 1800);
    } catch {
      setToast('Failed to save — please try again.');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [product.id, costPrice, ssPricing, ssPrice, ebPricing, ebPrice, onSave, onClose]);

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

          {/* Stock info */}
          {inv && (
            <div className="bg-base-200 rounded-xl px-4 py-3 flex items-center justify-between">
              <span className="text-sm text-base-content/60">Current Stock</span>
              <span className={`text-sm font-bold ${inv.total_stock <= (inv.low_stock_threshold || 5) ? 'text-error' : 'text-base-content'}`}>
                {inv.total_stock} units
                {inv.total_stock <= (inv.low_stock_threshold || 5) && <AlertTriangle size={12} className="inline ml-1" />}
              </span>
            </div>
          )}

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
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  onClose: () => void;
  onMerge: (keepId: string, removeId: string, stock: number) => void;
}> = ({ products, inventory, pricing, onClose, onMerge }) => {
  const unlinked = products.filter(p => !isLinked(p.id, pricing));
  const [keep, setKeep] = useState('');
  const [remove, setRemove] = useState('');
  const [stock, setStock] = useState(0);

  const keepProd = products.find(p => p.id === keep);
  const removeProd = products.find(p => p.id === remove);

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-lg">
        <h3 className="font-bold text-lg mb-4">Merge Products</h3>
        <p className="text-sm text-base-content/60 mb-4">Combine two products into one shared stock record while keeping platform-specific listings.</p>
        <div className="flex flex-col gap-3">
          <div>
            <label className="text-sm font-semibold mb-1 block">Keep this product</label>
            <select className="select select-bordered w-full" value={keep} onChange={e => { setKeep(e.target.value); setStock(inventory.find(i => i.product_id === e.target.value)?.total_stock || 0); }}>
              <option value="">Select product to keep...</option>
              {unlinked.filter(p => p.id !== remove).map(p => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
            </select>
          </div>
          <div>
            <label className="text-sm font-semibold mb-1 block">Remove & merge from</label>
            <select className="select select-bordered w-full" value={remove} onChange={e => setRemove(e.target.value)}>
              <option value="">Select product to remove...</option>
              {unlinked.filter(p => p.id !== keep).map(p => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
            </select>
          </div>
          {keepProd && removeProd && (
            <div className="bg-base-200 rounded-xl p-3">
              <div className="grid grid-cols-2 gap-2 text-sm mb-3">
                <div className="bg-base-100 rounded-lg p-2">
                  <p className="text-xs text-base-content/50 mb-1">Keep</p>
                  <p className="font-semibold">{keepProd.name}</p>
                  <p className="text-xs font-mono text-base-content/50">{keepProd.sku}</p>
                </div>
                <div className="bg-base-100 rounded-lg p-2">
                  <p className="text-xs text-base-content/50 mb-1">Remove</p>
                  <p className="font-semibold">{removeProd.name}</p>
                  <p className="text-xs font-mono text-base-content/50">{removeProd.sku}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-sm">Final stock:</label>
                <input type="number" className="input input-bordered input-sm w-24" value={stock} onChange={e => setStock(parseInt(e.target.value) || 0)} min={0} />
              </div>
            </div>
          )}
        </div>
        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-error" disabled={!keep || !remove} onClick={() => onMerge(keep, remove, stock)}>Merge Products</button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

// ─── Main Products Component ──────────────────────────────────────────────────
export const Products: React.FC<ProductsProps> = ({ products, inventory, pricing, onRefresh }) => {
  const [search, setSearch] = useState('');
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [newName, setNewName] = useState('');
  const [newSku, setNewSku] = useState('');
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const filtered = products.filter(p => {
    if (!search) return true;
    const q = search.toLowerCase();
    return p.name?.toLowerCase().includes(q) || p.sku?.toLowerCase().includes(q);
  });

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
      showToast('Products merged successfully!');
      onRefresh();
    } catch { showToast('Failed to merge'); }
    finally { setBusy(false); }
  }, [onRefresh]);

  const unlinkedCount = products.filter(p => !isLinked(p.id, pricing)).length;

  return (
    <div className="flex flex-col gap-4">
      {toast && (
        <div className="bg-success/10 border border-success/30 text-success rounded-xl px-4 py-3 text-sm font-medium">✓ {toast}</div>
      )}

      {/* Toolbar */}
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
        <button className="btn btn-primary btn-sm gap-1.5" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> Add
        </button>
        {unlinkedCount >= 2 && (
          <button className="btn btn-secondary btn-sm gap-1.5" onClick={() => setShowMerge(true)}>
            <Link size={14} /> Merge
          </button>
        )}
      </div>

      <p className="text-xs text-base-content/40">{filtered.length} of {products.length} products · Click any row to edit prices</p>

      {/* Products Table */}
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

                  return (
                    <tr
                      key={p.id}
                      className={`hover:bg-base-200/50 cursor-pointer transition-colors ${lowStock ? 'bg-orange-50/30' : ''}`}
                      onClick={() => setSelectedProduct(p)}
                    >
                      <td>
                        <div className="flex items-center gap-2">
                          {linked
                            ? <Link size={11} className="text-success flex-shrink-0" />
                            : <Unlink size={11} className="text-base-content/20 flex-shrink-0" />
                          }
                          <span className="text-sm font-medium text-base-content truncate max-w-[150px] sm:max-w-[200px]">{p.name}</span>
                        </div>
                      </td>
                      <td className="hidden sm:table-cell">
                        <span className="font-mono text-xs text-base-content/50">{p.sku}</span>
                      </td>
                      <td className="text-center">
                        <div className="flex justify-center gap-1">
                          {ssPr && <span className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-600">SS</span>}
                          {ebPr && <span className="inline-flex px-1.5 py-0.5 rounded text-xs font-medium bg-yellow-50 text-yellow-600">EB</span>}
                          {!ssPr && !ebPr && <span className="text-xs text-base-content/20">—</span>}
                        </div>
                      </td>
                      <td className="text-right">
                        <span className={`text-sm font-semibold ${lowStock ? 'text-orange-500' : 'text-base-content'}`}>
                          {inv?.total_stock ?? 0}
                          {lowStock && <AlertTriangle size={10} className="inline ml-1 text-orange-400" />}
                        </span>
                      </td>
                      <td className="text-right hidden md:table-cell">
                        <span className="text-sm text-base-content/70">
                          {ssPr ? `£${Number(ssPr.price).toFixed(2)}` : <span className="text-base-content/20">—</span>}
                        </span>
                      </td>
                      <td className="text-right hidden md:table-cell">
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

      {/* Pricing Modal */}
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
      {showMerge && (
        <MergeModal
          products={products}
          inventory={inventory}
          pricing={pricing}
          onClose={() => setShowMerge(false)}
          onMerge={handleMerge}
        />
      )}
    </div>
  );
};
