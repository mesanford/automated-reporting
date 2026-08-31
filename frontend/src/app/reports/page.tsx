"use client";

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  FileText, ArrowLeft, Zap, Filter, TrendingUp, Search, Download,
} from 'lucide-react';
import { useWorkspace } from '@/lib/workspace';
import { apiFetch, apiJson } from '@/lib/api';

interface ReportListRow {
  id: number;
  created_at: string;
  current_period_label: string | null;
  prior_period_label: string | null;
  comparison_type: string | null;
  scorecards: {
    totalSpend?: number;
    totalConversions?: number;
    totalRevenue?: number;
    blendedCPA?: number;
    blendedROAS?: number | null;
  };
}

export default function ReportsListPage() {
  const { active } = useWorkspace();
  const [rows, setRows] = useState<ReportListRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<ReportListRow[]>('/api/reports');
        if (!cancelled) setRows(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load reports');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [active]);

  const filtered = useMemo(() => {
    const f = from ? new Date(from).getTime() : 0;
    const t = to ? new Date(to).getTime() + 24 * 60 * 60 * 1000 : Infinity;
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      const ts = new Date(r.created_at).getTime();
      if (ts < f || ts > t) return false;
      if (q && !(r.current_period_label ?? '').toLowerCase().includes(q)) return false;
      return true;
    });
  }, [rows, from, to, search]);

  const fmt$ = (v: number | undefined | null) =>
    v == null ? '—' : `${active?.base_currency ?? '$'} ${v.toLocaleString()}`;

  /** Download CSV via authenticated fetch so the Bearer + X-Workspace-Id
   * headers are attached; <a href> can't carry them. */
  async function downloadCsv(reportId: number) {
    const res = await apiFetch(`/api/reports/${reportId}/csv`);
    if (!res.ok) {
      alert('Failed to export CSV.');
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `report-${reportId}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <nav className="border-b border-slate-100 bg-background/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/" className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200">
              <Zap className="text-white w-6 h-6 fill-white" />
            </Link>
            <h1 className="text-2xl font-black tracking-tight text-slate-900 flex items-center gap-2">
              <FileText className="text-blue-600" size={22} />
              Reports
            </h1>
            <span className="text-xs text-slate-400 ml-2">{rows.length} total</span>
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

      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-10">
        {/* Filters */}
        <div className="flex flex-wrap items-end gap-3 mb-6 p-4 border border-slate-100 rounded-2xl bg-white shadow-sm">
          <div className="flex items-center gap-2 text-xs text-slate-500 uppercase font-bold tracking-wider mr-2">
            <Filter size={14} className="text-blue-600" />
            Filter
          </div>
          <label className="text-xs text-slate-500 flex flex-col gap-1">
            From
            <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
              className="text-sm px-2 py-1.5 rounded border border-slate-200" />
          </label>
          <label className="text-xs text-slate-500 flex flex-col gap-1">
            To
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
              className="text-sm px-2 py-1.5 rounded border border-slate-200" />
          </label>
          <label className="flex-1 min-w-[200px] text-xs text-slate-500 flex flex-col gap-1">
            Search period
            <div className="relative">
              <Search size={12} className="absolute left-2 top-2 text-slate-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="e.g. 2026-05"
                className="w-full text-sm pl-7 pr-2 py-1.5 rounded border border-slate-200"
              />
            </div>
          </label>
          {(from || to || search) && (
            <button
              onClick={() => { setFrom(''); setTo(''); setSearch(''); }}
              className="text-xs text-slate-500 hover:text-blue-600 px-2 py-1.5"
            >
              Clear
            </button>
          )}
        </div>

        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {error && (
          <div className="border border-red-200 bg-red-50 text-red-700 rounded-xl p-4 text-sm">
            {error}
          </div>
        )}
        {!loading && filtered.length === 0 && (
          <p className="text-sm text-slate-400">No reports match the filter.</p>
        )}

        <div className="border border-slate-100 rounded-2xl bg-white shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500 font-bold">
              <tr>
                <th className="text-left px-4 py-2">Generated</th>
                <th className="text-left px-4 py-2">Period</th>
                <th className="text-right px-4 py-2">Spend</th>
                <th className="text-right px-4 py-2">Conversions</th>
                <th className="text-right px-4 py-2">CPA</th>
                <th className="text-right px-4 py-2">ROAS</th>
                <th className="text-right px-4 py-2">Export</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {filtered.map((r) => {
                const sc = r.scorecards || {};
                return (
                  <tr key={r.id} className="hover:bg-slate-50/50 transition-colors">
                    <td className="px-4 py-3">
                      <Link
                        href={`/?report=${r.id}`}
                        className="font-semibold text-slate-800 hover:text-blue-600 inline-flex items-center gap-1"
                      >
                        {new Date(r.created_at).toLocaleString([], {
                          month: 'short', day: 'numeric', year: 'numeric',
                          hour: '2-digit', minute: '2-digit',
                        })}
                        <TrendingUp size={12} className="opacity-60" />
                      </Link>
                      <div className="text-[10px] text-slate-400">#{r.id}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {r.current_period_label || '—'}
                      {r.prior_period_label && (
                        <div className="text-[10px] text-slate-400">vs {r.prior_period_label}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-semibold">{fmt$(sc.totalSpend)}</td>
                    <td className="px-4 py-3 text-right">{sc.totalConversions?.toLocaleString() ?? '—'}</td>
                    <td className="px-4 py-3 text-right">{fmt$(sc.blendedCPA)}</td>
                    <td className="px-4 py-3 text-right">
                      {sc.blendedROAS != null ? `${sc.blendedROAS}x` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => downloadCsv(r.id)}
                          className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-800 text-xs font-bold"
                        >
                          <Download size={12} />
                          CSV
                        </button>
                        <Link
                          href={`/reports/${r.id}/print`}
                          target="_blank"
                          className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-800 text-xs font-bold"
                        >
                          PDF
                        </Link>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
