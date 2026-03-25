import React, { useMemo } from 'react';
import { PoundSterling, TrendingUp, ShoppingBag, BarChart3 } from 'lucide-react';
import type { Order, Product } from '../types';

interface SalesProps {
  orders: Order[];
  products: Product[];
}

function isThisMonth(d: string): boolean {
  const date = new Date(d);
  const now = new Date();
  return date.getMonth() === now.getMonth() && date.getFullYear() === now.getFullYear();
}

function isThisWeek(d: string): boolean {
  const date = new Date(d);
  const now = new Date();
  const weekStart = new Date(now);
  weekStart.setDate(now.getDate() - now.getDay());
  weekStart.setHours(0, 0, 0, 0);
  return date >= weekStart;
}

export const Sales: React.FC<SalesProps> = ({ orders, products }) => {
  const stats = useMemo(() => {
    const allTimeRevenue = orders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const monthOrders = orders.filter(o => isThisMonth(o.ordered_at));
    const monthRevenue = monthOrders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const weekOrders = orders.filter(o => isThisWeek(o.ordered_at));
    const weekRevenue = weekOrders.reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);

    const sqRevenue = orders.filter(o => o.platform?.toLowerCase().includes('squarespace')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const ebRevenue = orders.filter(o => o.platform?.toLowerCase().includes('ebay')).reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
    const maxPlatRevenue = Math.max(sqRevenue, ebRevenue, 1);

    const totalUnits = orders.reduce((s, o) => s + (o.quantity || 0), 0);
    const sqUnits = orders.filter(o => o.platform?.toLowerCase().includes('squarespace')).reduce((s, o) => s + (o.quantity || 0), 0);
    const ebUnits = orders.filter(o => o.platform?.toLowerCase().includes('ebay')).reduce((s, o) => s + (o.quantity || 0), 0);

    const avgOrderValue = orders.length > 0 ? allTimeRevenue / orders.length : 0;

    // Top selling products
    const productSales: Record<string, { name: string; units: number; revenue: number }> = {};
    for (const o of orders) {
      const name = o.item_name || products.find(p => p.id === o.product_id)?.name || 'Unknown';
      if (!productSales[name]) productSales[name] = { name, units: 0, revenue: 0 };
      productSales[name].units += o.quantity || 0;
      productSales[name].revenue += Number(o.order_total || o.unit_price || 0);
    }
    const topProducts = Object.values(productSales).sort((a, b) => b.revenue - a.revenue).slice(0, 5);

    // Sales over time (last 7 days)
    const dailySales: { date: string; revenue: number }[] = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date();
      d.setDate(d.getDate() - i);
      const ds = d.toISOString().split('T')[0];
      const dayRevenue = orders
        .filter(o => o.ordered_at && o.ordered_at.startsWith(ds))
        .reduce((s, o) => s + Number(o.order_total || o.unit_price || 0), 0);
      dailySales.push({ date: ds, revenue: dayRevenue });
    }
    const maxDailyRevenue = Math.max(...dailySales.map(d => d.revenue), 1);

    return { allTimeRevenue, monthRevenue, weekRevenue, sqRevenue, ebRevenue, maxPlatRevenue, avgOrderValue, totalUnits, sqUnits, ebUnits, topProducts, dailySales, maxDailyRevenue };
  }, [orders, products]);

  if (orders.length === 0) {
    return (
      <div className="card bg-base-200">
        <div className="card-body items-center text-center p-8">
          <BarChart3 size={32} className="opacity-30" />
          <p className="text-base-content/60">No sales data yet — it will appear after orders sync.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Revenue Cards */}
      <div className="grid grid-cols-3 gap-3">
        <div className="card bg-base-200">
          <div className="card-body p-3">
            <div className="text-xs text-base-content/60 uppercase">All Time</div>
            <div className="text-xl font-bold">£{stats.allTimeRevenue.toFixed(2)}</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-3">
            <div className="text-xs text-base-content/60 uppercase">This Month</div>
            <div className="text-xl font-bold">£{stats.monthRevenue.toFixed(2)}</div>
          </div>
        </div>
        <div className="card bg-base-200">
          <div className="card-body p-3">
            <div className="text-xs text-base-content/60 uppercase">This Week</div>
            <div className="text-xl font-bold">£{stats.weekRevenue.toFixed(2)}</div>
          </div>
        </div>
      </div>

      {/* Avg order value */}
      <div className="card bg-base-200">
        <div className="card-body p-3 flex-row items-center gap-3">
          <PoundSterling size={18} className="opacity-60" />
          <div>
            <div className="text-xs text-base-content/60">Average Order Value</div>
            <div className="text-lg font-bold">£{stats.avgOrderValue.toFixed(2)}</div>
          </div>
        </div>
      </div>

      {/* Revenue by Platform */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-3">Revenue by Platform</h3>
          <div className="flex flex-col gap-2">
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="badge badge-neutral badge-sm">Squarespace</span>
                <span>£{stats.sqRevenue.toFixed(2)}</span>
              </div>
              <div className="w-full bg-base-300 rounded-full h-4">
                <div className="bg-neutral h-4 rounded-full transition-all" style={{ width: `${(stats.sqRevenue / stats.maxPlatRevenue) * 100}%` }} />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-sm mb-1">
                <span className="badge badge-warning badge-sm">eBay</span>
                <span>£{stats.ebRevenue.toFixed(2)}</span>
              </div>
              <div className="w-full bg-base-300 rounded-full h-4">
                <div className="bg-warning h-4 rounded-full transition-all" style={{ width: `${(stats.ebRevenue / stats.maxPlatRevenue) * 100}%` }} />
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Units Sold Breakdown */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-2">Units Sold</h3>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div>
              <div className="text-lg font-bold">{stats.totalUnits}</div>
              <div className="text-xs text-base-content/60">Total</div>
            </div>
            <div>
              <div className="text-lg font-bold">{stats.sqUnits}</div>
              <div className="text-xs text-base-content/60">Squarespace</div>
            </div>
            <div>
              <div className="text-lg font-bold">{stats.ebUnits}</div>
              <div className="text-xs text-base-content/60">eBay</div>
            </div>
          </div>
        </div>
      </div>

      {/* Top Selling Products */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-2">Top Selling Products</h3>
          {stats.topProducts.length === 0 ? (
            <p className="text-sm text-base-content/60">No product sales data.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {stats.topProducts.map((tp, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    <span className="badge badge-sm">{i + 1}</span>
                    <span className="truncate max-w-[200px]">{tp.name}</span>
                  </div>
                  <div className="flex items-center gap-3 text-xs">
                    <span className="text-base-content/60">{tp.units} units</span>
                    <span className="font-semibold">£{tp.revenue.toFixed(2)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Sales Chart (last 7 days) */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <h3 className="text-sm font-semibold mb-3">Last 7 Days</h3>
          <div className="flex items-end gap-1 h-32">
            {stats.dailySales.map((d, i) => (
              <div key={i} className="flex-1 flex flex-col items-center gap-1">
                <div className="text-xs text-base-content/60">
                  {d.revenue > 0 ? `£${d.revenue.toFixed(0)}` : ''}
                </div>
                <div className="w-full flex justify-center">
                  <div
                    className="w-full max-w-[30px] bg-primary rounded-t transition-all"
                    style={{ height: `${Math.max((d.revenue / stats.maxDailyRevenue) * 100, d.revenue > 0 ? 8 : 2)}px` }}
                  />
                </div>
                <div className="text-xs text-base-content/60">
                  {new Date(d.date).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
