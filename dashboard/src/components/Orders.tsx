import React, { useState, useCallback } from 'react';
import { Search, ChevronDown, ChevronUp, Copy, Check, Truck } from 'lucide-react';
import type { Order } from '../types';
import { updateOrder } from '../utils/supabase';

interface OrdersProps {
  orders: Order[];
  onRefresh: () => void;
}

function formatDate(d: string): string {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function PlatformBadge({ platform }: { platform: string }) {
  if (platform?.toLowerCase().includes('squarespace'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">Squarespace</span>;
  if (platform?.toLowerCase().includes('ebay'))
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-50 text-yellow-700 border border-yellow-100">eBay</span>;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{platform || '—'}</span>;
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || 'PENDING').toUpperCase();
  if (s === 'CANCELLED' || s === 'CANCELED')
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-600 border border-red-100">Cancelled</span>;
  if (s === 'PENDING' || s === 'NOT_STARTED')
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-orange-50 text-orange-600 border border-orange-100">Pending</span>;
  if (s === 'SHIPPED' || s === 'IN_PROGRESS')
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700 border border-blue-100">Shipped</span>;
  if (s === 'DELIVERED' || s === 'FULFILLED')
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700 border border-green-100">Fulfilled</span>;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">{status || 'Pending'}</span>;
}

const CARRIERS = ['Royal Mail', 'DPD', 'Hermes/Evri', 'DHL', 'UPS', 'FedEx', 'Yodel', 'ParcelForce', 'Other'];

const CopyButton: React.FC<{ text: string }> = ({ text }) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [text]);
  if (!text) return null;
  return (
    <button className="p-1 rounded hover:bg-base-300 transition-colors ml-1" onClick={handleCopy} title="Copy">
      {copied ? <Check size={11} className="text-success" /> : <Copy size={11} className="text-base-content/30" />}
    </button>
  );
};

