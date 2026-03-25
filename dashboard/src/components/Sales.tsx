import React, { useState, useMemo } from 'react';
import { PoundSterling, ShoppingBag, BarChart3, Filter } from 'lucide-react';
import type { Order, Product } from '../types';

interface SalesProps {
  orders: Order[];
  products: Product[];
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
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

const RANGE_OPTIONS: { id: RangeOption; label: string }[] = [
  { id: '7d', label: '7 Days' },
  { id: '30d', label: '30 Days' },
  { id: '3m', label: '3 Months' },
  { id: '12m', label: '12 Months' },
  { id: 'all', label: 'All Time' },
  { id: 'custom', label: 'Custom' },
];

function PlatformBadge({ platform }: { platform: string }) {
  if (platform?.toLowerCase().includes('squarespace'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">Squarespace</span>;
  if (platform?.toLowerCase().includes('ebay'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-100">eBay</span>;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{platform || '—'}</span>;
}

export const Sales: React.FC<SalesProps> = ({ orders, products }) => {
  const [range, setRange] = useState<RangeOption>('12m');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [productSearch, setProductSearch] = useState('');

  const { start, end } = useMemo(() => getDateRange(range, customStart, customEnd), [range, customStart, customEnd]);

  const filtered = useMemo(() => {
    return orders.filter(o => {
      if (o.ordered_at) {
        const d = new Date(o.ordered_at);
        if (d < start || d > end) return false;
      }
      if (platformFilter !== 'all' && !o.platform?.toLowerCase().includes(platformFilter)) return false;
      if (productSearch) {
        const q = productSearch.toLowerCase();
        const itemMatch = o.item_name?.toLowerCase().includes(q);
        const skuMatch = o.sku?.toLowerCase().includes(q);
        const prodMatch = products.find(p => p.id === o.product_id)?.name?.toLowerCase().includes(q);
        if (!itemMatch && !skuMatch && !prodMatch) return false;
      }
      return true;
    });
  }, [orders, start, end, platformFilter, productSearch, products]);

  const stats = useMemo(() => {
    const revenue = filtered.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const units = filtered.reduce((s, o) => s + (o.quantity || 0), 0);
    const sqRevenue = filtered.filter(o => o.platform?.toLowerCase().includes('squarespace')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const ebRevenue = filtered.filter(o => o.platform?.toLowerCase().includes('ebay')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const maxPlatform = Math.max(sqRevenue, ebRevenue, 1);
    const avgOrderValue = filtered.length > 0 ? revenue / filtered.length : 0;

    const productSales: Record<string, { name: string; units: number; revenue: number }> = {};
    for (const o of filtered) {
      const name = o.item_name || products.find(p => p.id === o.product_id)?.name || o.sku || 'Unknown';
      if (!productSales[name]) productSales[name] = { name, units: 0, revenue: 0 };
      productSales[name].units += o.quantity || 0;
      productSales[name].revenue += Number(o.order_total || o.unit_price || 0);
    }
    const topProducts = Object.values(productSales).sort((a, b) => b.revenue - a.revenue).slice(0, 8);

    // Daily chart (last N days based on range)
    const days = range === '7d' ? 7 : range === '30d' ? 30 : range === '3m' ? 30 : 30;
    const dailySales: { date: string; revenue: number; label: string }[] = [];
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const ds = d.toISOString().split('T')[0];
      const dayRevenue = filtered
        .filter(o => o.ordered_at && o.ordered_at.startsWith(ds))
        .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      dailySales.push({ date: ds, revenue: dayRevenue, label: d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) });
    }
    const maxDaily = Math.max(...dailySales.map(d => d.revenue), 1);

    return { revenue, units, sqRevenue, ebRevenue, maxPlatform, avgOrderValue, topProducts, dailySales, maxDaily };
  }, [filtered, products, range]);

  if (orders.length === 0) {
    return (
      <div className="bg-base-100 rounded-xl border border-base-300 p-12 text-center shadow-sm">
        <BarChart3 size={32} className="text-base-content/20 mx-auto mb-3" />
        <p className="text-base-content/40 text-sm">No sales data yet — orders will appear after syncing.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <Filter size={14} className="text-base-content/40" />
          <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide">Filters</p>
        </div>

        {/* Date Range */}
        <div className="flex flex-wrap gap-1.5 mb-3">
          {RANGE_OPTIONS.map(opt => (
            <button
              key={opt.id}
              onClick={() => setRange(opt.id)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                range === opt.id ? 'bg-primary text-white shadow-sm' : 'bg-base-200 text-base-content/60 hover:bg-base-300'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {range === 'custom' && (
          <div className="flex gap-3 mb-3 flex-wrap">
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

        {/* Platform & Product Filters */}
        <div className="flex flex-wrap gap-2">
          <select
            className="select select-bordered select-sm"
            value={platformFilter}
            onChange={e => setPlatformFilter(e.target.value)}
          >
            <option value="all">All Platforms</option>
            <option value="squarespace">Squarespace</option>
            <option value="ebay">eBay</option>
          </select>
          <input
            type="search"
            className="input input-bordered input-sm flex-1 min-w-[150px]"
            placeholder="Search by product or SKU..."
            value={productSearch}
            onChange={e => setProductSearch(e.target.value)}
          />
        </div>
      </div>

      <p className="text-xs text-base-content/40">{filtered.length} order{filtered.length !== 1 ? 's' : ''} matched</p>

      {/* Stats Cards */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide mb-2">Revenue</p>
          <p className="text-xl font-bold text-base-content">£{stats.revenue.toFixed(2)}</p>
        </div>
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide mb-2">Orders</p>
          <p className="text-xl font-bold text-base-content">{filtered.length}</p>
        </div>
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide mb-2">Avg Order</p>
          <p className="text-xl font-bold text-base-content">£{stats.avgOrderValue.toFixed(2)}</p>
        </div>
      </div>

      {/* Platform Breakdown */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-base-content mb-4">Revenue by Platform</h3>
        <div className="flex flex-col gap-3">
          <div>
            <div className="flex justify-between text-sm mb-1.5">
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700">Squarespace</span>
              <span className="font-semibold">£{stats.sqRevenue.toFixed(2)}</span>
            </div>
            <div className="w-full bg-base-200 rounded-full h-2.5">
              <div className="bg-indigo-400 h-2.5 rounded-full transition-all" style={{ width: `${(stats.sqRevenue / stats.maxPlatform) * 100}%` }} />
            </div>
          </div>
          <div>
            <div className="flex justify-between text-sm mb-1.5">
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700">eBay</span>
              <span className="font-semibold">£{stats.ebRevenue.toFixed(2)}</span>
            </div>
            <div className="w-full bg-base-200 rounded-full h-2.5">
              <div className="bg-yellow-400 h-2.5 rounded-full transition-all" style={{ width: `${(stats.ebRevenue / stats.maxPlatform) * 100}%` }} />
            </div>
          </div>
        </div>
      </div>

      {/* Top Products */}
      {stats.topProducts.length > 0 && (
        <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-base-300 bg-base-200/40">
            <h3 className="text-sm font-semibold text-base-content">Top Products</h3>
          </div>
          <div className="divide-y divide-base-200">
            {stats.topProducts.map((tp, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="w-5 h-5 rounded-full bg-base-200 flex items-center justify-center text-xs font-bold text-base-content/50 flex-shrink-0">{i + 1}</span>
                  <span className="text-sm text-base-content truncate">{tp.name}</span>
                </div>
                <div className="flex items-center gap-4 flex-shrink-0 ml-2">
                  <span className="text-xs text-base-content/40">{tp.units} units</span>
                  <span className="text-sm font-semibold text-base-content">£{tp.revenue.toFixed(2)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sales Chart */}
      {(range === '7d' || range === '30d' || range === '3m') && (
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-base-content mb-4">Daily Sales</h3>
          <div className="flex items-end gap-1 h-28">
            {stats.dailySales.map((d, i) => (
              <div key={i} className="flex-1 flex flex-col items-center gap-1 group">
                <div className="text-xs text-base-content/40 opacity-0 group-hover:opacity-100 transition-opacity">
                  {d.revenue > 0 ? `£${d.revenue.toFixed(0)}` : ''}
                </div>
                <div className="w-full flex justify-center">
                  <div
                    className={`w-full max-w-[20px] rounded-t transition-all ${d.revenue > 0 ? 'bg-primary' : 'bg-base-200'}`}
                    style={{ height: `${Math.max((d.revenue / stats.maxDaily) * 88, d.revenue > 0 ? 4 : 2)}px` }}
                  />
                </div>
                {stats.dailySales.length <= 14 && (
                  <div className="text-xs text-base-content/30 text-center" style={{ fontSize: '9px' }}>{d.label}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Orders Table */}
      <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-base-300 bg-base-200/40">
          <h3 className="text-sm font-semibold text-base-content">Orders</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="table table-sm w-full">
            <thead>
              <tr className="text-xs text-base-content/40 uppercase tracking-wide">
                <th>Date</th>
                <th>Platform</th>
                <th>Customer</th>
                <th>Item</th>
                <th className="text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 50).map(o => (
                <tr key={o.id} className="hover:bg-base-200/40">
                  <td className="text-xs text-base-content/50">{formatDate(o.ordered_at)}</td>
                  <td><PlatformBadge platform={o.platform} /></td>
                  <td className="text-sm">{o.customer_name || '—'}</td>
                  <td className="text-sm text-base-content/60 max-w-[160px] truncate">{o.item_name || o.sku || '—'}</td>
                  <td className="text-right text-sm font-semibold">£{Number(o.order_total || o.unit_price || 0).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {filtered.length > 50 && (
            <p className="text-center text-xs text-base-content/40 py-3">Showing 50 of {filtered.length} orders</p>
          )}
        </div>
      </div>
    </div>
  );
};
