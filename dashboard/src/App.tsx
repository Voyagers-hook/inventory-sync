import React, { useState, useEffect, useCallback } from 'react';
import { LayoutDashboard, ShoppingCart, Package, BarChart3, TrendingUp, Settings as SettingsIcon, RefreshCw } from 'lucide-react';
import type { TabName, Product, Inventory, Pricing, Order, SalesTrend, SyncLog, Setting } from './types';
import { fetchProducts, fetchInventory, fetchPricing, fetchOrders, fetchSalesTrends, fetchSyncLogs, fetchSettings } from './utils/supabase';
import { Dashboard } from './components/Dashboard';
import { Orders } from './components/Orders';
import { Products } from './components/Products';
import { Sales } from './components/Sales';
import { Trends } from './components/Trends';
import { Settings } from './components/Settings';

const TABS: { id: TabName; label: string; icon: React.ReactElement }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: <LayoutDashboard size={15} /> },
  { id: 'orders', label: 'Orders', icon: <ShoppingCart size={15} /> },
  { id: 'products', label: 'Products', icon: <Package size={15} /> },
  { id: 'sales', label: 'Sales', icon: <BarChart3 size={15} /> },
  { id: 'trends', label: 'Trends', icon: <TrendingUp size={15} /> },
  { id: 'settings', label: 'Settings', icon: <SettingsIcon size={15} /> },
];

const App: React.FC = () => {
  const [tab, setTab] = useState<TabName>('dashboard');
  const [filterLowStock, setFilterLowStock] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [products, setProducts] = useState<Product[]>([]);
  const [inventory, setInventory] = useState<Inventory[]>([]);
  const [pricing, setPricing] = useState<Pricing[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [trends, setTrends] = useState<SalesTrend[]>([]);
  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [error, setError] = useState('');

  const loadData = useCallback(async (isRefresh = false) => {
    setError('');
    if (isRefresh) setRefreshing(true);
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
      setError('Failed to connect to database. Please refresh the page.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleRefresh = useCallback(() => loadData(true), [loadData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-base-200">
        <div className="flex flex-col items-center gap-4">
          <img
            src="https://raw.githubusercontent.com/Voyagers-hook/images/refs/heads/main/logo%20trans.png"
            alt="Voyagers Hook"
            className="w-20 h-20 object-contain animate-pulse"
          />
          <p className="text-base-content/50 text-sm">Loading inventory data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-base-200">
      {/* Header */}
      <header className="bg-base-100 border-b border-base-300 px-4 py-2.5 flex items-center justify-between flex-shrink-0 shadow-sm">
        <div className="flex items-center gap-3">
          <img
            src="https://raw.githubusercontent.com/Voyagers-hook/images/refs/heads/main/logo%20trans.png"
            alt="Voyagers Hook"
            className="w-9 h-9 object-contain"
          />
          <div>
            <h1 className="font-bold text-sm leading-tight text-base-content">Voyagers Hook</h1>
            <p className="text-xs text-base-content/40 leading-tight">Inventory Tracker</p>
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm gap-1.5 text-base-content/60"
          onClick={handleRefresh}
          disabled={refreshing}
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          <span className="hidden sm:inline text-xs">{refreshing ? 'Refreshing...' : 'Refresh'}</span>
        </button>
      </header>

      {/* Navigation */}
      <nav className="bg-base-100 border-b border-base-300 flex-shrink-0">
        <div className="flex overflow-x-auto">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium whitespace-nowrap border-b-2 transition-all ${
                tab === t.id
                  ? 'border-primary text-primary bg-primary/5'
                  : 'border-transparent text-base-content/50 hover:text-base-content hover:bg-base-200'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>
      </nav>

      {/* Error Banner */}
      {error && (
        <div className="bg-error/10 border-b border-error/20 px-4 py-2 text-error text-sm flex-shrink-0">
          ⚠️ {error}
        </div>
      )}

      {/* Content */}
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-screen-2xl mx-auto p-4 px-6">
          {tab === 'dashboard' && (
            <Dashboard
              products={products}
              inventory={inventory}
              pricing={pricing}
              orders={orders}
              syncLogs={syncLogs}
              settings={settings}
              onRefresh={handleRefresh}
              onNavigate={setTab}
              onNavigateLowStock={() => { setFilterLowStock(true); setTab('products'); }}
            />
          )}
          {tab === 'orders' && <Orders orders={orders} onRefresh={handleRefresh} />}
          {tab === 'products' && <Products products={products} inventory={inventory} pricing={pricing} onRefresh={handleRefresh} initialLowStockFilter={filterLowStock} onFilterApplied={() => setFilterLowStock(false)} />}
          {tab === 'sales' && <Sales orders={orders} products={products} />}
          {tab === 'trends' && <Trends products={products} inventory={inventory} orders={orders} trends={trends} />}
          {tab === 'settings' && <Settings settings={settings} onRefresh={handleRefresh} />}
        </div>
      </main>
    </div>
  );
};

export default App;
