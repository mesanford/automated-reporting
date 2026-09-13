"use client";

/**
 * The printable report layout, shared by the authenticated route
 * (`/reports/[id]/print`) and the public share route (`/share/[token]/print`).
 *
 * It is deliberately presentational: it takes an already-fetched report and
 * knows nothing about auth or workspaces. That is what lets a share recipient,
 * who has no session and no workspace header, get byte-identical output to the
 * operator previewing it — which is the whole point of a client deliverable.
 */

import React from 'react';
import ReactMarkdown from 'react-markdown';
import { AlertTriangle } from 'lucide-react';

export interface PrintableReport {
  id: number;
  created_at: string | null;
  currentPeriodLabel: string | null;
  priorPeriodLabel: string | null;
  comparisonType?: string | null;
  scorecards: Record<string, number | null>;
  scorecardDeltas?: Record<string, { value?: string; direction?: string; confidence?: string }>;
  platformSummary: Array<Record<string, unknown>>;
  campaignSummary: Array<Record<string, unknown>>;
  geminiAnalysis: string | null;
  usedMockData?: boolean;
}

const METRIC_ROWS: Array<[string, string, string]> = [
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

export interface ReportPrintViewProps {
  report: PrintableReport;
  /** ISO currency code used to label money figures. */
  currency?: string;
  /** Shown on the cover. Omitted for recipients who shouldn't see it. */
  workspaceName?: string | null;
  /** Extra line under the cover, e.g. share-link expiry. */
  coverNote?: React.ReactNode;
  /** Fire the browser print dialog once the report has rendered. */
  autoPrint?: boolean;
}

export default function ReportPrintView({
  report,
  currency = 'USD',
  workspaceName,
  coverNote,
  autoPrint = false,
}: ReportPrintViewProps) {
  React.useEffect(() => {
    if (!autoPrint) return;
    // One frame after paint, so tables and markdown have laid out before the
    // dialog snapshots the page.
    const id = window.requestAnimationFrame(() => window.print());
    return () => window.cancelAnimationFrame(id);
  }, [autoPrint]);

  const fmt = (v: number | null | undefined, unit: string) => {
    if (v == null) return '—';
    if (unit === '$') return `${currency} ${v.toLocaleString()}`;
    if (unit === '%') return `${v}%`;
    return v.toLocaleString();
  };

  return (
    <div className="bg-white min-h-screen">
      <main className="max-w-4xl mx-auto px-6 py-10">
        <div className="no-print mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-black text-slate-900">
            {report.currentPeriodLabel || `Report #${report.id}`}
          </h1>
          <button
            onClick={() => window.print()}
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
          {workspaceName && (
            <p className="text-xs text-slate-400">Workspace: {workspaceName} ({currency})</p>
          )}
          {coverNote && <p className="text-xs text-slate-400">{coverNote}</p>}
        </section>

        {report.usedMockData && (
          <section className="mb-8 flex items-start gap-3 p-4 bg-amber-50 border border-amber-200 rounded-2xl print:border-2 print:border-amber-500">
            <AlertTriangle size={20} className="text-amber-600 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-amber-900">
              <span className="font-black uppercase tracking-wide">Includes fabricated demo data.</span>{' '}
              At least one connected channel in this report has no live API integration yet
              (Facebook Organic, Instagram Organic, or LinkedIn Organic), so its numbers are
              randomly generated sample data, not real performance figures. All other channels
              in this report are real.
            </p>
          </section>
        )}

        {/* Headline metrics */}
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
                const fallbackKey = key.replace('total', '').toLowerCase();
                const d = report.scorecardDeltas?.[key] ?? report.scorecardDeltas?.[fallbackKey];
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

        {/* By platform */}
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

        {/* Campaigns */}
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

        {/* Analysis */}
        {report.geminiAnalysis && (
          <section className="mb-8 page-break-before">
            <h3 className="text-lg font-black text-slate-900 mb-3">Analysis</h3>
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown>{report.geminiAnalysis}</ReactMarkdown>
            </div>
          </section>
        )}

        <style jsx global>{`
          @media print {
            .no-print { display: none !important; }
            @page { margin: 1.5cm; }
            body { background: white !important; }
            .break-inside-avoid { break-inside: avoid; }
            .page-break-before { page-break-before: always; }
          }
        `}</style>
      </main>
    </div>
  );
}
