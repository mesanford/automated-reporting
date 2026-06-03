"use client";

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import { apiJson } from '@/lib/api';
import { useWorkspace } from '@/lib/workspace';

interface PrintableReport {
  id: number;
  created_at: string | null;
  currentPeriodLabel: string | null;
  priorPeriodLabel: string | null;
  comparisonType: string | null;
  scorecards: Record<string, number | null>;
  scorecardDeltas: Record<string, { value?: string; direction?: string; confidence?: string }>;
  platformSummary: Array<Record<string, unknown>>;
  campaignSummary: Array<Record<string, unknown>>;
  hierarchySummary: {
    campaign: Array<Record<string, unknown>>;
    adGroup: Array<Record<string, unknown>>;
    adAsset: Array<Record<string, unknown>>;
  };
  topPerformer: Record<string, unknown> | null;
  bottomPerformer: Record<string, unknown> | null;
  geminiAnalysis: string | null;
}

const METRIC_ROWS: Array<[keyof PrintableReport['scorecards'] & string, string, string]> = [
  ['totalSpend', 'Total Spend', '$'],
  ['totalImpressions', 'Impressions', ''],
  ['totalClicks', 'Clicks', ''],
  ['totalConversions', 'Conversions', ''],
  ['totalRevenue', 'Revenue', '$'],
  ['blendedCPA', 'Blended CPA', '$'],
  ['blendedCTR', 'Blended CTR', '%'],
  ['blendedCVR', 'Blended CVR', '%'],
  ['blendedCPC', 'Blended CPC', '$'],
  ['blendedCPM', 'Blended CPM', '$'],
  ['blendedROAS', 'Blended ROAS', ''],
];

