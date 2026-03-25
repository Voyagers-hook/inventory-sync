import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, Activity, AlertCircle } from 'lucide-react';
import type { Product, Inventory, Order, SalesTrend } from '../types';

interface TrendsProps {
  products: Product[];
  inventory: Inventory[];
  orders: Order[];
  trends: SalesTrend[];
}

export const Trends: React.FC<TrendsProps> = ({ products, inventory, orders, trends }) => {
  const analytics = useMemo(() => {
    // Calculate sales velocity (units/day) per product over last 30 days
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    const recentOrders = orders.filter(o => new Date(o.ordered_at) >= thirtyDaysAgo);

    const productStats: {
      id: string; name: string; velocity: number; stock: number;
      daysUntilOut: number; totalSold: number; totalRevenue: number;
      sqRevenue: number; ebRevenue: number;
    }[] = [];

    for (const p of products) {
      const pOrders = recentOrders.filter(o => o.product_id === p.id);
      const totalSold = pOrders.reduce((s, o) => s + (o.quantity || 0), 0);
      const totalRevenue = pOrders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      const velocity = totalSold / 30;
      const inv = inventory.find(i => i.product_id === p.id);
      const stock = inv?.total_stock || 0;
      const daysUntilOut = velocity > 0 ? Math.floor(stock / velocity) : 999;

      const sqRevenue = pOrders.filter(o => o.platform?.toLowerCase().includes('squarespace'))
        .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      const ebRevenue = pOrders.filter(o => o.platform?.toLowerCase().includes('ebay'))
        .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);

      productStats.push({ id: p.id, name: p.name, velocity, stock, daysUntilOut, totalSold, totalRevenue, sqRevenue, ebRevenue });
    }

    const bestSellers = [...productStats].sort((a, b) => b.totalRevenue - a.totalRevenue).slice(0, 5);
    const worstSellers = [...productStats].sort((a, b) => a.totalRevenue - b.totalRevenue).slice(0, 5);
    const lowDays = productStats.filter(p => p.daysUntilOut < 30 && p.daysUntilOut < 999).sort((a, b) => a.daysUntilOut - b.daysUntilOut);

    // Stock turnover rate = units sold / average stock
    const totalSold30 = productStats.reduce((s, p) => s + p.totalSold, 0);
    const totalStock = productStats.reduce((s, p) => s + p.stock, 0);
    const turnoverRate = totalStock > 0 ? (totalSold30 / totalStock) : 0;

    // Platform comparison
    const sqTotal = productStats.reduce((s, p) => s + p.sqRevenue, 0);
    const ebTotal = productStats.reduce((s, p) => s + p.ebRevenue, 0);

    return { productStats, bestSellers, worstSellers, lowDays, turnoverRate, sqTotal, ebTotal, totalSold30, totalStock };
  }, [products, inventory, orders, trends]);

  if (products.length === 0) {
    return (
      <div className="card bg-base-200">
        <div className="card-body items-center text-center p-8">
          <Activity size={32} className="opacity-30" />
          <p className="text-base-content/60">No data yet — trends will appear after products and orders sync.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Overview */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card bg-base-200">
          <div className="card-body p-3">
            <div className="text-xs text-base-content/60 uppercase">Stock Turnover (30d)</div>
            <div className="text-xl font-bold">{analytics.turnoverRate.toFixed(2)}x</div>
            <div className="text-xs text-base-content/60">{analytics.totalSold30} sold / {analytics.totalStock} in stock</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-3">
            <div className="text-xs text-base-content/60 uppercase">Running Low</div>
            <div className="text-xl font-bold text-warning">{analytics.lowDays.length}</div>
            <div className="text-xs text-base-content/60">products &lt; 30 days stock</div>
          </div>
        </div>
      </div>

      {/* Platform Comparison */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-3">Platform Revenue (30 days)</h3>
          <div className="flex flex-col gap-2">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="badge badge-neutral badge-sm">Squarespace</span>
                <span>£{analytics.sqTotal.toFixed(2)}</span>
              </div>
              <progress className="progress progress-primary w-full" value={analytics.sqTotal} max={Math.max(analytics.sqTotal, analytics.ebTotal, 1)} />
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="badge badge-warning badge-sm">eBay</span>
                <span>£{analytics.ebTotal.toFixed(2)}</span>
              </div>
              <progress className="progress progress-warning w-full" value={analytics.ebTotal} max={Math.max(analytics.sqTotal, analytics.ebTotal, 1)} />
            </div>
          </div>
        </div>
      </div>

      {/* Sales Velocity */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-2">Sales Velocity (units/day)</h3>
          {analytics.productStats.filter(p => p.velocity > 0).length === 0 ? (
            <p className="text-sm text-base-content/60">No sales velocity data in last 30 days.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {analytics.productStats.filter(p => p.velocity > 0).sort((a, b) => b.velocity - a.velocity).slice(0, 10).map(p => (
                <div key={p.id} className="flex items-center justify-between text-sm">
                  <span className="truncate max-w-[180px]">{p.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{p.velocity.toFixed(2)}/day</span>
                    <TrendingUp size={12} className="text-success opacity-60" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Days Until Out of Stock */}
      {analytics.lowDays.length > 0 && (
        <div className="card bg-base-200">
          <div className="card-body p-4">
            <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
              <AlertCircle size={14} className="text-warning" />
              Stock Countdown
            </h3>
            <div className="flex flex-col gap-2">
              {analytics.lowDays.map(p => (
                <div key={p.id} className="flex items-center justify-between text-sm">
                  <span className="truncate max-w-[180px]">{p.name}</span>
                  <div className="flex items-center gap-2">
                    <span className={`font-bold ${p.daysUntilOut < 7 ? 'text-error' : p.daysUntilOut < 14 ? 'text-warning' : ''}`}>
                      {p.daysUntilOut} days
                    </span>
                    <span className="text-xs text-base-content/60">({p.stock} left)</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Best Sellers */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
            <TrendingUp size={14} className="text-success" /> Best Sellers (30 days)
          </h3>
          {analytics.bestSellers.filter(p => p.totalRevenue > 0).length === 0 ? (
            <p className="text-sm text-base-content/60">No sales data.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {analytics.bestSellers.filter(p => p.totalRevenue > 0).map((p, i) => (
                <div key={p.id} className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    <span className="badge badge-success badge-sm">{i + 1}</span>
                    <span className="truncate max-w-[160px]">{p.name}</span>
                  </div>
                  <span className="font-semibold">£{p.totalRevenue.toFixed(2)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Worst Sellers */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
            <TrendingDown size={14} className="text-error" /> Slowest Movers (30 days)
          </h3>
          <div className="flex flex-col gap-2">
            {analytics.worstSellers.map((p, i) => (
              <div key={p.id} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <span className="badge badge-error badge-sm">{i + 1}</span>
                  <span className="truncate max-w-[160px]">{p.name}</span>
                </div>
                <span className="font-semibold">£{p.totalRevenue.toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
