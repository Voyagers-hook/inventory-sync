import React, { useState, useCallback } from 'react';
import { Save, RefreshCw } from 'lucide-react';
import type { Setting } from '../types';
import { updateSetting } from '../utils/supabase';

interface SettingsProps {
  settings: Setting[];
  onRefresh: () => void;
}

export const Settings: React.FC<SettingsProps> = ({ settings, onRefresh }) => {
  const getVal = (key: string) => settings.find(s => s.key === key)?.value || '';

  const [syncEnabled, setSyncEnabled] = useState(getVal('sync_enabled') === 'true');
  const [alertEmail, setAlertEmail] = useState(getVal('alert_email'));
  const [lowStockDefault, setLowStockDefault] = useState(getVal('low_stock_default') || '5');
  const [syncInterval, setSyncInterval] = useState(getVal('sync_interval_minutes') || '60');
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState('');

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await updateSetting('sync_enabled', syncEnabled ? 'true' : 'false');
      await updateSetting('alert_email', alertEmail);
      await updateSetting('low_stock_default', lowStockDefault);
      await updateSetting('sync_interval_minutes', syncInterval);
      showToast('Settings saved!');
      onRefresh();
    } catch (err) {
      console.error('Failed to save settings:', err);
      showToast('Failed to save settings');
    } finally {
      setSaving(false);
    }
  }, [syncEnabled, alertEmail, lowStockDefault, syncInterval, onRefresh]);

  const handleSyncNow = useCallback(async () => {
    try {
      await updateSetting('sync_requested', 'true');
      showToast('Sync requested! It will run shortly.');
    } catch (err) {
      console.error('Failed to request sync:', err);
      showToast('Failed to request sync');
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {toast && <div className="alert alert-success text-sm py-2">{toast}</div>}

      <div className="card bg-base-200">
        <div className="card-body p-4 flex flex-col gap-4">
          {/* Sync Enabled */}
          <div className="flex items-center justify-between">
            <div>
              <div className="font-semibold text-sm">Sync Enabled</div>
              <div className="text-xs text-base-content/60">Automatically sync inventory between platforms</div>
            </div>
            <input
              type="checkbox"
              className="toggle toggle-primary"
              checked={syncEnabled}
              onChange={e => setSyncEnabled(e.target.checked)}
            />
          </div>

          {/* Alert Email */}
          <div>
            <label className="font-semibold text-sm">Alert Email</label>
            <div className="text-xs text-base-content/60 mb-1">Email for low stock and sync error alerts</div>
            <input
              className="input input-bordered w-full"
              type="email"
              value={alertEmail}
              onChange={e => setAlertEmail(e.target.value)}
              placeholder="email@example.com"
            />
          </div>

          {/* Low Stock Threshold */}
          <div>
            <label className="font-semibold text-sm">Default Low Stock Threshold</label>
            <div className="text-xs text-base-content/60 mb-1">Products at or below this quantity trigger alerts</div>
            <input
              className="input input-bordered w-24"
              type="number"
              min={0}
              value={lowStockDefault}
              onChange={e => setLowStockDefault(e.target.value)}
            />
          </div>

          {/* Sync Interval */}
          <div>
            <label className="font-semibold text-sm">Sync Interval (minutes)</label>
            <div className="text-xs text-base-content/60 mb-1">How often the automated sync runs</div>
            <input
              className="input input-bordered w-24"
              type="number"
              min={5}
              value={syncInterval}
              onChange={e => setSyncInterval(e.target.value)}
            />
          </div>

          <button className="btn btn-primary btn-sm self-start" onClick={handleSave} disabled={saving}>
            <Save size={14} />
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* Manual Sync */}
      <div className="card bg-base-200">
        <div className="card-body p-4">
          <div className="font-semibold text-sm mb-1">Manual Sync</div>
          <div className="text-xs text-base-content/60 mb-3">Trigger an immediate sync of all platforms</div>
          <button className="btn btn-secondary btn-sm" onClick={handleSyncNow}>
            <RefreshCw size={14} /> Sync Now
          </button>
        </div>
      </div>
    </div>
  );
};