export default function PrintableReportPage() {
  const params = useParams<{ id: string }>();
  const { active } = useWorkspace();
  const [report, setReport] = useState<PrintableReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<PrintableReport>(`/api/reports/${params.id}`);
        if (!cancelled) setReport(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load report');
      }
    })();
    return () => { cancelled = true; };
  }, [params?.id]);

  // Trigger the print dialog once the report has rendered. Users can also
  // hit Cmd/Ctrl-P themselves.
  function handlePrint() {
    window.print();
  }

  if (error) return <PrintShell><p className="text-red-600">{error}</p></PrintShell>;
  if (!report) return <PrintShell><p className="text-slate-400">Loading…</p></PrintShell>;

  const ccy = active?.base_currency ?? 'USD';
  const fmt = (v: number | null | undefined, unit: string) => {
    if (v == null) return '—';
    if (unit === '$') return `${ccy} ${v.toLocaleString()}`;
    if (unit === '%') return `${v}%`;
    return v.toLocaleString();
  };

  return (
    <PrintShell>
      {/* Print button — hidden in print output via @media print. */}
      <div className="no-print mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-black text-slate-900">
          Report #{report.id}
        </h1>
        <button
          onClick={handlePrint}
          className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-bold hover:bg-blue-700"
        >
          Print / Save as PDF
        </button>
      </div>

      {/* Cover */}
      <section className="mb-8 border-b border-slate-200 pb-6">
        <h2 className="text-3xl font-black text-slate-900">
          {report.currentPeriodLabel || `Report #${report.id}`}
        </h2>
        {report.priorPeriodLabel && (
          <p className="text-slate-500 mt-1">vs. {report.priorPeriodLabel}</p>
        )}
        <p className="text-xs text-slate-400 mt-2">
          Generated {report.created_at ? new Date(report.created_at).toLocaleString() : '—'}
          {report.comparisonType && ` · ${report.comparisonType}`}
        </p>
        {active && <p className="text-xs text-slate-400">Workspace: {active.name} ({ccy})</p>}
      </section>

      {/* Scorecards */}
      <section className="mb-8 break-inside-avoid">
        <h3 className="text-lg font-black text-slate-900 mb-3">Headline metrics</h3>
        <table className="w-full text-sm border-collapse">
          <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500 font-bold">
            <tr>
              <th className="text-left p-2 border border-slate-100">Metric</th>
              <th className="text-right p-2 border border-slate-100">Value</th>
              <th className="text-right p-2 border border-slate-100">Δ vs prior</th>
              <th className="text-left p-2 border border-slate-100">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map(([key, label, unit]) => {
              const v = report.scorecards?.[key];
              const delta = report.scorecardDeltas?.[key];
              const fallbackKey = key.replace('total', '').toLowerCase();
              const d = delta ?? report.scorecardDeltas?.[fallbackKey];
              return (
                <tr key={key}>
                  <td className="p-2 border border-slate-100 font-semibold">{label}</td>
                  <td className="p-2 border border-slate-100 text-right">{fmt(v, unit)}</td>
                  <td className="p-2 border border-slate-100 text-right">{d?.value ?? '—'}</td>
                  <td className="p-2 border border-slate-100 text-slate-500 text-xs">
                    {d?.confidence ?? '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {/* Platform breakdown */}
      {report.platformSummary?.length > 0 && (
        <section className="mb-8 break-inside-avoid">
          <h3 className="text-lg font-black text-slate-900 mb-3">By platform</h3>
          <table className="w-full text-sm border-collapse">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500 font-bold">
              <tr>
                {['Platform', 'Spend', 'Conversions', 'CPA', 'CTR', 'ROAS', 'Share'].map((h) => (
                  <th key={h} className="p-2 border border-slate-100 text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.platformSummary.map((row, i) => (
                <tr key={i}>
                  <td className="p-2 border border-slate-100 font-semibold">{String(row.platform)}</td>
                  <td className="p-2 border border-slate-100">{fmt(Number(row.spend), '$')}</td>
                  <td className="p-2 border border-slate-100">{Number(row.conversions).toLocaleString()}</td>
                  <td className="p-2 border border-slate-100">{fmt(Number(row.cpa), '$')}</td>
                  <td className="p-2 border border-slate-100">{Number(row.ctr)}%</td>
                  <td className="p-2 border border-slate-100">{row.roas != null ? `${row.roas}x` : '—'}</td>
                  <td className="p-2 border border-slate-100">{Number(row.spend_share)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {/* Top campaigns */}
      {report.campaignSummary?.length > 0 && (
        <section className="mb-8">
          <h3 className="text-lg font-black text-slate-900 mb-3">Campaigns</h3>
          <table className="w-full text-sm border-collapse">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500 font-bold">
              <tr>
                {['Platform', 'Campaign', 'Spend', 'Conv', 'CPA', 'CTR', 'ROAS'].map((h) => (
                  <th key={h} className="p-2 border border-slate-100 text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.campaignSummary.slice(0, 50).map((row, i) => (
                <tr key={i} className="break-inside-avoid">
                  <td className="p-2 border border-slate-100">{String(row.platform)}</td>
                  <td className="p-2 border border-slate-100">{String(row.campaign)}</td>
                  <td className="p-2 border border-slate-100">{fmt(Number(row.spend), '$')}</td>
                  <td className="p-2 border border-slate-100">{Number(row.conversions).toLocaleString()}</td>
                  <td className="p-2 border border-slate-100">{fmt(Number(row.cpa), '$')}</td>
                  <td className="p-2 border border-slate-100">{Number(row.ctr)}%</td>
                  <td className="p-2 border border-slate-100">{row.roas != null ? `${row.roas}x` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.campaignSummary.length > 50 && (
            <p className="text-xs text-slate-400 mt-2">
              Showing top 50 of {report.campaignSummary.length} campaigns.
            </p>
          )}
        </section>
      )}

      {/* AI analysis */}
      {report.geminiAnalysis && (
        <section className="mb-8 page-break-before">
          <h3 className="text-lg font-black text-slate-900 mb-3">Analysis</h3>
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown>{report.geminiAnalysis}</ReactMarkdown>
          </div>
        </section>
      )}

      {/* Print rules */}
      <style jsx global>{`
        @media print {
          .no-print { display: none !important; }
          @page { margin: 1.5cm; }
          body { background: white !important; }
          .break-inside-avoid { break-inside: avoid; }
          .page-break-before { page-break-before: always; }
        }
      `}</style>
    </PrintShell>
  );
}

function PrintShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white min-h-screen">
      <main className="max-w-4xl mx-auto px-6 py-10">{children}</main>
    </div>
  );
}
