"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Activity, ArrowLeft, Zap, FileText, RefreshCcw, Shield, Bell, DollarSign,
  Users, UserPlus, Clock, Wrench, AlertTriangle,
} from 'lucide-react';
import { useWorkspace } from '@/lib/workspace';
import { apiJson } from '@/lib/api';

interface AuditEvent {
  kind: 'audit';
  id: string;
  at: string | null;
  actor: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown> | null;
}

interface SyncEvent {
  kind: 'sync';
  id: string;
  at: string | null;
  actor: string | null;
  status: string;
  connection_id: number | null;
  report_id: number | null;
  error_message: string | null;
}

interface ReportEvent {
  kind: 'report';
  id: string;
  at: string | null;
  actor: string | null;
  report_id: number;
  period_label: string | null;
  total_spend: number | null;
  total_conversions: number | null;
}

type ActivityEvent = AuditEvent | SyncEvent | ReportEvent;

const ACTION_ICON: Record<string, React.ReactNode> = {
  'invite.create': <UserPlus size={14} />,
  'invite.accept': <UserPlus size={14} />,
  'member.role_change': <Users size={14} />,
  'member.remove': <Users size={14} />,
  'optimization.approve': <Wrench size={14} />,
  'optimization.execute': <Wrench size={14} />,
  'connection.delete': <RefreshCcw size={14} />,
  'schedule.create': <Clock size={14} />,
  'schedule.update': <Clock size={14} />,
  'schedule.delete': <Clock size={14} />,
  'alert.create': <Bell size={14} />,
  'alert.delete': <Bell size={14} />,
  'alert.trigger': <AlertTriangle size={14} />,
  'budget.create': <DollarSign size={14} />,
  'budget.delete': <DollarSign size={14} />,
  'budget.threshold_crossed': <DollarSign size={14} />,
};

export default function ActivityPage() {
  const { active } = useWorkspace();
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        const rows = await apiJson<ActivityEvent[]>(
          `/api/workspaces/${active.id}/activity?limit=200`,
        );
        if (!cancelled) setEvents(rows);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load activity');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [active]);

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <nav className="border-b border-slate-100 bg-background/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/" className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200">
              <Zap className="text-white w-6 h-6 fill-white" />
            </Link>
            <h1 className="text-2xl font-black tracking-tight text-slate-900 flex items-center gap-2">
              <Activity className="text-blue-600" size={22} />
              Activity
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

      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-10">
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {error && (
          <div className="border border-red-200 bg-red-50 text-red-700 rounded-xl p-4 text-sm">
            {error}
          </div>
        )}
        {!loading && events.length === 0 && (
          <p className="text-sm text-slate-400">No activity yet.</p>
        )}

        <ul className="space-y-1">
          {events.map((e) => (
            <li key={e.id}>
              <EventRow event={e} />
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}

function EventRow({ event }: { event: ActivityEvent }) {
  const ts = event.at ? new Date(event.at).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }) : '';

  if (event.kind === 'report') {
    return (
      <div className="flex items-start gap-3 py-2 border-b border-slate-50 text-sm">
        <span className="w-7 h-7 rounded-full bg-blue-50 text-blue-600 flex items-center justify-center flex-shrink-0">
          <FileText size={14} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-slate-800">
            <span className="font-semibold">Report #{event.report_id}</span>
            {event.period_label && (
              <span className="text-slate-400"> · {event.period_label}</span>
            )}
            {event.total_spend != null && (
              <span className="text-slate-400"> · ${event.total_spend.toLocaleString()} spend</span>
            )}
          </div>
        </div>
        <span className="text-xs text-slate-400 flex-shrink-0">{ts}</span>
      </div>
    );
  }

  if (event.kind === 'sync') {
    const failed = event.status === 'failed';
    return (
      <div className="flex items-start gap-3 py-2 border-b border-slate-50 text-sm">
        <span className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 ${
          failed ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600'
        }`}>
          <RefreshCcw size={14} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-slate-800">
            <span className="font-semibold">
              Sync {event.connection_id == null ? 'all connections' : `connection #${event.connection_id}`}
            </span>
            <span className={`ml-2 text-xs ${failed ? 'text-red-600' : 'text-emerald-600'}`}>
              {event.status}
            </span>
            {event.report_id != null && (
              <span className="text-slate-400"> · report #{event.report_id}</span>
            )}
          </div>
          {failed && event.error_message && (
            <div className="text-xs text-red-600 mt-0.5">{event.error_message}</div>
          )}
        </div>
        <span className="text-xs text-slate-400 flex-shrink-0">{ts}</span>
      </div>
    );
  }

  // audit
  const icon = ACTION_ICON[event.action] ?? <Shield size={14} />;
  return (
    <div className="flex items-start gap-3 py-2 border-b border-slate-50 text-sm">
      <span className="w-7 h-7 rounded-full bg-slate-50 text-slate-600 flex items-center justify-center flex-shrink-0">
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-slate-800">
          <span className="font-semibold">{event.action}</span>
          <span className="text-slate-400 ml-2">by {event.actor ?? 'system'}</span>
          {event.target_type && (
            <span className="text-[10px] uppercase tracking-wider text-slate-400 ml-2">
              {event.target_type}#{event.target_id}
            </span>
          )}
        </div>
        {event.payload && Object.keys(event.payload).length > 0 && (
          <pre className="text-[10px] bg-slate-50 border border-slate-100 rounded p-1.5 mt-1 overflow-x-auto">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        )}
      </div>
      <span className="text-xs text-slate-400 flex-shrink-0">{ts}</span>
    </div>
  );
}
