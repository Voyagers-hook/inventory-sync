import React, { useState, useCallback } from 'react';
import { Save, RefreshCw, Bell, Clock, Mail, Key } from 'lucide-react';
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
  const [githubToken, setGithubToken] = useState(getVal('github_token') || '');
  const [tokenVisible, setTokenVisible] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState('');

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 4000); };

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await updateSetting('sync_enabled', syncEnabled ? 'true' : 'false');
      await updateSetting('alert_email', alertEmail);
      await updateSetting('default_low_stock_threshold', lowStockDefault);
      if (githubToken) {
        await updateSetting('github_token', githubToken);
      }
      showToast('Settings saved!');
      onRefresh();
    } catch {
      showToast('Failed to save settings');
    } finally {
      setSaving(false);
    }
  }, [syncEnabled, alertEmail, lowStockDefault, githubToken, onRefresh]);

  const handleSyncNow = useCallback(async () => {
    setSyncing(true);
    try {
      const triggered = await triggerQuickSync();
      if (triggered) {
        showToast('✓ Sync triggered — pushing changes to eBay & Squarespace now (~1 min)');
      } else {
        showToast('⚠ No GitHub token set — add one in the GitHub section below, then try again');
      }
    } catch {
      showToast('Failed to trigger sync');
    } finally {
      setSyncing(false);
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {toast && (
        <div className={`rounded-xl px-4 py-3 text-sm font-medium ${toast.includes('Failed') || toast.includes('⚠') ? 'bg-error/10 text-error border border-error/20' : 'bg-success/10 text-success border border-success/20'}`}>
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
            <p>Hourly sync runs automatically. Manual sync dispatches immediately — requires a GitHub token (see below).</p>
          </div>
        </div>
      </div>

      {/* GitHub Token */}
      <div className="bg-base-100 rounded-xl border border-base-300 shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-base-300 bg-base-200/40 flex items-center gap-2">
          <Key size={14} className="text-base-content/40" />
          <h3 className="text-sm font-semibold text-base-content">GitHub Token</h3>
        </div>
        <div className="p-4 flex flex-col gap-3">
          <div>
            <label className="text-sm font-medium text-base-content block mb-1.5">Personal Access Token</label>
            <p className="text-xs text-base-content/40 mb-2">
              Required for the <strong>Sync Now</strong> button to work immediately.
              Create one at{' '}
              <a
                href="https://github.com/settings/tokens/new?scopes=workflow&description=Inventory+Sync"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary underline"
              >
                github.com/settings/tokens
              </a>
              {' '}with <code className="bg-base-200 px-1 rounded">workflow</code> scope. Stored securely in your database, never in the app bundle.
            </p>
            <div className="flex items-center gap-2 bg-base-200 rounded-xl px-3 py-2.5">
              <Key size={14} className="text-base-content/30 flex-shrink-0" />
              <input
                type={tokenVisible ? 'text' : 'password'}
                className="bg-transparent outline-none text-sm flex-1 text-base-content placeholder:text-base-content/30 font-mono"
                value={githubToken}
                onChange={e => setGithubToken(e.target.value)}
                placeholder={getVal('github_token') ? '••••••••••••••••••••' : 'ghp_xxxxxxxxxxxxxxxxxxxx'}
              />
              <button
                className="text-xs text-base-content/40 hover:text-base-content transition-colors"
                onClick={() => setTokenVisible(v => !v)}
                type="button"
              >
                {tokenVisible ? 'Hide' : 'Show'}
              </button>
            </div>
            {getVal('github_token') && !githubToken && (
              <p className="text-xs text-success mt-1.5">✓ Token saved — Sync Now is active</p>
            )}
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
        <p className="text-xs text-base-content/40 mb-3">
          Immediately pushes all pending stock and price changes to eBay &amp; Squarespace (~1 min).
          Requires GitHub token above.
        </p>
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
