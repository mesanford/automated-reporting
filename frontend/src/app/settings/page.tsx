"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Briefcase, ArrowLeft, UserPlus, Trash2, Shield, Copy, Check, Zap,
  ScrollText, Users as UsersIcon, Clock, Plus, Pause, Play, Bell, AlertTriangle,
  DollarSign, Calculator, Mail,
} from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { useWorkspace } from '@/lib/workspace';
import { apiFetch, apiJson } from '@/lib/api';

interface Member {
  user_subject: string;
  role: string;
  email: string | null;
  name: string | null;
  joined_at: string | null;
}

interface InviteResult {
  invite_id: number;
  email: string;
  role: string;
  expires_at: string;
  token: string;
  accept_url: string;
  email_delivery: {
    delivered: boolean;
    provider: string;
    error: string | null;
  };
}

interface Schedule {
  id: number;
  workspace_id: number;
  name: string;
  frequency: 'daily' | 'weekly';
  hour_utc: number;
  day_of_week: number | null;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
}

interface Pacing {
  budget_id: number;
  period_label: string;
  amount: number;
  spent: number;
  target_at_today: number;
  pace_pct: number | null;
  pct_used: number;
  status: 'on_pace' | 'under_pace' | 'over_pace' | 'exhausted';
  days_elapsed: number;
  days_total: number;
}

interface Budget {
  id: number;
  workspace_id: number;
  name: string;
  scope_type: 'workspace' | 'platform' | 'connection';
  scope_key: string | null;
  period_type: 'monthly' | 'quarterly';
  amount: string;
  start_date: string | null;
  is_active: boolean;
  alert_at_pct: number | null;
  pacing: Pacing | null;
}

interface AlertRule {
  id: number;
  workspace_id: number;
  name: string;
  metric: string;
  comparison: 'gt' | 'lt' | 'pct_change_gt';
  threshold: string;
  channels: Array<{ type: string; url?: string; to?: string }>;
  is_active: boolean;
  last_triggered_at: string | null;
  last_value: string | null;
}

interface DigestStatus {
  subscribed: boolean;
  cadence?: 'daily' | 'weekly';
  email?: string;
  next_send_at?: string | null;
  last_sent_at?: string | null;
  last_send_error?: string | null;
}

interface CustomKpi {
  id: number;
  name: string;
  formula: string;
  format: 'currency' | 'percent' | 'ratio' | 'number' | 'integer';
  description: string | null;
  is_active: boolean;
  sort_order: number;
}

interface AuditEntry {
  id: number;
  actor_subject: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown> | null;
  created_at: string | null;
}

const ROLES = ['admin', 'member', 'viewer'] as const;
const PRIVILEGED_ROLES = new Set(['owner', 'admin']);

