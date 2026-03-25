import React, { useState, useEffect, useCallback } from 'react';
import { LayoutDashboard, ShoppingCart, Package, BarChart3, TrendingUp, Settings as SettingsIcon } from 'lucide-react';
import type { TabName, Product, Inventory, Pricing, Order, SalesTrend, SyncLog, Setting } from './types';
import { fetchProducts, fetchInventory, fetchPricing, fetchOrders, fetchSalesTrends, fetchSyncLogs, fetchSettings } from './utils/supabase';
import { Dashboard } from './components/Dashboard';
import { Orders } from './components/Orders';
import { Products } from './components/Products';
import { Sales } from './components/Sales';
import { Trends } from './components/Trends';
import { Settings } from './components/Settings';

const TABS: { id: TabName; label: string; icon: React.ReactElement }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={16} /> },
  { id: 'orders', label: 'Orders', icon: <ShoppingCart size={16} /> },
  { id: 'products', label: 'Products', icon: <Package size={16} /> },
  { id: 'sales', label: 'Sales', icon: <BarChart3 size={16} /> },
  { id: 'trends', label: 'Trends', icon: <TrendingUp size={16} /> },
  { id: 'settings', label: 'Settings', icon: <SettingsIcon size={16} /> },
];

const App: React.FC = () => {
  const [tab, setTab] = useState<TabName>('dashboard');
  const [loading, setLoading] = useState(true);
  const [products, setProducts] = useState<Product[]>([]);
  const [inventory, setInventory] = useState<Inventory[]>([]);
  const [pricing, setPricing] = useState<Pricing[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [trends, setTrends] = useState<SalesTrend[]>([]);
  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    setError('');
    try {
      const [prods, inv, pri, ords, trds, logs, setts] = await Promise.all([
        fetchProducts(),
        fetchInventory(),
        fetchPricing(),
        fetchOrders(),
        fetchSalesTrends(),
        fetchSyncLogs(),
        fetchSettings(),
      ]);
      setProducts(prods);
      setInventory(inv);
      setPricing(pri);
      setOrders(ords);
      setTrends(trds);
      setSyncLogs(logs);
      setSettings(setts);
    } catch (err) {
      console.error('Failed to load data:', err);
      setError('Failed to connect to database. Please check your connection.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-base-100">
        <div className="flex flex-col items-center gap-4">
          <span className="loading loading-spinner loading-lg text-primary" />
          <p className="text-base-content/60">Loading inventory data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-base-100">
      {/* Header */}
      <div className="bg-base-200 border-b border-base-300 px-4 py-2 flex items-center gap-3">
        <span className="text-xl">📦</span>
        <span className="font-bold text-sm">Voyagers Hook — Inventory Tracker</span>
      </div>

      {/* Tabs */}
      <div className="bg-base-200 border-b border-base-300 sticky top-0 z-10">
        <div className="flex overflow-x-auto px-2 py-1 gap-1">
          {TABS.map(t => (
            <button
              key={t.id}
              className={`btn btn-sm gap-1 whitespace-nowrap ${tab === t.id ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setTab(t.id)}
            >
              {t.icon}
              <span>{t.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="alert alert-error text-sm py-2 mx-3 mt-3">
          {error}
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'dashboard' && <Dashboard products={products} inventory={inventory} pricing={pricing} orders={orders} syncLogs={syncLogs} settings={settings} onRefresh={loadData} />}
        {tab === 'orders' && <Orders orders={orders} onRefresh={loadData} />}
        {tab === 'products' && <Products products={products} inventory={inventory} pricing={pricing} onRefresh={loadData} />}
        {tab === 'sales' && <Sales orders={orders} products={products} />}
        {tab === 'trends' && <Trends products={products} inventory={inventory} orders={orders} trends={trends} />}
        {tab === 'settings' && <Settings settings={settings} onRefresh={loadData} />}
      </div>
    </div>
  );
};

export default App;
