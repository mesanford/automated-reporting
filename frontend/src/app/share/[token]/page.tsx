"use client";

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import { AlertTriangle, Download, FileText, Lock } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

interface ShareResponse {
  report: {
    id: number;
    created_at: string | null;
    currentPeriodLabel: string | null;
    priorPeriodLabel: string | null;
    scorecards: Record<string, number | null>;
    platformSummary: Array<Record<string, unknown>>;
    campaignSummary: Array<Record<string, unknown>>;
    geminiAnalysis: string | null;
    usedMockData?: boolean;
  };
  workspace?: {
    name: string | null;
    base_currency: string | null;
  };
  share: {
    expires_at: string;
    view_count: number;
  };
}

interface ErrorPayload {
  detail?: string;
}

type State =
  | { kind: 'loading' }
  | { kind: 'gone'; message: string }
  | { kind: 'notfound' }
  | { kind: 'ok'; data: ShareResponse };

export default function PublicSharePage() {
  const params = useParams<{ token: string }>();
  const [state, setState] = useState<State>({ kind: 'loading' });
  const [downloading, setDownloading] = useState(false);

  async function handleDownload() {
    if (!params?.token) return;
    setDownloading(true);
    try {
      const res = await fetch(`${API_BASE}/api/share/${params.token}/pdf`);
      if (!res.ok) {
        // 503 means the server has no Chromium; the browser print route still
        // produces a usable PDF, just not a byte-identical one.
        window.open(`/share/${params.token}/print`, '_blank', 'noopener');
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filenameFrom(res) || 'report.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  useEffect(() => {
    if (!params?.token) return;
    let cancelled = false;
    (async () => {
      try {
        // No auth headers — public endpoint by design.
        const res = await fetch(`${API_BASE}/api/share/${params.token}`);
        if (cancelled) return;
        if (res.status === 410) {
          const body: ErrorPayload = await res.json().catch(() => ({}));
          setState({ kind: 'gone', message: body.detail || 'This link is no longer valid.' });
          return;
        }
        if (!res.ok) {
          setState({ kind: 'notfound' });
          return;
        }
        const data = (await res.json()) as ShareResponse;
        setState({ kind: 'ok', data });
      } catch {
        if (!cancelled) setState({ kind: 'notfound' });
      }
    })();
    return () => { cancelled = true; };
  }, [params?.token]);

  if (state.kind === 'loading') return <Shell><p className="text-slate-400">Loading…</p></Shell>;
  if (state.kind === 'gone') return <ErrorState icon={<Lock size={28} />} title="Link no longer valid" message={state.message} />;
  if (state.kind === 'notfound') return <ErrorState icon={<AlertTriangle size={28} />} title="Link not found" message="The recipient may have a typo, or the link was never minted." />;

  const r = state.data.report;
  const ccy = state.data.workspace?.base_currency ?? 'USD';
  const fmt = (n: number | null | undefined, unit: string = '') =>
    n == null ? '—' : unit === '$' ? `${ccy} ${n.toLocaleString()}` : `${unit}${n.toLocaleString()}`;

  return (
    <Shell>
      <div className="mb-6 flex items-center justify-between flex-wrap gap-2">
        <div>
          <div className="flex items-center gap-2 text-blue-600">
            <FileText size={18} />
            <span className="text-xs uppercase tracking-wider font-black">
              Shared report
            </span>
          </div>
          <h1 className="text-3xl font-black text-slate-900 mt-1">
            {r.currentPeriodLabel || `Report #${r.id}`}
          </h1>
          {r.priorPeriodLabel && (
            <p className="text-sm text-slate-500">vs. {r.priorPeriodLabel}</p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Server-rendered PDF, so every recipient gets an identical file
              regardless of browser. If the server has no Chromium it answers
              503 and we fall back to the browser's own print dialog. */}
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold hover:bg-blue-700 disabled:opacity-60"
          >
            <Download size={16} />
            {downloading ? 'Preparing…' : 'Download PDF'}
          </button>
          <div className="text-xs text-slate-400 text-right">
            Expires {new Date(state.data.share.expires_at).toLocaleDateString()}
            <br />
            Viewed {state.data.share.view_count} time{state.data.share.view_count === 1 ? '' : 's'}
          </div>
        </div>
      </div>

      {r.usedMockData && (
        <div className="flex items-start gap-3 p-4 mb-6 bg-amber-50 border border-amber-200 rounded-2xl">
          <AlertTriangle size={20} className="text-amber-600 mt-0.5 flex-shrink-0" />
          <div className="text-sm text-amber-900">
            <span className="font-black uppercase tracking-wide">Includes fabricated demo data.</span>{' '}
            At least one connected channel has no live API integration yet, so its numbers are
            randomly generated sample data, not real performance figures.
          </div>
        </div>
      )}

      {/* Headline scorecards */}
      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
        {[
          ['Spend', '$', r.scorecards.totalSpend],
          ['Revenue', '$', r.scorecards.totalRevenue],
          ['Conversions', '', r.scorecards.totalConversions],
          ['ROAS', '', r.scorecards.blendedROAS],
        ].map(([label, prefix, val]) => (
          <div key={String(label)} className="border border-slate-100 rounded-xl bg-white p-3">
            <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400">{label}</div>
            <div className="text-lg font-black text-slate-900">
              {typeof val === 'number' ? fmt(val, String(prefix)) : (val ?? '—')}
            </div>
          </div>
        ))}
      </section>

      {/* Platforms */}
      {r.platformSummary?.length > 0 && (
        <section className="mb-8">
          <h2 className="text-base font-black text-slate-900 mb-2">By platform</h2>
          <div className="border border-slate-100 rounded-xl bg-white overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500 font-bold">
                <tr>
                  <th className="text-left p-2">Platform</th>
                  <th className="text-right p-2">Spend</th>
                  <th className="text-right p-2">Conv</th>
                  <th className="text-right p-2">CPA</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {r.platformSummary.map((row, i) => (
                  <tr key={i}>
                    <td className="p-2 font-semibold">{String(row.platform)}</td>
                    <td className="p-2 text-right">{fmt(Number(row.spend), '$')}</td>
                    <td className="p-2 text-right">{Number(row.conversions).toLocaleString()}</td>
                    <td className="p-2 text-right">{fmt(Number(row.cpa), '$')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* AI analysis */}
      {r.geminiAnalysis && (
        <section className="mb-8 prose prose-sm max-w-none">
          <ReactMarkdown>{r.geminiAnalysis}</ReactMarkdown>
        </section>
      )}

      <footer className="text-[11px] text-slate-400 border-t border-slate-100 pt-4">
        Read-only view. Generated by Antigravity.
      </footer>
    </Shell>
  );
}

/** Pull the server-supplied filename out of Content-Disposition. */
function filenameFrom(res: Response): string | null {
  const cd = res.headers.get('Content-Disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  return m ? m[1] : null;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <main className="max-w-4xl mx-auto px-6 py-10">{children}</main>
    </div>
  );
}

function ErrorState({
  icon, title, message,
}: { icon: React.ReactNode; title: string; message: string }) {
  return (
    <Shell>
      <div className="max-w-md mx-auto text-center py-20">
        <div className="w-14 h-14 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center mx-auto">
          {icon}
        </div>
        <h2 className="text-2xl font-black text-slate-900 mt-4">{title}</h2>
        <p className="text-sm text-slate-500 mt-2">{message}</p>
      </div>
    </Shell>
  );
}
