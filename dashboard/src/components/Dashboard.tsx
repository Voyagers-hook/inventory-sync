import React, { useState, useCallback, useMemo } from 'react';
import { PoundSterling, ShoppingCart, AlertTriangle, Package, RefreshCw, Clock, ChevronRight } from 'lucide-react';
import type { Product, Inventory, Pricing, Order, SyncLog, Setting, TabName } from '../types';
import { updateSetting, triggerQuickSync } from '../utils/supabase';

interface DashboardProps {
  products: Product[];
  inventory: Inventory[];
  pricing: Pricing[];
  orders: Order[];
  syncLogs: SyncLog[];
  settings: Setting[];
  onRefresh: () => void;
  onNavigate: (tab: TabName) => void;
  onNavigateLowStock: () => void;
}

type RangeOption = '7d' | '30d' | '3m' | '12m' | 'all' | 'custom';

function getDateRange(range: RangeOption, customStart?: string, customEnd?: string): { start: Date; end: Date } {
  const end = new Date();
  const start = new Date();
  switch (range) {
    case '7d': start.setDate(start.getDate() - 7); break;
    case '30d': start.setDate(start.getDate() - 30); break;
    case '3m': start.setMonth(start.getMonth() - 3); break;
    case '12m': start.setFullYear(start.getFullYear() - 1); break;
    case 'all': start.setFullYear(2000); break;
    case 'custom':
      return {
        start: customStart ? new Date(customStart) : new Date(Date.now() - 365 * 86400000),
        end: customEnd ? new Date(customEnd) : end,
      };
  }
  return { start, end };
}