const OrderCard: React.FC<{ order: Order; onUpdate: () => void }> = ({ order, onUpdate }) => {
  const [expanded, setExpanded] = useState(false);
  const [trackingNumber, setTrackingNumber] = useState(order.tracking_number || '');
  const [trackingCarrier, setTrackingCarrier] = useState(order.tracking_carrier || 'Royal Mail');
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState('');

  const handleSaveTracking = useCallback(async () => {
    if (!trackingNumber.trim()) return;
    setSaving(true);
    try {
      await updateOrder(order.id, {
        tracking_number: trackingNumber.trim(),
        tracking_carrier: trackingCarrier,
        fulfillment_status: 'SHIPPED',
      });
      setToast('Tracking saved — will sync to platform on next run.');
      setTimeout(() => setToast(''), 3000);
      onUpdate();
    } catch {
      setToast('Failed to save tracking');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [order.id, trackingNumber, trackingCarrier, onUpdate]);

  const addressLines = [
    { label: 'Name', value: order.customer_name },
    { label: 'Line 1', value: order.shipping_address_line1 },
    { label: 'Line 2', value: order.shipping_address_line2 },
    { label: 'City', value: order.shipping_city },
    { label: 'County', value: order.shipping_county },
    { label: 'Postcode', value: order.shipping_postcode },
    { label: 'Country', value: order.shipping_country },
  ].filter(l => l.value);

  return (
    <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-base-200/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex-1 flex flex-wrap items-center gap-2 min-w-0">
          <span className="font-mono text-xs font-semibold text-base-content/70">
            {order.order_number || order.platform_order_id?.slice(0, 12) || '—'}
          </span>
          <PlatformBadge platform={order.platform} />
          <StatusBadge status={order.fulfillment_status} />
          {order.customer_name && (
            <span className="text-sm text-base-content/70 hidden sm:inline truncate max-w-[150px]">{order.customer_name}</span>
          )}
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs text-base-content/40 hidden sm:inline">{formatDate(order.ordered_at)}</span>
          <span className="font-bold text-sm text-base-content">£{Number(order.order_total || order.unit_price || 0).toFixed(2)}</span>
          {expanded ? <ChevronUp size={15} className="text-base-content/30" /> : <ChevronDown size={15} className="text-base-content/30" />}
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-base-200 flex flex-col gap-4 pt-4">
          {toast && (
            <div className={`rounded-xl px-3 py-2 text-xs font-medium ${toast.includes('Failed') ? 'bg-error/10 text-error' : 'bg-success/10 text-success'}`}>
              {toast}
            </div>
          )}

          {/* Order details */}
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Customer</p>
              <div className="flex items-center">
                <span className="font-medium">{order.customer_name || '—'}</span>
                <CopyButton text={order.customer_name} />
              </div>
            </div>
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Email</p>
              <div className="flex items-center">
                <span className="text-xs break-all">{order.customer_email || '—'}</span>
                <CopyButton text={order.customer_email} />
              </div>
            </div>
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Item</p>
              <span>{order.item_name || order.sku || '—'}</span>
            </div>
            <div>
              <p className="text-xs text-base-content/40 mb-0.5">Qty × Price</p>
              <span>{order.quantity} × £{Number(order.unit_price || 0).toFixed(2)}</span>
            </div>
          </div>

          {/* Address */}
          {addressLines.length > 0 && (
            <div>
              <p className="text-xs text-base-content/40 uppercase tracking-wide mb-2">Shipping Address</p>
              <div className="bg-base-200 rounded-xl p-3 flex flex-col gap-1.5">
                {addressLines.map((line, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-base-content/40 w-14 flex-shrink-0">{line.label}</span>
                      <span className="font-medium">{line.value}</span>
                    </div>
                    <CopyButton text={line.value} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tracking */}
          <div>
            <p className="text-xs text-base-content/40 uppercase tracking-wide mb-2">Tracking</p>
            {order.tracking_number ? (
              <div className="bg-base-200 rounded-xl p-3 flex items-center gap-3 text-sm">
                <Truck size={14} className="text-base-content/40" />
                <span className="font-semibold">{order.tracking_carrier || 'Unknown'}</span>
                <span className="font-mono text-base-content/70">{order.tracking_number}</span>
                <CopyButton text={order.tracking_number} />
                <StatusBadge status={order.fulfillment_status} />
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <input
                    type="text"
                    className="input input-bordered input-sm flex-1"
                    placeholder="Tracking number"
                    value={trackingNumber}
                    onChange={e => setTrackingNumber(e.target.value)}
                  />
                  <select
                    className="select select-bordered select-sm"
                    value={trackingCarrier}
                    onChange={e => setTrackingCarrier(e.target.value)}
                  >
                    {CARRIERS.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                <button
                  className="btn btn-primary btn-sm self-start gap-1.5"
                  onClick={handleSaveTracking}
                  disabled={saving || !trackingNumber.trim()}
                >
                  <Truck size={13} />
                  {saving ? 'Saving...' : 'Add Tracking'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export const Orders: React.FC<OrdersProps> = ({ orders, onRefresh }) => {
  const [search, setSearch] = useState('');
  const [filterPlatform, setFilterPlatform] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');

  const filtered = orders.filter(o => {
    if (filterPlatform !== 'all' && !o.platform?.toLowerCase().includes(filterPlatform)) return false;
    if (filterStatus !== 'all') {
      const s = (o.fulfillment_status || 'PENDING').toUpperCase();
      if (filterStatus === 'PENDING' && (s !== 'PENDING' && s !== 'NOT_STARTED' || s === 'CANCELLED' || s === 'CANCELED')) return false;
      if (filterStatus !== 'PENDING' && filterStatus !== 'all' && (s === 'CANCELLED' || s === 'CANCELED')) return false;
      if (filterStatus === 'SHIPPED' && s !== 'SHIPPED' && s !== 'IN_PROGRESS') return false;
      if (filterStatus === 'DELIVERED' && s !== 'DELIVERED' && s !== 'FULFILLED') return false;
    }
    if (search) {
      const q = search.toLowerCase();
      if (!o.customer_name?.toLowerCase().includes(q) &&
          !o.order_number?.toLowerCase().includes(q) &&
          !o.platform_order_id?.toLowerCase().includes(q) &&
          !o.item_name?.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-3 shadow-sm flex flex-wrap gap-2">
        <div className="flex items-center gap-2 bg-base-200 rounded-lg px-3 py-2 flex-1 min-w-[160px]">
          <Search size={13} className="text-base-content/30 flex-shrink-0" />
          <input
            type="search"
            className="bg-transparent outline-none text-sm flex-1 placeholder:text-base-content/30"
            placeholder="Search orders..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="select select-bordered select-sm" value={filterPlatform} onChange={e => setFilterPlatform(e.target.value)}>
          <option value="all">All Platforms</option>
          <option value="squarespace">Squarespace</option>
          <option value="ebay">eBay</option>
        </select>
        <select className="select select-bordered select-sm" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="all">All Statuses</option>
          <option value="PENDING">Pending</option>
          <option value="CANCELLED">Cancelled</option>
          <option value="SHIPPED">Shipped</option>
          <option value="DELIVERED">Fulfilled</option>
        </select>
      </div>

      <p className="text-xs text-base-content/40">
        {filtered.length} order{filtered.length !== 1 ? 's' : ''}
        {(search || filterPlatform !== 'all' || filterStatus !== 'all') ? ' (filtered)' : ''}
      </p>

      {filtered.length === 0 ? (
        <div className="bg-base-100 rounded-xl border border-base-300 p-12 text-center shadow-sm">
          <p className="text-base-content/40 text-sm">
            {orders.length === 0 ? "No orders yet — they'll appear after the first sync." : 'No orders match your filters.'}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {filtered.map(o => <OrderCard key={o.id} order={o} onUpdate={onRefresh} />)}
        </div>
      )}
    </div>
  );
};
