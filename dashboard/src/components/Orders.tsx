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
    default: return <span className="badge badge-ghost badge-sm">{status || 'Pending'}</span>;
  }
}

const CARRIERS = ['Royal Mail', 'DPD', 'Hermes/Evri', 'DHL', 'UPS', 'FedEx', 'Yodel', 'ParcelForce', 'Other'];

const CopyButton: React.FC<{ text: string }> = ({ text }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }, [text]);

  if (!text) return null;
  return (
    <button className="btn btn-ghost btn-xs px-1" onClick={handleCopy} title="Copy">
      {copied ? <Check size={12} className="text-success" /> : <Copy size={12} className="opacity-60" />}
    </button>
  );
};

const OrderRow: React.FC<{ order: Order; onUpdate: () => void }> = ({ order, onUpdate }) => {
  const [expanded, setExpanded] = useState(false);
  const [trackingNumber, setTrackingNumber] = useState(order.tracking_number || '');
  const [trackingCarrier, setTrackingCarrier] = useState(order.tracking_carrier || 'Royal Mail');
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState('');

  const handleUpdateTracking = useCallback(async () => {
    if (!trackingNumber.trim()) return;
    setSaving(true);
    try {
      await updateOrder(order.id, {
        tracking_number: trackingNumber.trim(),
        tracking_carrier: trackingCarrier,
        fulfillment_status: 'SHIPPED',
      });
      setToast('Tracking saved! Will sync to platform next run.');
      setTimeout(() => setToast(''), 3000);
      onUpdate();
    } catch (err) {
      console.error('Failed to update tracking:', err);
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
    <div className="card bg-base-200 mb-2">
      <div
        className="flex items-center gap-2 p-3 cursor-pointer hover:bg-base-300 rounded-t-xl transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex-1 flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-semibold">{order.order_number || order.platform_order_id || '—'}</span>
          {platformBadge(order.platform)}
          {statusBadge(order.fulfillment_status)}
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-base-content/60 text-xs hidden sm:inline">{formatDate(order.ordered_at)}</span>
          <span className="font-semibold">£{Number(order.order_total || order.unit_price || 0).toFixed(2)}</span>
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </div>
      </div>

      {expanded && (
        <div className="p-3 pt-0 flex flex-col gap-3 border-t border-base-300">
          {toast && <div className="alert alert-success text-xs py-1">{toast}</div>}

          {/* Order info */}
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-base-content/60 text-xs">Customer</span>
              <div className="flex items-center gap-1">
                <span>{order.customer_name || '—'}</span>
                <CopyButton text={order.customer_name} />
              </div>
            </div>
            <div>
              <span className="text-base-content/60 text-xs">Email</span>
              <div className="flex items-center gap-1">
                <span className="text-xs break-all">{order.customer_email || '—'}</span>
                <CopyButton text={order.customer_email} />
              </div>
            </div>
            <div>
              <span className="text-base-content/60 text-xs">Item</span>
              <div>{order.item_name || '—'}</div>
            </div>
            <div>
              <span className="text-base-content/60 text-xs">Qty</span>
              <div>{order.quantity}</div>
            </div>
          </div>

          {/* Shipping Address */}
          {addressLines.length > 0 && (
            <div>
              <span className="text-base-content/60 text-xs uppercase tracking-wide">Shipping Address</span>
              <div className="flex flex-col gap-1 mt-1 bg-base-300 rounded-lg p-2">
                {addressLines.map((line, i) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span className="text-base-content/60 text-xs w-16">{line.label}:</span>
                      <span>{line.value}</span>
                    </div>
                    <CopyButton text={line.value} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tracking */}
          <div>
            <span className="text-base-content/60 text-xs uppercase tracking-wide">Tracking</span>
            {order.tracking_number ? (
              <div className="flex items-center gap-2 mt-1 bg-base-300 rounded-lg p-2 text-sm">
                <Truck size={14} className="opacity-60" />
                <span className="font-semibold">{order.tracking_carrier || 'Unknown'}</span>
                <span className="font-mono">{order.tracking_number}</span>
                <CopyButton text={order.tracking_number} />
                {statusBadge(order.fulfillment_status)}
              </div>
            ) : (
              <div className="flex flex-col gap-2 mt-1">
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
                  className="btn btn-primary btn-sm self-start"
                  onClick={handleUpdateTracking}
                  disabled={saving || !trackingNumber.trim()}
                >
                  <Truck size={14} />
                  {saving ? 'Saving...' : 'Update Tracking'}
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
    if (filterStatus !== 'all' && (o.fulfillment_status || 'PENDING').toUpperCase() !== filterStatus) return false;
    if (search) {
      const q = search.toLowerCase();
      const matchName = o.customer_name?.toLowerCase().includes(q);
      const matchOrder = o.order_number?.toLowerCase().includes(q);
      const matchPlatformOrder = o.platform_order_id?.toLowerCase().includes(q);
      if (!matchName && !matchOrder && !matchPlatformOrder) return false;
    }
    return true;
  });

  return (
    <div className="flex flex-col gap-3">
      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <label className="input input-bordered input-sm flex items-center gap-2 flex-1 min-w-[150px]">
          <Search size={14} className="opacity-60" />
          <input
            type="search"
            className="grow"
            placeholder="Search name or order #..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </label>
        <select className="select select-bordered select-sm" value={filterPlatform} onChange={e => setFilterPlatform(e.target.value)}>
          <option value="all">All Platforms</option>
          <option value="squarespace">Squarespace</option>
          <option value="ebay">eBay</option>
        </select>
        <select className="select select-bordered select-sm" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="all">All Statuses</option>
          <option value="PENDING">Pending</option>
          <option value="SHIPPED">Shipped</option>
          <option value="DELIVERED">Delivered</option>
        </select>
      </div>

      {/* Order count */}
      <div className="text-xs text-base-content/60">
        {filtered.length} order{filtered.length !== 1 ? 's' : ''} {search || filterPlatform !== 'all' || filterStatus !== 'all' ? '(filtered)' : ''}
      </div>

      {/* Orders list */}
      {filtered.length === 0 ? (
        <div className="card bg-base-200">
          <div className="card-body items-center text-center p-8">
            <p className="text-base-content/60">
              {orders.length === 0
                ? "No orders yet — they'll appear after the first sync runs."
                : 'No orders match your filters.'}
            </p>
          </div>
        </div>
      ) : (
        filtered.map(o => <OrderRow key={o.id} order={o} onUpdate={onRefresh} />)
      )}
    </div>
  );
};