function formatDate(d: string): string {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatDateShort(d: string): string {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
}

function PlatformBadge({ platform }: { platform: string }) {
  if (platform?.toLowerCase().includes('squarespace'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">Squarespace</span>;
  if (platform?.toLowerCase().includes('ebay'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-100">eBay</span>;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{platform || '—'}</span>;
}

const RANGE_OPTIONS: { id: RangeOption; label: string }[] = [
  { id: '7d', label: '7 Days' },
  { id: '30d', label: '30 Days' },
  { id: '3m', label: '3 Months' },
  { id: '12m', label: '12 Months' },
  { id: 'all', label: 'All Time' },
  { id: 'custom', label: 'Custom' },
];

export const Dashboard: React.FC<DashboardProps> = ({
  products, inventory, pricing, orders, syncLogs, settings, onRefresh, onNavigate, onNavigateLowStock
}) => {
  const [range, setRange] = useState<RangeOption>('12m');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState('');

  const { start, end } = useMemo(() => getDateRange(range, customStart, customEnd), [range, customStart, customEnd]);

  const filteredOrders = useMemo(() =>
    orders.filter(o => {
      if (!o.ordered_at) return false;
      const d = new Date(o.ordered_at);
      return d >= start && d <= end;
    }),
    [orders, start, end]
  );

  const stats = useMemo(() => {
    const revenue = filteredOrders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const orderCount = filteredOrders.length;
    const lowStockThreshold = parseInt(settings.find(s => s.key === 'default_low_stock_threshold')?.value || '5');
    const lowStock = inventory.filter(i => i.total_stock <= (i.low_stock_threshold || lowStockThreshold)).length;
    const sqRevenue = filteredOrders
      .filter(o => o.platform?.toLowerCase().includes('squarespace'))
      .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const ebRevenue = filteredOrders
      .filter(o => o.platform?.toLowerCase().includes('ebay'))
      .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    return { revenue, orderCount, lowStock, sqRevenue, ebRevenue };
  }, [filteredOrders, orders, inventory, settings]);

  const lastSync = syncLogs[0] || null;

  const handleSyncNow = useCallback(async () => {
    setSyncing(true);
    try {
      await updateSetting('manual_sync_requested', 'true');
      const triggered = await triggerQuickSync();
      setToast(triggered
        ? 'Sync triggered — running now (~1 min)'
        : 'No GitHub token saved — check Settings → GitHub Token'
      );
      setTimeout(() => setToast(''), 6000);
    } catch {
      setToast('Error triggering sync — check browser console');
      setTimeout(() => setToast(''), 4000);
    } finally {
      setSyncing(false);
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {toast && (
        <div className="bg-success/10 border border-success/30 text-success rounded-xl px-4 py-3 text-sm font-medium">
          {toast}
        </div>
      )}

      {/* Date Range Selector */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide mb-3">Time Period</p>
        <div className="flex flex-wrap gap-2">
          {RANGE_OPTIONS.map(opt => (
            <button
              key={opt.id}
              onClick={() => setRange(opt.id)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                range === opt.id
                  ? 'bg-primary text-white shadow-sm'
                  : 'bg-base-200 text-base-content/60 hover:bg-base-300'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        {range === 'custom' && (
          <div className="flex gap-3 mt-3 flex-wrap">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-base-content/50">From</label>
              <input type="date" className="input input-bordered input-sm" value={customStart} onChange={e => setCustomStart(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-base-content/50">To</label>
              <input type="date" className="input input-bordered input-sm" value={customEnd} onChange={e => setCustomEnd(e.target.value)} />
            </div>
          </div>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 gap-3">
        {/* Revenue */}
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <div className="flex items-start justify-between mb-2">
            <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide">Revenue</p>
            <div className="p-1.5 bg-green-50 rounded-lg"><PoundSterling size={13} className="text-green-600" /></div>
          </div>
          <p className="text-2xl font-bold text-base-content">£{stats.revenue.toFixed(2)}</p>
          <div className="flex gap-3 mt-1.5">
            <span className="text-xs text-base-content/40">SS £{stats.sqRevenue.toFixed(0)}</span>
            <span className="text-xs text-base-content/40">eBay £{stats.ebRevenue.toFixed(0)}</span>
          </div>
        </div>

        {/* Orders */}
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <div className="flex items-start justify-between mb-2">
            <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide">Orders</p>
            <div className="p-1.5 bg-blue-50 rounded-lg"><ShoppingCart size={13} className="text-blue-600" /></div>
          </div>
          <p className="text-2xl font-bold text-base-content">{stats.orderCount}</p>
          <p className="text-xs text-base-content/40 mt-1.5">in selected period</p>
        </div>

        {/* Low Stock */}
        <div
          className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm cursor-pointer hover:border-red-300 hover:shadow-md transition-all group"
          onClick={onNavigateLowStock}
        >
          <div className="flex items-start justify-between mb-2">
            <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide">Low Stock</p>
            <div className="p-1.5 bg-red-50 rounded-lg"><AlertTriangle size={13} className="text-red-500" /></div>
          </div>
          <p className="text-2xl font-bold text-red-500">{stats.lowStock}</p>
          <p className="text-xs text-base-content/40 mt-1.5 flex items-center gap-1">
            items need restocking <span className="opacity-0 group-hover:opacity-100 transition-opacity">· click to view →</span>
          </p>
        </div>
      </div>

      {/* Sync Status */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Clock size={14} className="text-base-content/40" />
            <h3 className="font-semibold text-sm text-base-content">Sync Status</h3>
          </div>
          <button
            className="btn btn-primary btn-sm gap-1.5"
            onClick={handleSyncNow}
            disabled={syncing}
          >
            <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Queuing...' : 'Sync Now'}
          </button>
        </div>
        {lastSync ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Status</p>
              <p className={`font-medium capitalize ${lastSync.status === 'success' ? 'text-success' : lastSync.status === 'error' ? 'text-error' : 'text-base-content'}`}>
                {lastSync.status}
              </p>
            </div>
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Last run</p>
              <p>{formatDate(lastSync.completed_at || lastSync.started_at)}</p>
            </div>
            {lastSync.error_message && (
              <div className="col-span-2 mt-1">
                <p className="text-xs text-error bg-error/10 rounded-lg px-3 py-2">{lastSync.error_message}</p>
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-base-content/40">No syncs recorded yet.</p>
        )}
      </div>

      {/* Inventory Summary */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Package size={14} className="text-base-content/40" />
            <h3 className="font-semibold text-sm text-base-content">Inventory</h3>
          </div>
          <button
            className="text-xs text-primary font-medium hover:underline flex items-center gap-0.5"
            onClick={() => onNavigate('products')}
          >
            View all <ChevronRight size={11} />
          </button>
        </div>
        <div className="flex gap-8">
          <div>
            <p className="text-2xl font-bold text-base-content">{products.length}</p>
            <p className="text-xs text-base-content/40 mt-0.5">Total products</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-base-content">{inventory.reduce((s, i) => s + (i.total_stock || 0), 0)}</p>
            <p className="text-xs text-base-content/40 mt-0.5">Units in stock</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-base-content">{pricing.filter(p => p.platform === 'ebay').length}</p>
            <p className="text-xs text-base-content/40 mt-0.5">eBay listings</p>
          </div>
        </div>
      </div>
    </div>
  );
};
