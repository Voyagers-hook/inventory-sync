import React, { useState, useCallback } from 'react';
import { Save, RefreshCw, Bell, Clock, Mail } from 'lucide-react';
import type { Setting } from '../types';
import { updateSetting, triggerQuickSync } from '../utils/supabase';

interface SettingsProps {
  settings: Setting[];
  onRefresh: () => void;
}

export const Settings: React.FC<SettingsProps> = ({ settings, onRefresh }) => {
  const getVal = (key: string) => settings.find(s => s.key === key)?.value || '';

  const [syncEnabled, setSyncEnabled] = useState(getVal('sync_enabled') !== 'false');
  const [alertEmail, setAlertEmail] = useState(getVal('alert_email') || 'joebaynton@gmail.com');
  const [lowStockDefault, setLowStockDefault] = useState(getVal('default_low_stock_threshold') || '5');
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState('');

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await updateSetting('sync_enabled', syncEnabled ? 'true' : 'false');
      await updateSetting('alert_email', alertEmail);
      await updateSetting('default_low_stock_threshold', lowStockDefault);
      showToast('Settings saved!');
      onRefresh();
    } catch {
      showToast('Failed to save settings');
    } finally {
      setSaving(false);
    }
  }, [syncEnabled, alertEmail, lowStockDefault, onRefresh]);

  const handleSyncNow = useCallback(async () => {
    setSyncing(true);
    try {
      await updateSetting('manual_sync_requested', 'true');
      const triggered = await triggerQuickSync();
      showToast(triggered ? '✓ Sync triggered — running now (~1 min)' : 'Sync queued — will run within the hour');
    } catch {
      showToast('Failed to trigger sync');
    } finally {
      setSyncing(false);
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {toast && (
        <div className={`rounded-xl px-4 py-3 text-sm font-medium ${toast.includes('Failed') ? 'bg-error/10 text-error border border-error/20' : 'bg-success/10 text-success border border-success/20'}`}>
          {toast}
        </div>
      )}

      {/* Sync Settings */}
      <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-base-300 bg-base-200/40 flex items-center gap-2">
          <Clock size={14} className="text-base-content/40" />
          <h3 className="text-sm font-semibold text-base-content">Sync Settings</h3>
        </div>
        <div className="p-4 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-base-content">Hourly Sync</p>
              <p className="text-xs text-base-content/40 mt-0.5">Automatically sync inventory every hour via GitHub Actions</p>
            </div>
            <input
              type="checkbox"
              className="toggle toggle-primary"
              checked={syncEnabled}
              onChange={e => setSyncEnabled(e.target.checked)}
            />
          </div>
          <div className="bg-base-200 rounded-xl p-3 text-xs text-base-content/50">
            <p className="font-medium text-base-content/70 mb-1">How syncing works</p>
            <p>GitHub Actions runs every hour for free (unlimited on a public repo). Manual sync triggers the workflow immediately.</p>
          </div>
        </div>
      </div>

      {/* Alert Settings */}
      <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-base-300 bg-base-200/40 flex items-center gap-2">
          <Bell size={14} className="text-base-content/40" />
          <h3 className="text-sm font-semibold text-base-content">Alerts</h3>
        </div>
        <div className="p-4 flex flex-col gap-4">
          <div>
            <label className="text-sm font-medium text-base-content block mb-1.5">Alert Email</label>
            <p className="text-xs text-base-content/40 mb-2">Receive low stock and sync error notifications</p>
            <div className="flex items-center gap-2 bg-base-200 rounded-xl px-3 py-2.5">
              <Mail size={14} className="text-base-content/30 flex-shrink-0" />
              <input
                type="email"
                className="bg-transparent outline-none text-sm flex-1 text-base-content placeholder:text-base-content/30"
                value={alertEmail}
                onChange={e => setAlertEmail(e.target.value)}
                placeholder="email@example.com"
              />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium text-base-content block mb-1.5">Default Low Stock Threshold</label>
            <p className="text-xs text-base-content/40 mb-2">Products at or below this quantity will be flagged as low stock</p>
            <div className="flex items-center gap-2">
              <input
                type="number"
                className="input input-bordered w-24"
                min={0}
                value={lowStockDefault}
                onChange={e => setLowStockDefault(e.target.value)}
              />
              <span className="text-sm text-base-content/50">units</span>
            </div>
          </div>
        </div>
      </div>

      {/* Save Button */}
      <button className="btn btn-primary gap-2" onClick={handleSave} disabled={saving}>
        <Save size={15} />
        {saving ? 'Saving...' : 'Save Settings'}
      </button>

      {/* Manual Sync */}
      <div className="bg-base-100 rounded-xl border border-base-300 p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-base-content mb-1">Manual Sync</h3>
        <p className="text-xs text-base-content/40 mb-3">Triggers the sync workflow immediately — completes in ~1 minute</p>
        <button className="btn btn-secondary btn-sm gap-1.5" onClick={handleSyncNow} disabled={syncing}>
          <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
          {syncing ? 'Triggering...' : 'Sync Now'}
        </button>
      </div>

      {/* Info */}
      <div className="bg-base-200 rounded-xl p-4 text-xs text-base-content/50">
        <p className="font-semibold text-base-content/70 mb-2">System Information</p>
        <div className="flex flex-col gap-1">
          <div className="flex justify-between"><span>Database</span><span className="font-medium">Supabase (Free tier)</span></div>
          <div className="flex justify-between"><span>Sync Engine</span><span className="font-medium">GitHub Actions (Free tier)</span></div>
          <div className="flex justify-between"><span>Dashboard</span><span className="font-medium">GitHub Pages (Free)</span></div>
          <div className="flex justify-between"><span>Running costs</span><span className="font-medium text-success">£0.00/month</span></div>
        </div>
      </div>
    </div>
  );
};

