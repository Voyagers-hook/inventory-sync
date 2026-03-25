import React, { useState, useCallback } from 'react';
import { Package, PoundSterling, AlertTriangle, ShoppingCart, RefreshCw, Clock } from 'lucide-react';
import type { Product, Inventory, Pricing, Order, SyncLog, Setting } from '../types';
import { updateSetting } from '../utils/supabase';

interface DashboardProps {
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  orders: Order[];
  syncLogs: SyncLog[];
  settings: Setting[];
  onRefresh: () => void;
}

function formatDate(d: string): string {
  if (!d) return '—';
  const date = new Date(d);
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function platformBadge(platform: string): React.ReactElement {
  if (platform?.toLowerCase().includes('squarespace')) {
    return <span className="badge badge-neutral badge-sm">Squarespace</span>;
  }
  if (platform?.toLowerCase().includes('ebay')) {
    return <span className="badge badge-warning badge-sm">eBay</span>;
  }
  return <span className="badge badge-ghost badge-sm">{platform || '—'}</span>;
}

function statusBadge(status: string): React.ReactElement {
  switch (status?.toUpperCase()) {
    case 'PENDING': return <span className="badge badge-warning badge-sm">Pending</span>;
    case 'SHIPPED': return <span className="badge badge-info badge-sm">Shipped</span>;
    case 'DELIVERED': return <span className="badge badge-success badge-sm">Delivered</span>;
    default: return <span className="badge badge-ghost badge-sm">{status || 'Unknown'}</span>;
  }
}

export const Dashboard: React.FC<DashboardProps> = ({ products, inventory, pricing, orders, syncLogs, settings, onRefresh }) => {
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState('');

  const totalProducts = products.length;
  const lowStockThreshold = parseInt(settings.find(s => s.key === 'default_low_stock_threshold')?.value || '5', 10);
  const lowStockItems = inventory.filter(i => i.total_stock <= (i.low_stock_threshold || lowStockThreshold)).length;
  const pendingOrders = orders.filter(o => o.fulfillment_status?.toUpperCase() === 'PENDING').length;

  const totalStockValue = inventory.reduce((sum, inv) => {
    const productPrices = pricing.filter(p => p.product_id === inv.product_id);
    const avgPrice = productPrices.length ? productPrices.reduce((s, p) => s + Number(p.price), 0) / productPrices.length : 0;
    return sum + inv.total_stock * avgPrice;
  }, 0);

  const recentOrders = orders.slice(0, 5);
  const lastSync = syncLogs.length > 0 ? syncLogs[0] : null;

  const handleSyncNow = useCallback(async () => {
    setSyncing(true);
    try {
      await updateSetting('sync_requested', 'true');
      setToast('Sync requested! It will run shortly.');
      setTimeout(() => setToast(''), 3000);
    } catch (err) {
      console.error('Failed to request sync:', err);
      setToast('Failed to request sync');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSyncing(false);
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {toast && (
        <div className="alert alert-success text-sm py-2">
          {toast}
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <div className="flex items-center gap-2 text-base-content/60">
              <Package size={16} className="opacity-60" />
              <span className="text-xs uppercase tracking-wide">Products</span>
            </div>
            <div className="text-2xl font-bold">{totalProducts}</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <div className="flex items-center gap-2 text-base-content/60">
              <PoundSterling size={16} className="opacity-60" />
              <span className="text-xs uppercase tracking-wide">Stock Value</span>
            </div>
            <div className="text-2xl font-bold">£{totalStockValue.toFixed(2)}</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <div className="flex items-center gap-2 text-base-content/60">
              <AlertTriangle size={16} className="opacity-60" />
              <span className="text-xs uppercase tracking-wide">Low Stock</span>
            </div>
            <div className="text-2xl font-bold text-warning">{lowStockItems}</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <div className="flex items-center gap-2 text-base-content/60">
              <ShoppingCart size={16} className="opacity-60" />
              <span className="text-xs uppercase tracking-wide">Pending Orders</span>
            </div>
            <div className="text-2xl font-bold text-info">{pendingOrders}</div>
          </div>
        </div>
      </div>

      {/* Sync Status */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Clock size={16} className="opacity-60" />
              <span className="font-semibold text-sm">Last Sync</span>
            </div>
            <button className="btn btn-primary btn-sm" onClick={handleSyncNow} disabled={syncing}>
              <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Requesting...' : 'Sync Now'}
            </button>
          </div>
          {lastSync ? (
            <div className="flex flex-col gap-1 mt-2 text-sm">
              <div className="flex gap-2">
                <span className="text-base-content/60">Status:</span>
                <span className={lastSync.status === 'success' ? 'text-success' : lastSync.status === 'error' ? 'text-error' : ''}>{lastSync.status}</span>
              </div>
              <div className="flex gap-2">
                <span className="text-base-content/60">Time:</span>
                <span>{formatDate(lastSync.completed_at || lastSync.started_at)}</span>
              </div>
              <div className="flex gap-2">
                <span className="text-base-content/60">Source:</span>
                <span>{lastSync.source || '—'}</span>
              </div>
              {lastSync.error_message && (
                <div className="text-error text-xs mt-1">{lastSync.error_message}</div>
              )}
            </div>
          ) : (
            <p className="text-base-content/60 text-sm mt-2">No syncs recorded yet.</p>
          )}
        </div>
      </div>

      {/* Recent Orders */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="font-semibold text-sm mb-2">Recent Orders</h3>
          {recentOrders.length === 0 ? (
            <p className="text-base-content/60 text-sm">No orders yet — they'll appear after the first sync runs.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="table table-sm">
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Platform</th>
                    <th>Customer</th>
                    <th>Total</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentOrders.map(o => (
                    <tr key={o.id}>
                      <td className="text-xs">{o.order_number || o.platform_order_id || '—'}</td>
                      <td>{platformBadge(o.platform)}</td>
                      <td className="text-xs">{o.customer_name || '—'}</td>
                      <td className="text-xs">£{Number(o.order_total || o.unit_price || 0).toFixed(2)}</td>
                      <td>{statusBadge(o.fulfillment_status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Refresh */}
      <button className="btn btn-ghost btn-sm self-center" onClick={onRefresh}>
        <RefreshCw size={14} /> Refresh Data
      </button>
    </div>
  );
};
