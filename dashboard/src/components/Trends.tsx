import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, Activity, AlertCircle } from 'lucide-react';
import type { Product, Inventory, Order, SalesTrend } from '../types';

interface TrendsProps {
  products: Product[];
  inventory: Inventory[];
  orders: Order[];
  trends: SalesTrend[];
}

export const Trends: React.FC<TrendsProps> = ({ products, inventory, orders }) => {
  const analytics = useMemo(() => {
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    const recentOrders = orders.filter(o => o.ordered_at && new Date(o.ordered_at) >= thirtyDaysAgo);

    const productStats = products.map(p => {
      const pOrders = recentOrders.filter(o => o.product_id === p.id);
      const totalSold = pOrders.reduce((s, o) => s + (o.quantity || 0), 0);
      const totalRevenue = pOrders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      const velocity = totalSold / 30;
      const inv = inventory.find(i => i.product_id === p.id);
      const stock = inv?.total_stock || 0;
      const daysUntilOut = velocity > 0 ? Math.floor(stock / velocity) : 9999;
      const sqRevenue = pOrders.filter(o => o.platform?.toLowerCase().includes('squarespace')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      const ebRevenue = pOrders.filter(o => o.platform?.toLowerCase().includes('ebay')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      return { id: p.id, name: p.name, velocity, stock, daysUntilOut, totalSold, totalRevenue, sqRevenue, ebRevenue };
    });

    const bestSellers = [...productStats].filter(p => p.totalRevenue > 0).sort((a, b) => b.totalRevenue - a.totalRevenue).slice(0, 5);
    const worstSellers = [...productStats].sort((a, b) => a.totalRevenue - b.totalRevenue).slice(0, 5);
    const lowDays = productStats.filter(p => p.daysUntilOut < 30 && p.daysUntilOut < 9999).sort((a, b) => a.daysUntilOut - b.daysUntilOut);
    const totalSold30 = productStats.reduce((s, p) => s + p.totalSold, 0);
    const totalStock = productStats.reduce((s, p) => s + p.stock, 0);
    const turnoverRate = totalStock > 0 ? totalSold30 / totalStock : 0;
    const sqTotal = productStats.reduce((s, p) => s + p.sqRevenue, 0);
    const ebTotal = productStats.reduce((s, p) => s + p.ebRevenue, 0);
    const maxPlatform = Math.max(sqTotal, ebTotal, 1);
    const velocityItems = productStats.filter(p => p.velocity > 0).sort((a, b) => b.velocity - a.velocity).slice(0, 10);

    return { productStats, bestSellers, worstSellers, lowDays, turnoverRate, sqTotal, ebTotal, maxPlatform, totalSold30, totalStock, velocityItems };
  }, [products, inventory, orders]);

  if (products.length === 0) {
    return (
      <div className="bg-base-100 rounded-xl border border-base-300 p-12 text-center shadow-sm">
        <Activity size={32} className="text-base-content/20 mx-auto mb-3" />
        <p className="text-base-content/40 text-sm">No data yet — trends will appear after products and orders sync.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Overview Cards */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
          <p className="text-xs font-semibold text-base-content/40 uppercase tracking-wide mb-2">Stock Turnover (30d)</p>
          <p className="text-2xl font-bold text-base-content">{analytics.turnoverRate.toFixed(2)}x</p>
          <p className="text-xs text-base-content/40 mt-1">{analytics.totalSold30} sold / {analytics.totalStock} in stock</p>
        </div>
        <div className="bg-base-100 rounded-xl border border-orange-200 p-4 shadow-sm">
          <p className="text-xs font-semibold text-orange-500/70 uppercase tracking-wide mb-2">Running Low</p>
          <p className="text-2xl font-bold text-orange-500">{analytics.lowDays.length}</p>
          <p className="text-xs text-base-content/40 mt-1">items under 30 days stock</p>
        </div>
      </div>

      {/* Platform Revenue */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-base-content mb-4">Platform Revenue (30 days)</h3>
        <div className="flex flex-col gap-3">
          <div>
            <div className="flex justify-between text-sm mb-1.5">
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700">Squarespace</span>
              <span className="font-semibold">£{analytics.sqTotal.toFixed(2)}</span>
            </div>
            <div className="w-full bg-base-200 rounded-full h-2.5">
              <div className="bg-indigo-400 h-2.5 rounded-full" style={{ width: `${(analytics.sqTotal / analytics.maxPlatform) * 100}%` }} />
            </div>
          </div>
          <div>
            <div className="flex justify-between text-sm mb-1.5">
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700">eBay</span>
              <span className="font-semibold">£{analytics.ebTotal.toFixed(2)}</span>
            </div>
            <div className="w-full bg-base-200 rounded-full h-2.5">
              <div className="bg-yellow-400 h-2.5 rounded-full" style={{ width: `${(analytics.ebTotal / analytics.maxPlatform) * 100}%` }} />
            </div>
          </div>
        </div>
      </div>

      {/* Stock Countdown */}
      {analytics.lowDays.length > 0 && (
        <div className="bg-base-100 rounded-xl border border-orange-200 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-orange-100 bg-orange-50/50 flex items-center gap-2">
            <AlertCircle size={14} className="text-orange-500" />
            <h3 className="text-sm font-semibold text-orange-700">Stock Countdown</h3>
          </div>
          <div className="divide-y divide-base-200">
            {analytics.lowDays.map(p => (
              <div key={p.id} className="flex items-center justify-between px-4 py-3">
                <span className="text-sm text-base-content truncate max-w-[200px]">{p.name}</span>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className={`font-bold text-sm ${p.daysUntilOut < 7 ? 'text-error' : p.daysUntilOut < 14 ? 'text-warning' : 'text-base-content/70'}`}>
                    {p.daysUntilOut} days
                  </span>
                  <span className="text-xs text-base-content/40">({p.stock} left)</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Sales Velocity */}
      {analytics.velocityItems.length > 0 && (
        <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-base-300 bg-base-200/40">
            <h3 className="text-sm font-semibold text-base-content">Sales Velocity (units/day, last 30 days)</h3>
          </div>
          <div className="divide-y divide-base-200">
            {analytics.velocityItems.map(p => (
              <div key={p.id} className="flex items-center justify-between px-4 py-3">
                <span className="text-sm text-base-content truncate max-w-[200px]">{p.name}</span>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-base-content/60">{p.velocity.toFixed(2)}/day</span>
                  <TrendingUp size={12} className="text-success" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Best Sellers */}
      {analytics.bestSellers.length > 0 && (
        <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
          <div className="px-4 py-3 border-b border-base-300 bg-base-200/40 flex items-center gap-2">
            <TrendingUp size={14} className="text-success" />
            <h3 className="text-sm font-semibold text-base-content">Best Sellers (30 days)</h3>
          </div>
          <div className="divide-y divide-base-200">
            {analytics.bestSellers.map((p, i) => (
              <div key={p.id} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="w-5 h-5 rounded-full bg-success/10 text-success flex items-center justify-center text-xs font-bold flex-shrink-0">{i + 1}</span>
                  <span className="text-sm text-base-content truncate">{p.name}</span>
                </div>
                <span className="text-sm font-semibold ml-2">£{p.totalRevenue.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Slowest Movers */}
      <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-base-300 bg-base-200/40 flex items-center gap-2">
          <TrendingDown size={14} className="text-error" />
          <h3 className="text-sm font-semibold text-base-content">Slowest Movers (30 days)</h3>
        </div>
        <div className="divide-y divide-base-200">
          {analytics.worstSellers.map((p, i) => (
            <div key={p.id} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-3 min-w-0">
                <span className="w-5 h-5 rounded-full bg-error/10 text-error flex items-center justify-center text-xs font-bold flex-shrink-0">{i + 1}</span>
                <span className="text-sm text-base-content truncate">{p.name}</span>
              </div>
              <span className="text-sm font-semibold ml-2 text-base-content/50">£{p.totalRevenue.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
