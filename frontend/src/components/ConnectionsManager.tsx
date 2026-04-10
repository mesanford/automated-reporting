"use client";

import React, { useState, useEffect } from 'react';
import { CalendarRange, Plus, RefreshCcw, CheckCircle2, Zap } from 'lucide-react';
import { SyncMonitor } from './SyncMonitor';
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

const MAX_SYNC_WINDOW_DAYS = 90;

const toIsoDate = (value: Date) => {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const parseIsoDateParts = (isoDate: string) => {
  const [year, month, day] = isoDate.split('-').map((part) => Number(part));
  if (!year || !month || !day) {
    return null;
  }
  return { year, month, day };
};

const toUtcIsoDate = (value: Date) => {
  const year = value.getUTCFullYear();
  const month = `${value.getUTCMonth() + 1}`.padStart(2, '0');
  const day = `${value.getUTCDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const shiftIsoDate = (daysAgo: number) => {
  const value = new Date();
  value.setHours(12, 0, 0, 0);
  value.setDate(value.getDate() - daysAgo);
  return toIsoDate(value);
};

const getCurrentQuarterStartIsoDate = () => {
  const value = new Date();
  const quarterMonth = Math.floor(value.getMonth() / 3) * 3;
  value.setHours(12, 0, 0, 0);
  value.setMonth(quarterMonth);
  value.setDate(1);
  return toIsoDate(value);
};

const diffDaysInclusive = (startDate: string, endDate: string) => {
  const start = parseIsoDateParts(startDate);
  const end = parseIsoDateParts(endDate);
  if (!start || !end) {
    return Number.NaN;
  }
  const startUtc = Date.UTC(start.year, start.month - 1, start.day);
  const endUtc = Date.UTC(end.year, end.month - 1, end.day);
  return Math.floor((endUtc - startUtc) / 86400000) + 1;
};

const shiftExistingIsoDate = (isoDate: string, deltaDays: number) => {
  const parsed = parseIsoDateParts(isoDate);
  if (!parsed) {
    return isoDate;
  }
  const value = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day));
  value.setUTCDate(value.getUTCDate() + deltaDays);
  return toUtcIsoDate(value);
};

const deriveComparisonRange = (startDate: string, endDate: string) => {
  const dayCount = diffDaysInclusive(startDate, endDate);
  if (Number.isNaN(dayCount) || dayCount <= 0) {
    return {
      comparisonStartDate: shiftIsoDate(27),
      comparisonEndDate: shiftIsoDate(14),
    };
  }

  return {
    comparisonStartDate: shiftExistingIsoDate(startDate, -dayCount),
    comparisonEndDate: shiftExistingIsoDate(endDate, -dayCount),
  };
};

const shiftYearIsoDate = (dateStr: string, yearsOffset: number) => {
  const parsed = parseIsoDateParts(dateStr);
  if (!parsed) return dateStr;
  const value = new Date(Date.UTC(parsed.year + yearsOffset, parsed.month - 1, parsed.day));
  return toUtcIsoDate(value);
};

const deriveComparisonRangeYearly = (startDate: string, endDate: string) => {
  return {
    comparisonStartDate: shiftYearIsoDate(startDate, -1),
    comparisonEndDate: shiftYearIsoDate(endDate, -1),
  };
};

const parseApiResponse = async (response: Response) => {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const message =
      typeof payload === 'object' &&
      payload !== null &&
      'message' in payload &&
      typeof (payload as { message?: unknown }).message === 'string'
        ? ((payload as { message: string }).message)
        : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return payload;
};

interface Connection {
  id: number;
  platform: string;
  account_name: string;
  account_id: string;
  is_active: number;
  available_accounts?: AdAccount[];
  selected_account_ids?: string[];
}

interface AdAccount {
  id: string;
  name: string;
  status?: string;
  currency?: string;
}

interface ConnectionsManagerProps {
  onSyncComplete: (reportData: unknown) => void;
  isSyncing: boolean;
  setIsSyncing: (val: boolean) => void;
  setUploadStep: (step: string) => void;
}

type SyncPreset = '30d' | '60d' | 'current_quarter' | 'custom';

type SyncTarget =
  | { type: 'single'; connectionId: number }
  | { type: 'all' };

interface ConnectionDiagnostic {
  connectionId: number;
  platform: string;
  accountName: string;
  accountId?: string;
  status: 'ok' | 'warning' | 'error';
  selectedAdAccounts: number;
  issues: string[];
}

const PLATFORMS = [
  { id: 'google', name: 'Google Ads', color: 'blue' },
  { id: 'meta', name: 'Meta Ads', color: 'indigo' },
  { id: 'linkedin', name: 'LinkedIn Ads', color: 'sky' },
  { id: 'tiktok', name: 'TikTok Ads', color: 'pink' },
  { id: 'microsoft', name: 'Microsoft Ads', color: 'emerald' }
];

export const ConnectionsManager: React.FC<ConnectionsManagerProps> = ({ 
  onSyncComplete, 
  isSyncing, 
  setIsSyncing,
  setUploadStep 
}) => {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [showAddModal, setShowAddModal] = useState(false);
  const [syncAllToast, setSyncAllToast] = useState<string | null>(null);
  const [showAccountModalFor, setShowAccountModalFor] = useState<number | null>(null);
  const [accountQuery, setAccountQuery] = useState('');
  const [isLoadingAccounts, setIsLoadingAccounts] = useState(false);
  const [isSavingAccounts, setIsSavingAccounts] = useState(false);
  const [modalAccounts, setModalAccounts] = useState<AdAccount[]>([]);
  const [modalSelectedIds, setModalSelectedIds] = useState<string[]>([]);
  const [isRunningDiagnostics, setIsRunningDiagnostics] = useState(false);
  const [showDiagnosticsModal, setShowDiagnosticsModal] = useState(false);
  const [diagnosticsOverall, setDiagnosticsOverall] = useState<'ok' | 'warning' | 'error'>('ok');
  const [diagnosticsResults, setDiagnosticsResults] = useState<ConnectionDiagnostic[]>([]);
  const [syncingConnectionId, setSyncingConnectionId] = useState<number | null>(null);
  const [pendingSyncTarget, setPendingSyncTarget] = useState<SyncTarget | null>(null);
  const [syncPreset, setSyncPreset] = useState<SyncPreset>('30d');
  const [syncStartDate, setSyncStartDate] = useState<string>(shiftIsoDate(29));
  const [syncEndDate, setSyncEndDate] = useState<string>(shiftIsoDate(0));
  const defaultComparisonRange = deriveComparisonRange(shiftIsoDate(29), shiftIsoDate(0));
  const [comparisonStartDate, setComparisonStartDate] = useState<string>(defaultComparisonRange.comparisonStartDate);
  const [comparisonEndDate, setComparisonEndDate] = useState<string>(defaultComparisonRange.comparisonEndDate);
  const [comparisonTouched, setComparisonTouched] = useState(false);
  const [comparisonMatchMode, setComparisonMatchMode] = useState<'previous_period' | 'previous_year'>('previous_period');

  const syncComparisonToCurrent = (startDate: string, endDate: string, mode: 'previous_period' | 'previous_year' = comparisonMatchMode) => {
    const next = mode === 'previous_year' 
      ? deriveComparisonRangeYearly(startDate, endDate)
      : deriveComparisonRange(startDate, endDate);
    setComparisonStartDate(next.comparisonStartDate);
    setComparisonEndDate(next.comparisonEndDate);
  };

  const updateCurrentRange = (startDate: string, endDate: string, forceComparisonSync = false) => {
    setSyncStartDate(startDate);
    setSyncEndDate(endDate);
    if (forceComparisonSync || !comparisonTouched) {
      syncComparisonToCurrent(startDate, endDate, comparisonMatchMode);
    }
  };

  const applySyncPreset = (preset: SyncPreset) => {
    setSyncPreset(preset);
    if (preset === 'custom') {
      return;
    }

    const nextEndDate = shiftIsoDate(0);
    let nextStartDate: string;
    
    if (preset === 'current_quarter') {
      nextStartDate = getCurrentQuarterStartIsoDate();
    } else {
      const presetDays = preset === '30d' ? 30 : 60;
      nextStartDate = shiftIsoDate(presetDays - 1);
    }
    
    setComparisonTouched(false);
    updateCurrentRange(nextStartDate, nextEndDate, true);
  };

  const openSyncModal = (target: SyncTarget) => {
    setPendingSyncTarget(target);
    const nextEndDate = shiftIsoDate(0);
    const nextStartDate = shiftIsoDate(29);
    setSyncPreset('30d');
    setComparisonTouched(false);
    updateCurrentRange(nextStartDate, nextEndDate, true);
  };

  const closeSyncModal = () => {
    if (isSyncing) {
      return;
    }
    setPendingSyncTarget(null);
  };

  const buildSyncPayload = (comparisonRange?: { comparisonStartDate: string; comparisonEndDate: string }) => ({
    start_date: syncStartDate,
    end_date: syncEndDate,
    comparison_start_date: comparisonRange?.comparisonStartDate ?? comparisonStartDate,
    comparison_end_date: comparisonRange?.comparisonEndDate ?? comparisonEndDate,
  });

  const formatAccountIdForDisplay = (platform: string, accountId: string) => {
    const raw = (accountId || '').trim();
    if (platform !== 'google') return raw;
    const digits = raw.replace(/\D/g, '');
    if (digits.length !== 10) return raw;
    return `${digits.slice(0, 3)}-${digits.slice(3, 6)}-${digits.slice(6)}`;
  };

  const formatAccountNameForDisplay = (platform: string, accountName: string, accountId: string) => {
    const name = (accountName || '').trim();
    if (platform !== 'google') return name;
    const rawId = (accountId || '').replace(/\D/g, '');
    if (!rawId || rawId.length !== 10) return name;

    const basePattern = new RegExp(`^Google Ads\\s*${rawId}(\\s*\\(Manager\\))?$`, 'i');
    if (basePattern.test(name)) {
      const managerSuffix = /\(Manager\)$/i.test(name) ? ' (Manager)' : '';
      return `Google Ads ${formatAccountIdForDisplay(platform, rawId)}${managerSuffix}`;
    }
    return name;
  };

  useEffect(() => {
    fetchConnections();
  }, []);

  const fetchConnections = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/connections`);
      const data = await parseApiResponse(response);
      setConnections(Array.isArray(data) ? (data as Connection[]) : []);
    } catch (error) {
      console.error('Error fetching connections:', error);
    }
  };

  const startOAuthConnection = (platform: string, connectionId?: number) => {
    const reconnectParam = typeof connectionId === 'number' ? `?connection_id=${connectionId}` : '';
    const target = `${API_BASE}/api/auth/${platform}/login${reconnectParam}`;
    window.location.href = target;
  };

  const removeConnection = async (connection: Connection) => {
    const confirmed = window.confirm(`Remove connection \"${connection.account_name}\" (${connection.platform})?`);
    if (!confirmed) return;

    try {
      const response = await fetch(`${API_BASE}/api/connections/${connection.id}`, { method: 'DELETE' });
      const result = await parseApiResponse(response) as { status?: string; message?: string };
      if (result.status === 'success') {
        fetchConnections();
      } else {
        alert(result.message || 'Failed to remove connection.');
      }
    } catch (error) {
      console.error('Remove connection error:', error);
      alert('Failed to remove connection. Is the backend running?');
    }
  };

  const syncNow = async (id: number, payload: { start_date: string; end_date: string; comparison_start_date: string; comparison_end_date: string }) => {
    setIsSyncing(true);
    setSyncingConnectionId(id);
    setUploadStep('Connecting to API...');
    try {
      const response = await fetch(`${API_BASE}/api/sync/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response) as { status?: string; message?: string } & Record<string, unknown>;
      if (result.status === 'success') {
        onSyncComplete(result);
      } else {
        alert(result.message || 'Sync failed');
      }
    } catch (error) {
      console.error('Sync error:', error);
      alert('Sync failed. Is the backend running?');
    } finally {
      setIsSyncing(false);
      setUploadStep('');
      setTimeout(() => setSyncingConnectionId(null), 2000);
    }
  };

  const syncAll = async (payload: { start_date: string; end_date: string; comparison_start_date: string; comparison_end_date: string }) => {
    if (connections.length === 0) {
      alert('No connected accounts to sync.');
      return;
    }

    setIsSyncing(true);
    setSyncingConnectionId(-1); // Special ID for "sync all"
    setUploadStep('Syncing all connected accounts...');
    try {
      const response = await fetch(`${API_BASE}/api/sync-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const result = await parseApiResponse(response) as {
        status?: string;
        message?: string;
        syncedConnections?: number;
        totalActiveConnections?: number;
      } & Record<string, unknown>;
      if (result.status === 'success') {
        onSyncComplete(result);
        const synced = Number(result.syncedConnections ?? 0);
        const total = Number(result.totalActiveConnections ?? connections.filter((c) => c.is_active === 1).length);
        setSyncAllToast(`Synced ${synced} of ${total} active account${total === 1 ? '' : 's'}.`);
        window.setTimeout(() => setSyncAllToast(null), 3000);
      } else {
        alert(result.message || 'Sync all failed');
      }
    } catch (error) {
      console.error('Sync all error:', error);
      alert('Sync all failed. Is the backend running?');
    } finally {
      setIsSyncing(false);
      setUploadStep('');
      setTimeout(() => setSyncingConnectionId(null), 2000);
    }
  };

  const confirmSync = async () => {
    if (!pendingSyncTarget) {
      return;
    }

    const dayCount = diffDaysInclusive(syncStartDate, syncEndDate);
    const effectiveComparisonRange = comparisonTouched
      ? { comparisonStartDate, comparisonEndDate }
      : (comparisonMatchMode === 'previous_year'
          ? deriveComparisonRangeYearly(syncStartDate, syncEndDate)
          : deriveComparisonRange(syncStartDate, syncEndDate));
    const comparisonDayCount = diffDaysInclusive(
      effectiveComparisonRange.comparisonStartDate,
      effectiveComparisonRange.comparisonEndDate,
    );
    if (!syncStartDate || !syncEndDate || Number.isNaN(dayCount)) {
      alert('Enter a valid sync date range.');
      return;
    }
    if (dayCount <= 0) {
      alert('Start date must be on or before end date.');
      return;
    }
    if (dayCount > MAX_SYNC_WINDOW_DAYS) {
      alert(`Sync range cannot exceed ${MAX_SYNC_WINDOW_DAYS} days.`);
      return;
    }
    if (!effectiveComparisonRange.comparisonStartDate || !effectiveComparisonRange.comparisonEndDate || Number.isNaN(comparisonDayCount)) {
      alert('Enter a valid comparison date range.');
      return;
    }
    if (comparisonDayCount <= 0) {
      alert('Comparison start date must be on or before comparison end date.');
      return;
    }
    if (comparisonDayCount !== dayCount) {
      alert('Comparison range must match the selected sync range length for an accurate period-over-period comparison.');
      return;
    }
    if (new Date(`${effectiveComparisonRange.comparisonEndDate}T12:00:00`).getTime() >= new Date(`${syncStartDate}T12:00:00`).getTime()) {
      alert('Comparison period must end before the current sync period starts.');
      return;
    }

    if (!comparisonTouched) {
      setComparisonStartDate(effectiveComparisonRange.comparisonStartDate);
      setComparisonEndDate(effectiveComparisonRange.comparisonEndDate);
    }

    const payload = buildSyncPayload(effectiveComparisonRange);
    const target = pendingSyncTarget;
    setPendingSyncTarget(null);

    if (target.type === 'single') {
      await syncNow(target.connectionId, payload);
      return;
    }

    await syncAll(payload);
  };

  const runDiagnostics = async () => {
    setIsRunningDiagnostics(true);
    try {
      const response = await fetch(`${API_BASE}/api/connections/diagnostics`);
      const result = await parseApiResponse(response) as {
        status?: string;
        message?: string;
        overall?: 'ok' | 'warning' | 'error';
        results?: ConnectionDiagnostic[];
      };
      if (result.status === 'success') {
        setDiagnosticsOverall((result.overall ?? 'ok') as 'ok' | 'warning' | 'error');
        setDiagnosticsResults((result.results ?? []) as ConnectionDiagnostic[]);
        setShowDiagnosticsModal(true);
      } else {
        alert(result.message || 'Diagnostics failed');
      }
    } catch (error) {
      console.error('Diagnostics error:', error);
      alert('Diagnostics failed. Is the backend running?');
    } finally {
      setIsRunningDiagnostics(false);
    }
  };

  const openAccountSelector = async (connection: Connection) => {
    setShowAccountModalFor(connection.id);
    setAccountQuery('');
    setIsLoadingAccounts(true);
    try {
      const response = await fetch(`${API_BASE}/api/connections/${connection.id}/accounts`);
      const result = await parseApiResponse(response) as {
        status?: string;
        message?: string;
        accounts?: AdAccount[];
        selectedAccountIds?: string[];
      };
      if (result.status === 'success') {
        setModalAccounts(result.accounts ?? []);
        setModalSelectedIds(result.selectedAccountIds ?? []);
      } else {
        alert(result.message || 'Unable to load ad accounts.');
      }
    } catch (error) {
      console.error('Discover accounts error:', error);
      alert('Unable to load ad accounts. Is the backend running?');
    } finally {
      setIsLoadingAccounts(false);
    }
  };

  const searchAccounts = async () => {
    if (!showAccountModalFor) return;
    setIsLoadingAccounts(true);
    try {
      const response = await fetch(`${API_BASE}/api/connections/${showAccountModalFor}/accounts?query=${encodeURIComponent(accountQuery)}`);
      const result = await parseApiResponse(response) as {
        status?: string;
        message?: string;
        accounts?: AdAccount[];
        selectedAccountIds?: string[];
      };
      if (result.status === 'success') {
        setModalAccounts(result.accounts ?? []);
        setModalSelectedIds(result.selectedAccountIds ?? []);
      } else {
        alert(result.message || 'Account search failed.');
      }
    } catch (error) {
      console.error('Search accounts error:', error);
      alert('Unable to search ad accounts.');
    } finally {
      setIsLoadingAccounts(false);
    }
  };

  const toggleModalAccountSelection = (accountId: string) => {
    setModalSelectedIds((prev) => (
      prev.includes(accountId)
        ? prev.filter((id) => id !== accountId)
        : [...prev, accountId]
    ));
  };

  const saveAccountSelection = async () => {
    if (!showAccountModalFor) return;
    if (modalSelectedIds.length === 0) {
      alert('Select at least one ad account.');
      return;
    }

    setIsSavingAccounts(true);
    try {
      const response = await fetch(`${API_BASE}/api/connections/${showAccountModalFor}/accounts/select`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_account_ids: modalSelectedIds }),
      });
      const result = await parseApiResponse(response) as { status?: string; message?: string };
      if (result.status === 'success') {
        setShowAccountModalFor(null);
        fetchConnections();
      } else {
        alert(result.message || 'Could not save account selection.');
      }
    } catch (error) {
      console.error('Save account selection error:', error);
      alert('Could not save account selection.');
    } finally {
      setIsSavingAccounts(false);
    }
  };

  const getSelectedAccountsPreview = (connection: Connection) => {
    const selectedIds = connection.selected_account_ids ?? [];
    if (selectedIds.length === 0) {
      return {
        countLabel: 'Default account only',
        namesLabel: connection.account_name,
        namesTitle: connection.account_name,
      };
    }

    const availableMap = new Map((connection.available_accounts ?? []).map((a) => [a.id, a.name]));
    const selectedNames = selectedIds.map((id) => availableMap.get(id) ?? id);
    const preview = selectedNames.slice(0, 2).join(', ');
    const namesLabel = selectedNames.length > 2
      ? `${preview} +${selectedNames.length - 2} more`
      : preview;

    return {
      countLabel: `${selectedIds.length} selected`,
      namesLabel,
      namesTitle: selectedNames.join(', '),
    };
  };

  const modalConnection = showAccountModalFor
    ? connections.find((c) => c.id === showAccountModalFor)
    : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-xl font-bold text-slate-800">Connected Accounts</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={runDiagnostics}
            disabled={isRunningDiagnostics}
            className="flex items-center gap-2 px-4 py-2 bg-background border border-slate-200 text-foreground rounded-xl text-sm font-bold hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isRunningDiagnostics ? 'Running Checks...' : 'Run Diagnostics'}
          </button>
          <button
            onClick={() => openSyncModal({ type: 'all' })}
            disabled={isSyncing || connections.length === 0}
            className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-xl text-sm font-bold hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-lg"
          >
            <RefreshCcw size={16} className={isSyncing ? 'animate-spin' : ''} />
            {isSyncing ? 'Syncing All...' : 'Sync All'}
          </button>
          <button 
            onClick={() => setShowAddModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors shadow-lg shadow-blue-100"
          >
            <Plus size={16} />
            Connect New
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {connections.length === 0 ? (
          <div className="col-span-full py-12 text-center border-2 border-dashed border-slate-100 rounded-3xl">
            <p className="text-slate-400 font-medium">No accounts connected yet.</p>
            <p className="text-xs text-slate-400 mt-1">Connect a platform to enable one-click syncing.</p>
          </div>
        ) : (
          connections.map((conn) => {
            const selectedPreview = getSelectedAccountsPreview(conn);
            return (
            <div key={conn.id} className="p-6 bg-background border border-slate-100 rounded-[2rem] shadow-sm hover:border-blue-100 transition-all group">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center
                    ${conn.platform === 'google' ? 'bg-blue-50 text-blue-600' : 
                      conn.platform === 'meta' ? 'bg-indigo-50 text-indigo-600' :
                      conn.platform === 'linkedin' ? 'bg-sky-50 text-sky-600' :
                      conn.platform === 'microsoft' ? 'bg-emerald-50 text-emerald-600' :
                      'bg-pink-50 text-pink-600'}
                  `}>
                    <Zap size={20} className="fill-current" />
                  </div>
                  <div>
                    <p className="text-sm font-black text-slate-800 uppercase tracking-tight">{conn.platform}</p>
                    <p className="text-xs font-bold text-slate-400">{formatAccountIdForDisplay(conn.platform, conn.account_id)}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => removeConnection(conn)}
                    disabled={isSyncing}
                    className="px-2.5 py-1 rounded-lg bg-rose-50 text-rose-700 text-[10px] font-black uppercase tracking-wide hover:bg-rose-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Remove
                  </button>
                  <button
                    onClick={() => openAccountSelector(conn)}
                    disabled={isSyncing}
                    className="px-2.5 py-1 rounded-lg bg-blue-50 text-blue-700 text-[10px] font-black uppercase tracking-wide hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Edit Selection
                  </button>
                  <div className="flex items-center gap-2 text-emerald-500 bg-emerald-50 px-2 py-1 rounded-lg">
                    <CheckCircle2 size={12} />
                    <span className="text-[10px] font-black uppercase">Active</span>
                  </div>
                </div>
              </div>
              
              <h4 className="text-lg font-bold text-slate-700 mb-6">{formatAccountNameForDisplay(conn.platform, conn.account_name, conn.account_id)}</h4>

              <div className="mb-4 rounded-xl bg-slate-50 border border-slate-100 p-3">
                <p className="text-[11px] font-black uppercase tracking-wider text-slate-500 mb-2">Selected ad accounts</p>
                <p className="text-sm font-semibold text-slate-700">
                  {selectedPreview.countLabel}
                </p>
                <p className="mt-1 text-xs text-slate-500 truncate" title={selectedPreview.namesTitle}>
                  {selectedPreview.namesLabel}
                </p>
                <button
                  onClick={() => openAccountSelector(conn)}
                  disabled={isSyncing}
                  className="mt-2 text-xs font-bold text-blue-700 hover:text-blue-800"
                >
                  Find and select ad accounts
                </button>
              </div>
              
              <button 
                onClick={() => openSyncModal({ type: 'single', connectionId: conn.id })}
                disabled={isSyncing}
                className="w-full h-12 border-2 border-slate-50 hover:border-blue-600 hover:text-blue-600 rounded-2xl flex items-center justify-center gap-2 text-sm font-bold text-slate-400 transition-all group-hover:border-blue-100"
              >
                <RefreshCcw size={16} className={isSyncing ? 'animate-spin text-blue-600' : ''} />
                {isSyncing ? 'Syncing...' : 'Sync Now'}
              </button>
            </div>
          );
          })
        )}
      </div>

      {syncAllToast && (
        <div className="fixed bottom-6 right-6 z-[110] px-4 py-3 rounded-2xl bg-emerald-600 text-white text-sm font-bold shadow-2xl shadow-emerald-200">
          {syncAllToast}
        </div>
      )}

      {showAccountModalFor && (
        <div className="fixed inset-0 z-[120] bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-background w-full max-w-2xl rounded-[2rem] shadow-2xl p-6">
            <h3 className="text-2xl font-black text-slate-900 mb-2">Select Ad Accounts</h3>
            <p className="text-slate-500 font-medium mb-4">Find the ad accounts to include in analysis for this connected platform.</p>

            <div className="flex items-center gap-2 mb-4">
              <input
                value={accountQuery}
                onChange={(e) => setAccountQuery(e.target.value)}
                placeholder="Search by account name or ID"
                className="flex-1 h-11 px-3 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
              <button
                onClick={searchAccounts}
                disabled={isLoadingAccounts}
                className="h-11 px-4 rounded-xl bg-slate-900 text-white text-sm font-bold disabled:opacity-50"
              >
                {isLoadingAccounts ? 'Searching...' : 'Search'}
              </button>
            </div>

            <div className="border border-slate-100 rounded-2xl max-h-[320px] overflow-auto">
              {isLoadingAccounts ? (
                <p className="p-4 text-sm text-slate-500">Loading ad accounts...</p>
              ) : modalAccounts.length === 0 ? (
                <p className="p-4 text-sm text-slate-500">No ad accounts found.</p>
              ) : (
                <div className="divide-y divide-slate-100">
                  {modalAccounts.map((acc) => {
                    const isChecked = modalSelectedIds.includes(acc.id);
                    return (
                      <label key={acc.id} className="flex items-center justify-between p-4 cursor-pointer hover:bg-slate-50">
                        <div>
                          <p className="text-sm font-bold text-slate-800">{formatAccountNameForDisplay(modalConnection?.platform ?? '', acc.name, acc.id)}</p>
                          <p className="text-xs text-slate-500">{formatAccountIdForDisplay(modalConnection?.platform ?? '', acc.id)}</p>
                        </div>
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleModalAccountSelection(acc.id)}
                          className="w-4 h-4"
                        />
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                onClick={() => setShowAccountModalFor(null)}
                className="px-4 py-2 rounded-xl text-slate-500 font-bold hover:text-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={saveAccountSelection}
                disabled={isSavingAccounts || modalSelectedIds.length === 0}
                className="px-4 py-2 rounded-xl bg-blue-600 text-white font-bold hover:bg-blue-700 disabled:opacity-50"
              >
                {isSavingAccounts ? 'Saving...' : `Save (${modalSelectedIds.length})`}
              </button>
            </div>
          </div>
        </div>
      )}

      {showDiagnosticsModal && (
        <div className="fixed inset-0 z-[115] bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-background w-full max-w-3xl rounded-[2rem] shadow-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-2xl font-black text-slate-900">Connection Diagnostics</h3>
                <p className="text-sm text-slate-500 mt-1">Credential and account-discovery checks across all connected platforms.</p>
              </div>
              <span className={`px-3 py-1 rounded-lg text-xs font-black uppercase tracking-wide ${
                diagnosticsOverall === 'ok'
                  ? 'bg-emerald-50 text-emerald-700'
                  : diagnosticsOverall === 'warning'
                    ? 'bg-amber-50 text-amber-700'
                    : 'bg-rose-50 text-rose-700'
              }`}>
                {diagnosticsOverall}
              </span>
            </div>

            <div className="max-h-[420px] overflow-auto border border-slate-100 rounded-2xl">
              {diagnosticsResults.length === 0 ? (
                <p className="p-4 text-sm text-slate-500">No connected accounts found.</p>
              ) : (
                <div className="divide-y divide-slate-100">
                  {diagnosticsResults.map((row) => (
                    <div key={row.connectionId} className="p-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-black text-slate-800 uppercase tracking-wide">{row.platform}</p>
                          <p className="text-sm font-semibold text-slate-700">{row.accountName}</p>
                          {!!row.accountId && (
                            <p className="text-xs text-slate-500 mt-0.5">{formatAccountIdForDisplay(row.platform, row.accountId)}</p>
                          )}
                        </div>
                        <div className="text-right flex items-center gap-3">
                          {row.status !== 'ok' && (
                            <button
                              onClick={() => startOAuthConnection(row.platform, row.connectionId)}
                              className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-black uppercase tracking-wide hover:bg-blue-700"
                            >
                              Reconnect
                            </button>
                          )}
                          <div>
                          <p className="text-xs text-slate-500">Selected accounts: {row.selectedAdAccounts}</p>
                          <p className={`text-xs font-bold uppercase ${
                            row.status === 'ok'
                              ? 'text-emerald-600'
                              : row.status === 'warning'
                                ? 'text-amber-600'
                                : 'text-rose-600'
                          }`}>
                            {row.status}
                          </p>
                          </div>
                        </div>
                      </div>
                      {row.issues.length > 0 && (
                        <div className="mt-2 rounded-xl bg-slate-50 p-3 space-y-1">
                          {row.issues.map((issue, idx) => (
                            <p key={idx} className="text-xs text-slate-600">- {issue}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                onClick={() => setShowDiagnosticsModal(false)}
                className="px-4 py-2 rounded-xl text-slate-500 font-bold hover:text-slate-700"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {showAddModal && (
        <div className="fixed inset-0 z-[100] bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-background w-full max-w-md rounded-[2.5rem] shadow-2xl p-8 animate-in fade-in zoom-in duration-300">
            <h3 className="text-2xl font-black text-slate-900 mb-2">Connect Platform</h3>
            <p className="text-slate-500 font-medium mb-8">Select a platform to connect via OAuth.</p>
            
            <div className="grid grid-cols-2 gap-4 mb-8">
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => startOAuthConnection(p.id)}
                  className="p-6 border-2 border-slate-50 hover:border-blue-600 hover:bg-blue-50/30 rounded-3xl flex flex-col items-center gap-3 transition-all text-center group"
                >
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center bg-${p.color}-50 text-${p.color}-600 group-hover:scale-110 transition-transform`}>
                    <Zap size={24} className="fill-current" />
                  </div>
                  <span className="text-sm font-black text-slate-700">{p.name}</span>
                </button>
              ))}
            </div>
            
            <button 
              onClick={() => setShowAddModal(false)}
              className="w-full py-4 text-slate-400 font-bold hover:text-slate-600 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {pendingSyncTarget && (
        <div className="fixed inset-0 z-[120] bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="w-full max-w-md rounded-[2rem] border border-slate-200 bg-white shadow-2xl p-6">
            <div className="flex items-start gap-3 mb-5">
              <div className="w-10 h-10 rounded-xl bg-violet-50 text-violet-600 flex items-center justify-center">
                <CalendarRange size={20} />
              </div>
              <div>
                <h3 className="text-2xl font-black text-slate-900">Sync Period Comparison</h3>
                <p className="text-sm text-slate-500 mt-1">
                  Choose the current reporting window and the prior comparison window. Both ranges must be the same length, with a {MAX_SYNC_WINDOW_DAYS}-day maximum.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 mb-4">
              {[
                { id: '30d', label: 'Last 30 days' },
                { id: '60d', label: 'Last 60 days' },
                { id: 'current_quarter', label: 'Current Quarter' },
                { id: 'custom', label: 'Custom range' },
              ].map((option) => {
                const isActive = syncPreset === option.id;
                return (
                  <button
                    key={option.id}
                    onClick={() => applySyncPreset(option.id as SyncPreset)}
                    className={`rounded-xl border px-3 py-2 text-sm font-bold transition-colors ${
                      isActive
                        ? 'border-violet-500 bg-violet-50 text-violet-600'
                        : 'border-slate-200 text-slate-600 hover:border-violet-400 hover:bg-violet-50/20'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-black uppercase tracking-wide text-slate-500">Current start</span>
                <input
                  type="date"
                  value={syncStartDate}
                  max={syncEndDate}
                  onChange={(event) => {
                    setSyncPreset('custom');
                    updateCurrentRange(event.target.value, syncEndDate);
                  }}
                  className="h-11 w-full rounded-xl border border-slate-200 px-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-black uppercase tracking-wide text-slate-500">Current end</span>
                <input
                  type="date"
                  value={syncEndDate}
                  min={syncStartDate}
                  max={shiftIsoDate(0)}
                  onChange={(event) => {
                    setSyncPreset('custom');
                    updateCurrentRange(syncStartDate, event.target.value);
                  }}
                  className="h-11 w-full rounded-xl border border-slate-200 px-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>
            </div>

            <div className="mt-4 flex flex-col gap-2 rounded-xl border border-slate-100 bg-slate-50 px-3 py-3">
              <div>
                <p className="text-xs font-black uppercase tracking-wide text-slate-500">Comparison period</p>
                <p className="text-xs text-slate-500">Auto-filled based on the current window. You can adjust it.</p>
              </div>
              <div className="flex bg-white rounded-lg border border-slate-200 overflow-hidden shadow-sm self-start mt-1">
                <button
                  type="button"
                  onClick={() => {
                    setComparisonMatchMode('previous_period');
                    setComparisonTouched(false);
                    syncComparisonToCurrent(syncStartDate, syncEndDate, 'previous_period');
                  }}
                  className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-wider transition-colors ${comparisonMatchMode === 'previous_period' ? 'bg-violet-600 text-white' : 'text-slate-500 hover:bg-slate-50'}`}
                >
                  Previous Period
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setComparisonMatchMode('previous_year');
                    setComparisonTouched(false);
                    syncComparisonToCurrent(syncStartDate, syncEndDate, 'previous_year');
                  }}
                  className={`px-3 py-1.5 text-[10px] font-black uppercase tracking-wider transition-colors ${comparisonMatchMode === 'previous_year' ? 'bg-violet-600 text-white' : 'text-slate-500 hover:bg-slate-50'}`}
                >
                  Previous Year
                </button>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-black uppercase tracking-wide text-slate-500">Comparison start</span>
                <input
                  type="date"
                  value={comparisonStartDate}
                  max={comparisonEndDate}
                  onChange={(event) => {
                    setComparisonTouched(true);
                    setComparisonStartDate(event.target.value);
                  }}
                  className="h-11 w-full rounded-xl border border-slate-200 px-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-black uppercase tracking-wide text-slate-500">Comparison end</span>
                <input
                  type="date"
                  value={comparisonEndDate}
                  min={comparisonStartDate}
                  max={shiftIsoDate(0)}
                  onChange={(event) => {
                    setComparisonTouched(true);
                    setComparisonEndDate(event.target.value);
                  }}
                  className="h-11 w-full rounded-xl border border-slate-200 px-3 text-sm font-medium text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>
            </div>

            <div className="mt-4 rounded-xl bg-slate-50 border border-slate-100 p-3 text-xs text-slate-600">
              Sync target: {pendingSyncTarget.type === 'all' ? 'all active connections' : 'this connection'}
              <br />
              Current window: {diffDaysInclusive(syncStartDate, syncEndDate)} day{diffDaysInclusive(syncStartDate, syncEndDate) === 1 ? '' : 's'}
              <br />
              Comparison window: {diffDaysInclusive(comparisonStartDate, comparisonEndDate)} day{diffDaysInclusive(comparisonStartDate, comparisonEndDate) === 1 ? '' : 's'}
            </div>

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                onClick={closeSyncModal}
                disabled={isSyncing}
                className="px-4 py-2 rounded-xl text-slate-500 font-bold hover:text-slate-700 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={confirmSync}
                disabled={isSyncing}
                className="px-4 py-2 rounded-xl bg-blue-600 text-white font-bold hover:bg-blue-700 disabled:opacity-50"
              >
                {pendingSyncTarget.type === 'all' ? 'Start Sync All' : 'Start Sync'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sync Monitor */}
      <SyncMonitor 
        connectionId={syncingConnectionId ?? -1} 
        isVisible={syncingConnectionId !== null && syncingConnectionId !== -1}
      />
    </div>
  );
};