export default function SettingsPage() {
  const { active } = useWorkspace();
  const [members, setMembers] = useState<Member[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [kpis, setKpis] = useState<CustomKpi[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canManage = active ? PRIVILEGED_ROLES.has(active.role ?? '') : false;
  const isOwner = active?.role === 'owner';

  async function loadAll() {
    if (!active) return;
    setError(null);
    setLoading(true);
    try {
      const m = await apiJson<Member[]>(`/api/workspaces/${active.id}/members`);
      setMembers(m);
      const s = await apiJson<Schedule[]>(`/api/workspaces/${active.id}/schedules`);
      setSchedules(s);
      const a = await apiJson<AlertRule[]>(`/api/workspaces/${active.id}/alerts`);
      setRules(a);
      const bgs = await apiJson<Budget[]>(`/api/workspaces/${active.id}/budgets`);
      setBudgets(bgs);
      const ks = await apiJson<CustomKpi[]>(`/api/workspaces/${active.id}/kpis`);
      setKpis(ks);
      if (canManage) {
        const a = await apiJson<AuditEntry[]>(
          `/api/workspaces/${active.id}/audit-log?limit=100`,
        );
        setAudit(a);
      } else {
        setAudit([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id]);

  if (!active) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-sm text-slate-500">No active workspace.</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <nav className="border-b border-slate-100 bg-background/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200"
            >
              <Zap className="text-white w-6 h-6 fill-white" />
            </Link>
            <h1 className="text-2xl font-black tracking-tight text-slate-900 flex items-center gap-2">
              <Briefcase className="text-blue-600" size={22} />
              Workspace settings
            </h1>
          </div>
          <Link
            href="/"
            className="flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-blue-600"
          >
            <ArrowLeft size={16} />
            Back to dashboard
          </Link>
        </div>
      </nav>

      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-10 space-y-10">
        {/* Workspace summary */}
        <WorkspaceHeaderSection active={active} canManage={canManage} onChanged={loadAll} />

        {error && (
          <div className="border border-red-200 bg-red-50 text-red-700 rounded-xl p-4 text-sm">
            {error}
          </div>
        )}

        <MembersSection
          members={members}
          loading={loading}
          isOwner={isOwner}
          activeUserSubject={null}
          onChanged={loadAll}
          workspaceId={active.id}
        />

        {canManage && (
          <InviteSection
            workspaceId={active.id}
            onInvited={loadAll}
          />
        )}

        <SchedulesSection
          schedules={schedules}
          loading={loading}
          canManage={canManage}
          workspaceId={active.id}
          onChanged={loadAll}
        />

        <AlertsSection
          rules={rules}
          loading={loading}
          canManage={canManage}
          workspaceId={active.id}
          onChanged={loadAll}
        />

        <BudgetsSection
          budgets={budgets}
          loading={loading}
          canManage={canManage}
          workspaceId={active.id}
          onChanged={loadAll}
        />

        <KpisSection
          kpis={kpis}
          loading={loading}
          canManage={canManage}
          workspaceId={active.id}
          onChanged={loadAll}
        />

        <DigestSection workspaceId={active.id} />

        {canManage ? (
          <AuditSection entries={audit} loading={loading} />
        ) : (
          <p className="text-xs text-slate-400 italic">
            Audit log is available to owners and admins.
          </p>
        )}
      </main>
    </div>
  );
}

const COMMON_CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'INR', 'BRL', 'MXN'];

function WorkspaceHeaderSection({
  active,
  canManage,
  onChanged,
}: {
  active: { id: number; name: string; slug: string; role: string | null; base_currency: string };
  canManage: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function setCurrency(next: string) {
    if (next === active.base_currency) return;
    setBusy(true);
    try {
      await apiFetch(`/api/workspaces/${active.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ base_currency: next }),
      });
      // Reload so the switcher + dashboard see the new currency.
      window.location.reload();
    } finally {
      setBusy(false);
      void onChanged();
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wider text-slate-400 font-bold">
            Workspace
          </div>
          <div className="text-2xl font-black text-slate-900 mt-1 truncate">{active.name}</div>
          <div className="text-sm text-slate-500 mt-1">slug: {active.slug}</div>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {canManage ? (
            <label className="flex items-center gap-2 text-sm">
              <span className="text-slate-500 text-xs">Currency</span>
              <select
                value={active.base_currency}
                onChange={(e) => void setCurrency(e.target.value)}
                disabled={busy}
                className="text-sm font-bold rounded-lg border border-slate-200 px-2 py-1.5"
              >
                {COMMON_CURRENCIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
          ) : (
            <span className="text-xs text-slate-400">{active.base_currency}</span>
          )}
          <RoleBadge role={active.role ?? 'member'} />
        </div>
      </div>
    </section>
  );
}

function RoleBadge({ role }: { role: string }) {
  const tone =
    role === 'owner'
      ? 'bg-indigo-100 text-indigo-700'
      : role === 'admin'
        ? 'bg-blue-100 text-blue-700'
        : role === 'viewer'
          ? 'bg-slate-100 text-slate-600'
          : 'bg-emerald-100 text-emerald-700';
  return (
    <span className={`text-[11px] font-black uppercase tracking-wider px-3 py-1.5 rounded-full ${tone}`}>
      {role}
    </span>
  );
}

function MembersSection({
  members,
  loading,
  isOwner,
  onChanged,
  workspaceId,
}: {
  members: Member[];
  loading: boolean;
  isOwner: boolean;
  activeUserSubject: string | null;
  onChanged: () => void;
  workspaceId: number;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  async function changeRole(userSubject: string, role: string) {
    setBusy(userSubject);
    try {
      const res = await apiFetch(
        `/api/workspaces/${workspaceId}/members/${encodeURIComponent(userSubject)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ role }),
        },
      );
      if (!res.ok) throw new Error(`Failed to change role (${res.status})`);
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove(userSubject: string) {
    if (!confirm(`Remove ${userSubject} from this workspace?`)) return;
    setBusy(userSubject);
    try {
      const res = await apiFetch(
        `/api/workspaces/${workspaceId}/members/${encodeURIComponent(userSubject)}`,
        { method: 'DELETE' },
      );
      if (!res.ok) throw new Error(`Failed to remove (${res.status})`);
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <UsersIcon size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Members</h2>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && members.length === 0 && (
        <p className="text-sm text-slate-400">No members yet.</p>
      )}

      <ul className="divide-y divide-slate-50">
        {members.map((m) => (
          <li key={m.user_subject} className="py-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="font-semibold text-sm text-slate-800 truncate">
                {m.email || m.name || m.user_subject}
              </div>
              <div className="text-xs text-slate-400 truncate">
                {m.user_subject}
                {m.joined_at && ` · joined ${new Date(m.joined_at).toLocaleDateString()}`}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {isOwner && m.role !== 'owner' ? (
                <select
                  value={m.role}
                  disabled={busy === m.user_subject}
                  onChange={(e) => void changeRole(m.user_subject, e.target.value)}
                  className="text-xs font-bold rounded-lg border border-slate-200 px-2 py-1.5"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
              ) : (
                <RoleBadge role={m.role} />
              )}
              {isOwner && m.role !== 'owner' && (
                <button
                  onClick={() => void remove(m.user_subject)}
                  disabled={busy === m.user_subject}
                  className="text-slate-400 hover:text-red-500 disabled:opacity-50"
                  aria-label="Remove member"
                >
                  <Trash2 size={16} />
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function InviteSection({
  workspaceId,
  onInvited,
}: {
  workspaceId: number;
  onInvited: () => void;
}) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<string>('member');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InviteResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setResult(null);
    if (!email.trim()) return;
    setBusy(true);
    try {
      const r = await apiJson<InviteResult>(
        `/api/workspaces/${workspaceId}/invites`,
        {
          method: 'POST',
          body: JSON.stringify({ email: email.trim(), role }),
        },
      );
      setResult(r);
      setEmail('');
      await onInvited();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to invite');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <UserPlus size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Invite a teammate</h2>
      </div>

      <div className="flex flex-col sm:flex-row gap-2">
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          type="email"
          placeholder="teammate@company.com"
          disabled={busy}
          className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm focus:outline-none focus:border-blue-400"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          disabled={busy}
          className="text-sm font-semibold rounded-lg border border-slate-200 px-3 py-2"
        >
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button
          onClick={() => void submit()}
          disabled={busy || !email.trim()}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300 hover:bg-blue-700"
        >
          {busy ? 'Sending…' : 'Send invite'}
        </button>
      </div>

      {error && (
        <p className="text-xs text-red-600 mt-3">{error}</p>
      )}

      {result && <InviteLinkPanel result={result} />}
    </section>
  );
}

function InviteLinkPanel({ result }: { result: InviteResult }) {
  const [copied, setCopied] = useState<'url' | 'token' | null>(null);

  // Prefer the URL the backend computed (uses FRONTEND_URL); fall back to
  // the current origin if the response omitted it.
  const acceptUrl =
    result.accept_url ||
    (typeof window !== 'undefined'
      ? `${window.location.origin}/invites/accept?token=${encodeURIComponent(result.token)}`
      : `/invites/accept?token=${encodeURIComponent(result.token)}`);

  async function copy(value: string, which: 'url' | 'token') {
    await navigator.clipboard.writeText(value);
    setCopied(which);
    setTimeout(() => setCopied(null), 1500);
  }

  const delivered = result.email_delivery?.delivered ?? false;
  const provider = result.email_delivery?.provider ?? 'noop';
  const deliveryError = result.email_delivery?.error;

  // Tone: green when delivered, amber when fallback (no provider / error).
  const tone = delivered
    ? { border: 'border-emerald-200', bg: 'bg-emerald-50', heading: 'text-emerald-900', body: 'text-emerald-800', accent: 'border-emerald-200 bg-emerald-600 text-emerald-700' }
    : { border: 'border-amber-200', bg: 'bg-amber-50', heading: 'text-amber-900', body: 'text-amber-800', accent: 'border-amber-200 bg-amber-600 text-amber-700' };

  return (
    <div className={`mt-4 border ${tone.border} ${tone.bg} rounded-xl p-4 text-sm`}>
      {delivered ? (
        <>
          <p className={`font-semibold ${tone.heading} mb-1`}>
            Invite emailed to {result.email}.
          </p>
          <p className={`text-xs ${tone.body} mb-3`}>
            Sent via {provider}. Link expires {new Date(result.expires_at).toLocaleString()}.
            The accept link is below as a backup if {result.email.split('@')[0]} doesn&apos;t see the email.
          </p>
        </>
      ) : (
        <>
          <p className={`font-semibold ${tone.heading} mb-1`}>
            Invite created — send this link to {result.email}.
          </p>
          <p className={`text-xs ${tone.body} mb-3`}>
            {deliveryError
              ? `Email delivery failed (${deliveryError}). Share the link manually.`
              : 'Email delivery isn\'t configured yet — share the link manually.'}{' '}
            It expires {new Date(result.expires_at).toLocaleString()}.
          </p>
        </>
      )}

      <div className="space-y-2">
        <div>
          <div className={`text-[10px] uppercase tracking-wider font-bold ${tone.body} mb-1`}>
            Accept link
          </div>
          <div className="flex items-center gap-2">
            <code className={`flex-1 text-xs bg-white border ${tone.border} rounded px-2 py-1.5 truncate font-mono`}>
              {acceptUrl}
            </code>
            <button
              onClick={() => void copy(acceptUrl, 'url')}
              className={`px-2 py-1.5 rounded text-white text-xs font-bold flex items-center gap-1 ${delivered ? 'bg-emerald-600' : 'bg-amber-600'}`}
            >
              {copied === 'url' ? <Check size={12} /> : <Copy size={12} />}
              {copied === 'url' ? 'Copied' : 'Copy link'}
            </button>
          </div>
        </div>

        <details className={`text-xs ${tone.body}`}>
          <summary className="cursor-pointer">Raw token</summary>
          <div className="flex items-center gap-2 mt-2">
            <code className={`flex-1 bg-white border ${tone.border} rounded px-2 py-1.5 truncate font-mono`}>
              {result.token}
            </code>
            <button
              onClick={() => void copy(result.token, 'token')}
              className={`px-2 py-1.5 rounded text-white text-xs font-bold flex items-center gap-1 ${delivered ? 'bg-emerald-600' : 'bg-amber-600'}`}
            >
              {copied === 'token' ? <Check size={12} /> : <Copy size={12} />}
              {copied === 'token' ? 'Copied' : 'Copy'}
            </button>
          </div>
        </details>
      </div>
    </div>
  );
}


const DOW_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function SchedulesSection({
  schedules,
  loading,
  canManage,
  workspaceId,
  onChanged,
}: {
  schedules: Schedule[];
  loading: boolean;
  canManage: boolean;
  workspaceId: number;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | 'new' | null>(null);
  const [newName, setNewName] = useState('');
  const [newFreq, setNewFreq] = useState<'daily' | 'weekly'>('daily');
  const [newHour, setNewHour] = useState(13);
  const [newDow, setNewDow] = useState(0);

  async function create() {
    if (!newName.trim() || !canManage) return;
    setBusy('new');
    try {
      const body: Record<string, unknown> = {
        name: newName.trim(),
        frequency: newFreq,
        hour_utc: newHour,
      };
      if (newFreq === 'weekly') body.day_of_week = newDow;
      await apiFetch(`/api/workspaces/${workspaceId}/schedules`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setNewName('');
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function toggle(s: Schedule) {
    setBusy(s.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/schedules/${s.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !s.is_active }),
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove(s: Schedule) {
    if (!confirm(`Delete schedule "${s.name}"?`)) return;
    setBusy(s.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/schedules/${s.id}`, {
        method: 'DELETE',
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <Clock size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Scheduled syncs</h2>
        <span className="text-xs text-slate-400 ml-2">all times UTC</span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && schedules.length === 0 && (
        <p className="text-sm text-slate-400">No schedules yet.</p>
      )}

      <ul className="divide-y divide-slate-50">
        {schedules.map((s) => (
          <li key={s.id} className="py-3 flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="font-semibold text-sm text-slate-800 truncate">
                {s.name}
              </div>
              <div className="text-xs text-slate-500 truncate">
                {s.frequency === 'daily'
                  ? `Daily at ${String(s.hour_utc).padStart(2, '0')}:00`
                  : `Weekly on ${DOW_LABELS[s.day_of_week ?? 0]} at ${String(s.hour_utc).padStart(2, '0')}:00`}
                {s.next_run_at && ` · next ${new Date(s.next_run_at).toLocaleString()}`}
                {s.last_run_at && ` · last ${new Date(s.last_run_at).toLocaleString()}`}
              </div>
            </div>
            {canManage && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void toggle(s)}
                  disabled={busy === s.id}
                  className="text-slate-400 hover:text-blue-600 disabled:opacity-50"
                  title={s.is_active ? 'Pause' : 'Resume'}
                >
                  {s.is_active ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <button
                  onClick={() => void remove(s)}
                  disabled={busy === s.id}
                  className="text-slate-400 hover:text-red-500 disabled:opacity-50"
                  title="Delete"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            )}
            {!s.is_active && (
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                paused
              </span>
            )}
          </li>
        ))}
      </ul>

      {canManage && (
        <div className="mt-4 border-t border-slate-100 pt-4 flex flex-col sm:flex-row gap-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Schedule name"
            className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
          />
          <select
            value={newFreq}
            onChange={(e) => setNewFreq(e.target.value as 'daily' | 'weekly')}
            className="text-sm font-semibold rounded-lg border border-slate-200 px-3 py-2"
          >
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
          {newFreq === 'weekly' && (
            <select
              value={newDow}
              onChange={(e) => setNewDow(Number(e.target.value))}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              {DOW_LABELS.map((label, i) => (
                <option key={i} value={i}>{label}</option>
              ))}
            </select>
          )}
          <select
            value={newHour}
            onChange={(e) => setNewHour(Number(e.target.value))}
            className="text-sm rounded-lg border border-slate-200 px-3 py-2"
          >
            {Array.from({ length: 24 }, (_, i) => (
              <option key={i} value={i}>{String(i).padStart(2, '0')}:00 UTC</option>
            ))}
          </select>
          <button
            onClick={() => void create()}
            disabled={busy === 'new' || !newName.trim()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300 hover:bg-blue-700 flex items-center gap-1"
          >
            <Plus size={14} />
            Add
          </button>
        </div>
      )}
    </section>
  );
}


const METRIC_OPTIONS = [
  'totalSpend', 'totalImpressions', 'totalClicks', 'totalConversions',
  'totalRevenue', 'blendedCPA', 'blendedCTR', 'blendedCVR', 'blendedCPC',
  'blendedCPM', 'blendedROAS',
];

const COMPARISON_LABELS: Record<string, string> = {
  gt: 'greater than',
  lt: 'less than',
  pct_change_gt: '% change over',
};

function AlertsSection({
  rules,
  loading,
  canManage,
  workspaceId,
  onChanged,
}: {
  rules: AlertRule[];
  loading: boolean;
  canManage: boolean;
  workspaceId: number;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | 'new' | null>(null);
  const [newName, setNewName] = useState('');
  const [newMetric, setNewMetric] = useState('blendedCPA');
  const [newCmp, setNewCmp] = useState<'gt' | 'lt' | 'pct_change_gt'>('gt');
  const [newThreshold, setNewThreshold] = useState('50');
  const [newChannelType, setNewChannelType] = useState<'slack' | 'google_chat' | 'email'>('slack');
  const [newChannelTarget, setNewChannelTarget] = useState('');

  async function create() {
    if (!newName.trim() || !canManage) return;
    setBusy('new');
    try {
      const channel = newChannelTarget.trim()
        ? [
            newChannelType === 'email'
              ? { type: 'email', to: newChannelTarget.trim() }
              : { type: newChannelType, url: newChannelTarget.trim() },
          ]
        : [];
      await apiFetch(`/api/workspaces/${workspaceId}/alerts`, {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          metric: newMetric,
          comparison: newCmp,
          threshold: newThreshold,
          channels: channel,
        }),
      });
      setNewName('');
      setNewChannelTarget('');
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function toggle(r: AlertRule) {
    setBusy(r.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/alerts/${r.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !r.is_active }),
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove(r: AlertRule) {
    if (!confirm(`Delete alert "${r.name}"?`)) return;
    setBusy(r.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/alerts/${r.id}`, {
        method: 'DELETE',
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <Bell size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Alert rules</h2>
        <span className="text-xs text-slate-400 ml-2">evaluated after each sync</span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && rules.length === 0 && (
        <p className="text-sm text-slate-400">No alert rules yet.</p>
      )}

      <ul className="divide-y divide-slate-50">
        {rules.map((r) => (
          <li key={r.id} className="py-3 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-sm text-slate-800 truncate flex items-center gap-2">
                {r.name}
                {r.last_triggered_at && (
                  <AlertTriangle size={12} className="text-amber-500" />
                )}
              </div>
              <div className="text-xs text-slate-500 truncate">
                {r.metric} {COMPARISON_LABELS[r.comparison] || r.comparison} {r.threshold}
                {r.last_triggered_at && (
                  <> · last fired {new Date(r.last_triggered_at).toLocaleString()}
                    {r.last_value && ` at ${r.last_value}`}
                  </>
                )}
                {r.channels.length > 0 && ` · → ${r.channels.map(c => c.type).join(', ')}`}
              </div>
            </div>
            {canManage && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void toggle(r)}
                  disabled={busy === r.id}
                  className="text-slate-400 hover:text-blue-600 disabled:opacity-50"
                  title={r.is_active ? 'Pause' : 'Resume'}
                >
                  {r.is_active ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <button
                  onClick={() => void remove(r)}
                  disabled={busy === r.id}
                  className="text-slate-400 hover:text-red-500 disabled:opacity-50"
                  title="Delete"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            )}
            {!r.is_active && (
              <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                paused
              </span>
            )}
          </li>
        ))}
      </ul>

      {canManage && (
        <div className="mt-4 border-t border-slate-100 pt-4 space-y-2">
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Alert name"
              className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
            />
            <select
              value={newMetric}
              onChange={(e) => setNewMetric(e.target.value)}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              {METRIC_OPTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <select
              value={newCmp}
              onChange={(e) => setNewCmp(e.target.value as 'gt' | 'lt' | 'pct_change_gt')}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              <option value="gt">&gt;</option>
              <option value="lt">&lt;</option>
              <option value="pct_change_gt">%Δ &gt;</option>
            </select>
            <input
              value={newThreshold}
              onChange={(e) => setNewThreshold(e.target.value)}
              placeholder="50"
              className="w-20 px-3 py-2 rounded-lg border border-slate-200 text-sm"
            />
          </div>
          <div className="flex flex-col sm:flex-row gap-2">
            <select
              value={newChannelType}
              onChange={(e) => setNewChannelType(e.target.value as 'slack' | 'google_chat' | 'email')}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              <option value="slack">Slack webhook</option>
              <option value="google_chat">Google Chat webhook</option>
              <option value="email">Email</option>
            </select>
            <input
              value={newChannelTarget}
              onChange={(e) => setNewChannelTarget(e.target.value)}
              placeholder={newChannelType === 'email' ? 'team@example.com' : 'https://hooks…'}
              className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
            />
            <button
              onClick={() => void create()}
              disabled={busy === 'new' || !newName.trim()}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300 hover:bg-blue-700 flex items-center gap-1"
            >
              <Plus size={14} />
              Add
            </button>
          </div>
        </div>
      )}
    </section>
  );
}


const PACE_TONE: Record<Pacing['status'], { bar: string; pill: string; label: string }> = {
  on_pace: { bar: 'bg-emerald-500', pill: 'bg-emerald-100 text-emerald-700', label: 'on pace' },
  under_pace: { bar: 'bg-blue-500', pill: 'bg-blue-100 text-blue-700', label: 'under pace' },
  over_pace: { bar: 'bg-amber-500', pill: 'bg-amber-100 text-amber-700', label: 'over pace' },
  exhausted: { bar: 'bg-red-500', pill: 'bg-red-100 text-red-700', label: 'exhausted' },
};

function BudgetsSection({
  budgets,
  loading,
  canManage,
  workspaceId,
  onChanged,
}: {
  budgets: Budget[];
  loading: boolean;
  canManage: boolean;
  workspaceId: number;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | 'new' | null>(null);
  const [name, setName] = useState('');
  const [scope, setScope] = useState<'workspace' | 'platform'>('workspace');
  const [scopeKey, setScopeKey] = useState('google');
  const [period, setPeriod] = useState<'monthly' | 'quarterly'>('monthly');
  const [amount, setAmount] = useState('10000');
  const [alertPct, setAlertPct] = useState(80);

  async function create() {
    if (!name.trim() || !canManage) return;
    setBusy('new');
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/budgets`, {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          scope_type: scope,
          scope_key: scope === 'workspace' ? null : scopeKey,
          period_type: period,
          amount,
          alert_at_pct: alertPct,
        }),
      });
      setName('');
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove(b: Budget) {
    if (!confirm(`Delete budget "${b.name}"?`)) return;
    setBusy(b.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/budgets/${b.id}`, {
        method: 'DELETE',
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <DollarSign size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Budgets</h2>
        <span className="text-xs text-slate-400 ml-2">linear pacing, evaluated after each sync</span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && budgets.length === 0 && (
        <p className="text-sm text-slate-400">No budgets yet.</p>
      )}

      <ul className="space-y-4">
        {budgets.map((b) => {
          const p = b.pacing;
          const tone = p ? PACE_TONE[p.status] : PACE_TONE.under_pace;
          const pct = Math.min(100, Math.round((p?.pct_used ?? 0) * 100));
          return (
            <li key={b.id} className="border border-slate-50 rounded-xl p-3">
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="min-w-0">
                  <div className="font-semibold text-sm text-slate-800 truncate">
                    {b.name}
                  </div>
                  <div className="text-xs text-slate-500 truncate">
                    {b.scope_type === 'workspace' ? 'All platforms' : `${b.scope_type}: ${b.scope_key}`}
                    {' · '}{b.period_type}{' · '}${Number(b.amount).toLocaleString()}
                    {p && ` · ${p.period_label}`}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-full ${tone.pill}`}>
                    {tone.label}
                  </span>
                  {canManage && (
                    <button
                      onClick={() => void remove(b)}
                      disabled={busy === b.id}
                      className="text-slate-400 hover:text-red-500 disabled:opacity-50"
                      title="Delete"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
              {p && (
                <>
                  <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className={`h-full ${tone.bar} transition-all`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-500 mt-1">
                    <span>${p.spent.toLocaleString()} of ${p.amount.toLocaleString()}</span>
                    <span>
                      day {p.days_elapsed}/{p.days_total} · target ${p.target_at_today.toLocaleString()}
                    </span>
                  </div>
                </>
              )}
            </li>
          );
        })}
      </ul>

      {canManage && (
        <div className="mt-4 border-t border-slate-100 pt-4 space-y-2">
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Budget name"
              className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
            />
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value as 'workspace' | 'platform')}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              <option value="workspace">All platforms</option>
              <option value="platform">One platform</option>
            </select>
            {scope === 'platform' && (
              <select
                value={scopeKey}
                onChange={(e) => setScopeKey(e.target.value)}
                className="text-sm rounded-lg border border-slate-200 px-3 py-2"
              >
                {['google', 'meta', 'linkedin', 'tiktok', 'microsoft'].map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            )}
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value as 'monthly' | 'quarterly')}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
            </select>
          </div>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="flex-1 relative">
              <span className="absolute left-3 top-2 text-sm text-slate-400">$</span>
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                type="number"
                min="0"
                className="w-full pl-6 pr-3 py-2 rounded-lg border border-slate-200 text-sm"
                placeholder="10000"
              />
            </div>
            <div className="flex items-center gap-1">
              <span className="text-xs text-slate-500">alert at</span>
              <input
                value={alertPct}
                onChange={(e) => setAlertPct(Number(e.target.value))}
                type="number"
                min="0"
                max="100"
                className="w-16 px-2 py-2 rounded-lg border border-slate-200 text-sm text-center"
              />
              <span className="text-xs text-slate-500">%</span>
            </div>
            <button
              onClick={() => void create()}
              disabled={busy === 'new' || !name.trim()}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300 hover:bg-blue-700 flex items-center gap-1"
            >
              <Plus size={14} />
              Add
            </button>
          </div>
        </div>
      )}
    </section>
  );
}


const KPI_FORMATS = ['number', 'integer', 'currency', 'percent', 'ratio'];

function KpisSection({
  kpis,
  loading,
  canManage,
  workspaceId,
  onChanged,
}: {
  kpis: CustomKpi[];
  loading: boolean;
  canManage: boolean;
  workspaceId: number;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<number | 'new' | null>(null);
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('totalRevenue - totalSpend');
  const [format, setFormat] = useState('currency');
  const [createError, setCreateError] = useState<string | null>(null);
  const [variables, setVariables] = useState<string[]>([]);

  useEffect(() => {
    apiJson<{ variables: string[] }>('/api/kpi-variables')
      .then((v) => setVariables(v.variables))
      .catch(() => {});
  }, []);

  async function create() {
    if (!name.trim() || !canManage) return;
    setBusy('new');
    setCreateError(null);
    try {
      const res = await apiFetch(`/api/workspaces/${workspaceId}/kpis`, {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), formula, format }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setCreateError(body.detail || `Failed (${res.status})`);
        return;
      }
      setName('');
      setFormula('totalRevenue - totalSpend');
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  async function remove(k: CustomKpi) {
    if (!confirm(`Delete KPI "${k.name}"?`)) return;
    setBusy(k.id);
    try {
      await apiFetch(`/api/workspaces/${workspaceId}/kpis/${k.id}`, {
        method: 'DELETE',
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <Calculator size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Custom KPIs</h2>
        <span className="text-xs text-slate-400 ml-2">formula-based metrics on the dashboard</span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && kpis.length === 0 && (
        <p className="text-sm text-slate-400">No custom KPIs yet.</p>
      )}

      <ul className="divide-y divide-slate-50">
        {kpis.map((k) => (
          <li key={k.id} className="py-3 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-sm text-slate-800 truncate">{k.name}</div>
              <div className="text-xs text-slate-500 truncate font-mono">{k.formula}</div>
              <div className="text-[10px] text-slate-400 uppercase tracking-wider mt-0.5">
                format: {k.format}
              </div>
            </div>
            {canManage && (
              <button
                onClick={() => void remove(k)}
                disabled={busy === k.id}
                className="text-slate-400 hover:text-red-500 disabled:opacity-50"
                title="Delete"
              >
                <Trash2 size={14} />
              </button>
            )}
          </li>
        ))}
      </ul>

      {canManage && (
        <div className="mt-4 border-t border-slate-100 pt-4 space-y-2">
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="KPI name (e.g. Net margin)"
              className="flex-1 px-3 py-2 rounded-lg border border-slate-200 text-sm"
            />
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className="text-sm rounded-lg border border-slate-200 px-3 py-2"
            >
              {KPI_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>
          <textarea
            value={formula}
            onChange={(e) => setFormula(e.target.value)}
            rows={2}
            className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm font-mono"
            placeholder="totalRevenue - totalSpend"
          />
          {variables.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">Available variables</summary>
              <div className="mt-1 flex flex-wrap gap-1">
                {variables.map((v) => (
                  <button
                    key={v}
                    onClick={() => setFormula((f) => f + (f.endsWith(' ') ? v : ` ${v}`))}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-100 hover:bg-slate-200"
                  >
                    {v}
                  </button>
                ))}
              </div>
            </details>
          )}
          {createError && (
            <p className="text-xs text-red-600">{createError}</p>
          )}
          <button
            onClick={() => void create()}
            disabled={busy === 'new' || !name.trim()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300 hover:bg-blue-700 flex items-center gap-1"
          >
            <Plus size={14} />
            Add KPI
          </button>
        </div>
      )}
    </section>
  );
}


function DigestSection({ workspaceId }: { workspaceId: number }) {
  const { user } = useAuth();
  const [status, setStatus] = useState<DigestStatus | null>(null);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const s = await apiJson<DigestStatus>(`/api/workspaces/${workspaceId}/digests/me`);
      setStatus(s);
      setEmail(s.email || user?.email || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function setCadence(cadence: 'off' | 'daily' | 'weekly') {
    setBusy(true);
    setError(null);
    try {
      if (cadence === 'off') {
        await apiFetch(`/api/workspaces/${workspaceId}/digests/unsubscribe`, {
          method: 'POST',
        });
      } else {
        const res = await apiFetch(`/api/workspaces/${workspaceId}/digests/subscribe`, {
          method: 'POST',
          body: JSON.stringify({
            cadence,
            email: email.trim() || undefined,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `Failed (${res.status})`);
        }
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update');
    } finally {
      setBusy(false);
    }
  }

  const currentCadence: 'off' | 'daily' | 'weekly' =
    status?.subscribed && status.cadence ? status.cadence : 'off';

  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <Mail size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Email digest</h2>
        <span className="text-xs text-slate-400 ml-2">a snapshot of your workspace, delivered on a schedule</span>
      </div>

      <div className="space-y-3">
        <label className="block text-xs text-slate-500 font-bold uppercase tracking-wider">
          Send to
        </label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          type="email"
          disabled={busy}
          className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm"
        />

        <div className="flex flex-wrap items-center gap-2 pt-2">
          {(['off', 'daily', 'weekly'] as const).map((c) => (
            <button
              key={c}
              onClick={() => void setCadence(c)}
              disabled={busy || c === currentCadence}
              className={`text-xs font-bold px-3 py-1.5 rounded-full border transition-colors disabled:cursor-default ${
                c === currentCadence
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-slate-600 border-slate-200 hover:border-blue-300'
              }`}
            >
              {c === 'off' ? 'Off' : c === 'daily' ? 'Daily · 13:00 UTC' : 'Weekly · Mon 13:00 UTC'}
            </button>
          ))}
        </div>

        {error && <p className="text-xs text-red-600">{error}</p>}

        {status?.subscribed && (
          <div className="text-xs text-slate-500 pt-2 border-t border-slate-100">
            {status.next_send_at && (
              <div>Next send: {new Date(status.next_send_at).toLocaleString()}</div>
            )}
            {status.last_sent_at && (
              <div>Last sent: {new Date(status.last_sent_at).toLocaleString()}</div>
            )}
            {status.last_send_error && (
              <div className="text-red-600 mt-1">
                Last delivery error: {status.last_send_error}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}


function AuditSection({
  entries,
  loading,
}: {
  entries: AuditEntry[];
  loading: boolean;
}) {
  return (
    <section className="border border-slate-100 rounded-2xl p-6 bg-white shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <ScrollText size={18} className="text-blue-600" />
        <h2 className="text-lg font-black text-slate-900">Audit log</h2>
        <span className="text-xs text-slate-400 ml-2">latest 100 events</span>
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {!loading && entries.length === 0 && (
        <p className="text-sm text-slate-400">No events yet.</p>
      )}

      <ul className="divide-y divide-slate-50 max-h-[480px] overflow-y-auto">
        {entries.map((e) => (
          <li key={e.id} className="py-3 text-sm flex items-start gap-3">
            <Shield size={14} className="text-slate-300 mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-bold text-slate-800">{e.action}</span>
                <span className="text-xs text-slate-400">
                  by {e.actor_subject}
                </span>
                {e.target_type && (
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                    {e.target_type}#{e.target_id}
                  </span>
                )}
              </div>
              {e.payload && Object.keys(e.payload).length > 0 && (
                <pre className="mt-1 text-[11px] bg-slate-50 border border-slate-100 rounded p-2 overflow-x-auto">
                  {JSON.stringify(e.payload, null, 2)}
                </pre>
              )}
            </div>
            <span className="text-xs text-slate-400 flex-shrink-0">
              {e.created_at
                ? new Date(e.created_at).toLocaleString([], {
                    month: 'short', day: 'numeric',
                    hour: '2-digit', minute: '2-digit',
                  })
                : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
