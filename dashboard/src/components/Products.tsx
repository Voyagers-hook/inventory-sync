import React, { useState, useCallback } from 'react';
import { Plus, Trash2, Merge, Edit3, Check, X, Link, Unlink } from 'lucide-react';
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

function platformIcons(productId: string, pricing: Pricing[]): React.ReactElement {
  const platforms = pricing.filter(p => p.product_id === productId).map(p => p.platform);
  return (
    <div className="flex gap-1">
      {platforms.includes('squarespace') && <span className="badge badge-neutral badge-xs">SQ</span>}
      {platforms.includes('ebay') && <span className="badge badge-warning badge-xs">EB</span>}
      {platforms.length === 0 && <span className="badge badge-ghost badge-xs">None</span>}
    </div>
  );
}

function isLinkedByPricing(productId: string, pricing: Pricing[]): boolean {
  const platforms = pricing.filter(p => p.product_id === productId).map(p => p.platform);
  return platforms.includes('squarespace') && platforms.includes('ebay');
}

function calcMargin(costPrice: number | null, sellPrice: number): string {
  if (!costPrice || costPrice <= 0) return '—';
  const margin = ((sellPrice - costPrice) / sellPrice) * 100;
  return `${margin.toFixed(0)}%`;
}

function calcProfit(costPrice: number | null, sellPrice: number): string {
  if (!costPrice || costPrice <= 0) return '—';
  return `£${(sellPrice - costPrice).toFixed(2)}`;
}

const AddProductModal: React.FC<{ onClose: () => void; onSave: (name: string, sku: string, desc: string, costPrice: number | null) => void }> = ({ onClose, onSave }) => {
  const [name, setName] = useState('');
  const [sku, setSku] = useState('');
  const [desc, setDesc] = useState('');
  const [costPrice, setCostPrice] = useState('');

  return (
    <div className="modal modal-open">
      <div className="modal-box">
        <h3 className="font-bold text-lg">Add Product</h3>
        <div className="flex flex-col gap-3 mt-4">
          <input className="input input-bordered" placeholder="Product name" value={name} onChange={e => setName(e.target.value)} />
          <input className="input input-bordered" placeholder="SKU" value={sku} onChange={e => setSku(e.target.value)} />
          <input className="input input-bordered" placeholder="Cost price (£)" type="number" step="0.01" min="0" value={costPrice} onChange={e => setCostPrice(e.target.value)} />
          <textarea className="textarea textarea-bordered" placeholder="Description" value={desc} onChange={e => setDesc(e.target.value)} />
        </div>
        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => onSave(name, sku, desc, costPrice ? parseFloat(costPrice) : null)} disabled={!name.trim()}>Add</button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

const MergeModal: React.FC<{
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  onClose: () => void;
  onMerge: (keepId: string, removeId: string, stock: number) => void;
}> = ({ products, inventory, pricing, onClose, onMerge }) => {
  const unlinked = products.filter(p => !isLinkedByPricing(p.id, pricing));
  const [keep, setKeep] = useState('');
  const [remove, setRemove] = useState('');
  const [stock, setStock] = useState(0);

  const keepProd = products.find(p => p.id === keep);
  const removeProd = products.find(p => p.id === remove);
  const keepInv = inventory.find(i => i.product_id === keep);
  const removeInv = inventory.find(i => i.product_id === remove);

  const handleKeepChange = (id: string) => {
    setKeep(id);
    const inv = inventory.find(i => i.product_id === id);
    setStock(inv?.total_stock || 0);
  };

  return (
    <div className="modal modal-open">
      <div className="modal-box max-w-lg">
        <h3 className="font-bold text-lg">Merge Products</h3>
        <p className="text-sm text-base-content/60 mt-1">Combine two products into one. Platform IDs from the removed product will be moved to the kept product.</p>

        <div className="flex flex-col gap-3 mt-4">
          <div>
            <label className="text-sm font-semibold">Keep this product:</label>
            <select className="select select-bordered w-full mt-1" value={keep} onChange={e => handleKeepChange(e.target.value)}>
              <option value="">Select product to keep...</option>
              {unlinked.filter(p => p.id !== remove).map(p => (
                <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-sm font-semibold">Remove &amp; merge into above:</label>
            <select className="select select-bordered w-full mt-1" value={remove} onChange={e => setRemove(e.target.value)}>
              <option value="">Select product to remove...</option>
              {unlinked.filter(p => p.id !== keep).map(p => (
                <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>
              ))}
            </select>
          </div>

          {keepProd && removeProd && (
            <div className="bg-base-300 rounded-lg p-3">
              <div className="text-sm font-semibold mb-2">Preview</div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="bg-base-200 rounded p-2">
                  <div className="text-xs text-base-content/60">Keep</div>
                  <div className="font-semibold">{keepProd.name}</div>
                  <div className="text-xs text-base-content/60">{keepProd.sku}</div>
                  <div className="text-xs mt-1">Stock: {keepInv?.total_stock || 0}</div>
                  <div className="mt-1">{platformIcons(keepProd.id, pricing)}</div>
                </div>
                <div className="bg-base-200 rounded p-2">
                  <div className="text-xs text-base-content/60">Remove</div>
                  <div className="font-semibold">{removeProd.name}</div>
                  <div className="text-xs text-base-content/60">{removeProd.sku}</div>
                  <div className="text-xs mt-1">Stock: {removeInv?.total_stock || 0}</div>
                  <div className="mt-1">{platformIcons(removeProd.id, pricing)}</div>
                </div>
              </div>
              <div className="mt-3">
                <label className="text-sm">Final stock quantity:</label>
                <input
                  type="number"
                  className="input input-bordered input-sm w-24 ml-2"
                  value={stock}
                  onChange={e => setStock(parseInt(e.target.value) || 0)}
                  min={0}
                />
              </div>
            </div>
          )}
        </div>

        <div className="modal-action">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-error"
            disabled={!keep || !remove}
            onClick={() => onMerge(keep, remove, stock)}
          >
            <Merge size={14} /> Merge
          </button>
        </div>
      </div>
      <div className="modal-backdrop" onClick={onClose} />
    </div>
  );
};

export const Products: React.FC<ProductsProps> = ({ products, inventory, pricing, onRefresh }) => {
  const [showAdd, setShowAdd] = useState(false);
  const [showMerge, setShowMerge] = useState(false);
  const [editingStock, setEditingStock] = useState<string | null>(null);
  const [editStockVal, setEditStockVal] = useState(0);
  const [editingPrice, setEditingPrice] = useState<string | null>(null);
  const [editPriceVal, setEditPriceVal] = useState(0);
  const [editingCost, setEditingCost] = useState<string | null>(null);
  const [editCostVal, setEditCostVal] = useState(0);
  const [toast, setToast] = useState('');
  const [busy, setBusy] = useState(false);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const handleAdd = useCallback(async (name: string, sku: string, desc: string, costPrice: number | null) => {
    setBusy(true);
    try {
      const prod = await createProduct({ name, sku, description: desc, cost_price: costPrice });
      await createInventory({ product_id: prod.id, total_stock: 0, reserved_stock: 0, low_stock_threshold: 5 });
      setShowAdd(false);
      showToast('Product added!');
      onRefresh();
    } catch (err) {
      console.error('Failed to add product:', err);
      showToast('Failed to add product');
    } finally {
      setBusy(false);
    }
  }, [onRefresh]);

  const handleDelete = useCallback(async (id: string, name: string) => {
    if (!confirm) return;
    setBusy(true);
    try {
      await deleteProduct(id);
      showToast(`Deleted ${name}`);
      onRefresh();
    } catch (err) {
      console.error('Failed to delete product:', err);
      showToast('Failed to delete product');
    } finally {
      setBusy(false);
    }
  }, [onRefresh]);

  const handleStockSave = useCallback(async (productId: string) => {
    try {
      await updateInventory(productId, { total_stock: editStockVal });
      setEditingStock(null);
      showToast('Stock updated!');
      onRefresh();
    } catch (err) {
      console.error('Failed to update stock:', err);
      showToast('Failed to update stock');
    }
  }, [editStockVal, onRefresh]);

  const handlePriceSave = useCallback(async (pricingId: string) => {
    try {
      await updatePricing(pricingId, editPriceVal);
      setEditingPrice(null);
      showToast('Price updated!');
      onRefresh();
    } catch (err) {
      console.error('Failed to update price:', err);
      showToast('Failed to update price');
    }
  }, [editPriceVal, onRefresh]);

  const handleCostSave = useCallback(async (productId: string) => {
    try {
      await updateProduct(productId, { cost_price: editCostVal });
      setEditingCost(null);
      showToast('Cost price updated!');
      onRefresh();
    } catch (err) {
      console.error('Failed to update cost price:', err);
      showToast('Failed to update cost price');
    }
  }, [editCostVal, onRefresh]);

  const handleMerge = useCallback(async (keepId: string, removeId: string, stock: number) => {
    setBusy(true);
    try {
      await mergeProducts(keepId, removeId, stock);
      setShowMerge(false);
      showToast('Products merged!');
      onRefresh();
    } catch (err) {
      console.error('Failed to merge products:', err);
      showToast('Failed to merge products');
    } finally {
      setBusy(false);
    }
  }, [onRefresh]);

  const unlinkedCount = products.filter(p => !isLinkedByPricing(p.id, pricing)).length;

  // Calculate average selling price per product for margin display
  const getAvgPrice = (productId: string): number => {
    const prices = pricing.filter(pr => pr.product_id === productId);
    if (prices.length === 0) return 0;
    return prices.reduce((sum, pr) => sum + Number(pr.price), 0) / prices.length;
  };

  return (
    <div className="flex flex-col gap-3">
      {toast && <div className="alert alert-success text-sm py-2">{toast}</div>}

      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>
          <Plus size={14} /> Add Product
        </button>
        {unlinkedCount >= 2 && (
          <button className="btn btn-secondary btn-sm" onClick={() => setShowMerge(true)}>
            <Merge size={14} /> Merge ({unlinkedCount} unlinked)
          </button>
        )}
      </div>

      {products.length === 0 ? (
        <div className="card bg-base-200">
          <div className="card-body items-center text-center p-8">
            <p className="text-base-content/60">No products yet. Add one or run a sync.</p>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Product</th>
                <th>SKU</th>
                <th>Platforms</th>
                <th>Stock</th>
                <th>Cost</th>
                <th>Prices</th>
                <th>Margin</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {products.map(p => {
                const inv = inventory.find(i => i.product_id === p.id);
                const prices = pricing.filter(pr => pr.product_id === p.id);
                const linked = isLinkedByPricing(p.id, pricing);
                const lowStock = inv && inv.total_stock <= (inv.low_stock_threshold || 5);
                const avgPrice = getAvgPrice(p.id);

                return (
                  <tr key={p.id} className={lowStock ? 'bg-warning/10' : ''}>
                    <td>
                      <div className="flex items-center gap-1">
                        {linked
                          ? <Link size={12} className="text-success opacity-60" />
                          : <Unlink size={12} className="text-warning opacity-60" />
                        }
                        <span className="text-sm font-semibold">{p.name}</span>
                      </div>
                    </td>
                    <td className="text-xs font-mono">{p.sku}</td>
                    <td>{platformIcons(p.id, pricing)}</td>
                    <td>
                      {editingStock === p.id ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="number"
                            className="input input-bordered input-xs w-16"
                            value={editStockVal}
                            onChange={e => setEditStockVal(parseInt(e.target.value) || 0)}
                            min={0}
                          />
                          <button className="btn btn-ghost btn-xs" onClick={() => handleStockSave(p.id)}><Check size={12} /></button>
                          <button className="btn btn-ghost btn-xs" onClick={() => setEditingStock(null)}><X size={12} /></button>
                        </div>
                      ) : (
                        <span
                          className={`cursor-pointer text-sm ${lowStock ? 'text-warning font-bold' : ''}`}
                          onClick={() => { setEditingStock(p.id); setEditStockVal(inv?.total_stock || 0); }}
                          title="Click to edit"
                        >
                          {inv?.total_stock ?? 0} <Edit3 size={10} className="inline opacity-40" />
                        </span>
                      )}
                    </td>
                    <td>
                      {editingCost === p.id ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="number"
                            className="input input-bordered input-xs w-20"
                            value={editCostVal}
                            onChange={e => setEditCostVal(parseFloat(e.target.value) || 0)}
                            step="0.01"
                            min={0}
                          />
                          <button className="btn btn-ghost btn-xs" onClick={() => handleCostSave(p.id)}><Check size={12} /></button>
                          <button className="btn btn-ghost btn-xs" onClick={() => setEditingCost(null)}><X size={12} /></button>
                        </div>
                      ) : (
                        <span
                          className="cursor-pointer text-sm"
                          onClick={() => { setEditingCost(p.id); setEditCostVal(p.cost_price || 0); }}
                          title="Click to edit cost price"
                        >
                          {p.cost_price ? `£${Number(p.cost_price).toFixed(2)}` : '—'} <Edit3 size={10} className="inline opacity-40" />
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="flex flex-col gap-1">
                        {prices.length === 0 && <span className="text-xs text-base-content/60">—</span>}
                        {prices.map(pr => (
                          <div key={pr.id} className="flex items-center gap-1">
                            <span className={`text-xs ${pr.platform === 'squarespace' ? 'badge badge-neutral badge-xs' : 'badge badge-warning badge-xs'}`}>
                              {pr.platform === 'squarespace' ? 'SQ' : 'EB'}
                            </span>
                            {editingPrice === pr.id ? (
                              <div className="flex items-center gap-1">
                                <input
                                  type="number"
                                  className="input input-bordered input-xs w-20"
                                  value={editPriceVal}
                                  onChange={e => setEditPriceVal(parseFloat(e.target.value) || 0)}
                                  step="0.01"
                                  min={0}
                                />
                                <button className="btn btn-ghost btn-xs" onClick={() => handlePriceSave(pr.id)}><Check size={12} /></button>
                                <button className="btn btn-ghost btn-xs" onClick={() => setEditingPrice(null)}><X size={12} /></button>
                              </div>
                            ) : (
                              <span
                                className="text-xs cursor-pointer"
                                onClick={() => { setEditingPrice(pr.id); setEditPriceVal(Number(pr.price)); }}
                                title="Click to edit"
                              >
                                £{Number(pr.price).toFixed(2)} <Edit3 size={10} className="inline opacity-40" />
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="flex flex-col gap-1">
                        {prices.length === 0 && <span className="text-xs text-base-content/60">—</span>}
                        {prices.map(pr => (
                          <div key={pr.id} className="flex items-center gap-1 text-xs">
                            <span className={pr.platform === 'squarespace' ? 'badge badge-neutral badge-xs' : 'badge badge-warning badge-xs'}>
                              {pr.platform === 'squarespace' ? 'SQ' : 'EB'}
                            </span>
                            <span className={Number(pr.price) > (p.cost_price || 0) && p.cost_price ? 'text-success' : p.cost_price ? 'text-error' : ''}>
                              {calcMargin(p.cost_price, Number(pr.price))}
                            </span>
                            <span className="text-base-content/40">
                              {calcProfit(p.cost_price, Number(pr.price))}
                            </span>
                          </div>
                        ))}
                      </div>
                    </td>
                    <td>
                      <button
                        className="btn btn-ghost btn-xs text-error"
                        onClick={() => handleDelete(p.id, p.name)}
                        disabled={busy}
                        title="Delete product"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && <AddProductModal onClose={() => setShowAdd(false)} onSave={handleAdd} />}
      {showMerge && <MergeModal products={products} inventory={inventory} pricing={pricing} onClose={() => setShowMerge(false)} onMerge={handleMerge} />}
    </div>
  );
};
