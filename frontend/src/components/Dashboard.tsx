"use client";

import React, { useMemo, useRef, useState } from 'react';
import {
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
  AreaChart, Area, BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
} from 'recharts';
import ReactMarkdown from 'react-markdown';
import html2canvas from 'html2canvas';
import jsPDF from 'jspdf';
import {
  TrendingUp, TrendingDown, DollarSign, MousePointer2, Target, BarChart3,
  Sparkles, Download, Eye, Award, AlertTriangle, Activity, Percent,
} from 'lucide-react';
import { CampaignTable } from './CampaignTable';

// ── Constants ──────────────────────────────────────────────────────────────────
const PLATFORM_COLORS: Record<string, string> = {
  google:    '#3b82f6',
  meta:      '#818cf8',
  linkedin:  '#0a66c2',
  tiktok:    '#ff0050',
  microsoft: '#10b981',
};
const PLATFORMS = ['google', 'meta', 'linkedin', 'tiktok', 'microsoft'];
const PLATFORM_DISPLAY: Record<string, string> = {
  google:    'Google Ads',
  meta:      'Meta Ads',
  linkedin:  'LinkedIn',
  tiktok:    'TikTok',
  microsoft: 'Microsoft Ads',
};
const ChartTooltipStyle = {
  borderRadius: '16px', border: 'none',
  boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', backgroundColor: '#fff',
};

// ── Types ──────────────────────────────────────────────────────────────────────
interface Delta { value: string; direction: 'positive' | 'negative' | 'neutral'; }
interface PlatformDelta { spend?: Delta; conversions?: Delta; cpa?: Delta; ctr?: Delta; }
interface PerformerInfo {
  campaign: string; platform: string; cpa: number; spend: number; conversions: number;
}
interface CampaignSummaryRow {
  platform: string;
  campaign: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  cpa: number;
  ctr: number;
  cvr: number;
  cpc: number;
  spend_share: number;
}
interface PlatformSummaryRow {
  platform: string;
  spend: number;
  cpa: number;
  ctr: number;
  conversions: number;
}
interface HierarchySummaryRow {
  level: string;
  platform: string;
  name: string;
  spend: number;
  impressions: number;
  clicks: number;
  conversions: number;
  cpa: number;
  ctr: number;
  cvr: number;
  cpc: number;
  roas: number;
  spend_share: number;
}
interface HierarchySummary {
  campaign: HierarchySummaryRow[];
  adGroup: HierarchySummaryRow[];
  adAsset: HierarchySummaryRow[];
}
interface ChartDataPoint {
  date: string;
  [key: string]: string | number | null;
}
interface KpiCardProps {
  title: string;
  value: number | string | null | undefined;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  prefix?: string;
  suffix?: string;
  delta?: Delta;
  deltaLabel?: string;
}
interface DashboardProps {
  data: {
    chartData: ChartDataPoint[];
    scorecards: {
      totalSpend: number; totalImpressions: number; totalClicks: number;
      totalConversions: number; blendedCPA: number; blendedCTR: number;
      blendedCVR: number; blendedCPC: number; blendedCPM: number; blendedROAS: number | null;
    };
    scorecardDeltas: Record<string, Delta>;
    platformDeltas: Record<string, PlatformDelta>;
    comparisonType: string;
    currentPeriodLabel: string;
    priorPeriodLabel: string;
    campaignSummary: CampaignSummaryRow[];
    hierarchySummary: HierarchySummary;
    platformSummary: PlatformSummaryRow[];
    topPerformer: PerformerInfo | null;
    bottomPerformer: PerformerInfo | null;
    geminiAnalysis: string;
  };
}

// ── Sub-components ─────────────────────────────────────────────────────────────
const DeltaBadge = ({ delta, label }: { delta?: Delta; label?: string }) => {
  if (!delta || delta.value === 'N/A') return null;
  const pos = delta.direction === 'positive';
  return (
    <span className={`text-[10px] font-bold flex items-center gap-0.5 ${pos ? 'text-emerald-600' : 'text-red-500'}`}>
      {pos ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
      {delta.value}{label ? ` ${label}` : ''}
    </span>
  );
};

const KpiCard = ({ title, value, icon: Icon, prefix = "", suffix = "", delta, deltaLabel }: KpiCardProps) => (
  <div className="bg-white p-5 rounded-3xl border border-slate-100 shadow-sm hover:shadow-md transition-shadow">
    <div className="flex items-start justify-between mb-3">
      <div className="p-2.5 bg-blue-50 rounded-2xl text-blue-600"><Icon size={20} /></div>
      <DeltaBadge delta={delta} label={deltaLabel} />
    </div>
    <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">{title}</p>
    <h4 className="text-xl font-black text-slate-900 mt-1 tabular-nums">
      {prefix}{typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : (value ?? '—')}{suffix}
    </h4>
  </div>
);

const SectionHeader = ({ icon: Icon, title }: { icon: React.ComponentType<{ size?: number; className?: string }>; title: string }) => (
  <div className="flex items-center gap-3 mb-5">
    <Icon className="text-blue-600" size={20} />
    <h3 className="text-lg font-bold text-slate-800">{title}</h3>
  </div>
);

const PerformerCard = ({ performer, type }: { performer: PerformerInfo; type: 'top' | 'bottom' }) => {
  const isTop = type === 'top';
  return (
    <div className={`bg-white p-6 rounded-[2rem] border shadow-sm ${isTop ? 'border-emerald-100' : 'border-red-100'}`}>
      <div className="flex items-center gap-3 mb-4">
        <div className={`p-2 rounded-xl ${isTop ? 'bg-emerald-50' : 'bg-red-50'}`}>
          {isTop ? <Award size={18} className="text-emerald-600" /> : <AlertTriangle size={18} className="text-red-500" />}
        </div>
        <h4 className={`text-sm font-black uppercase tracking-wider ${isTop ? 'text-emerald-700' : 'text-red-600'}`}>
          {isTop ? 'Top Performer' : 'Needs Attention'}
        </h4>
      </div>
      <p className="text-base font-bold text-slate-800 truncate" title={performer.campaign}>
        {performer.campaign}
      </p>
      <span className={`mt-1 inline-block px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider
        ${performer.platform === 'google'    ? 'bg-blue-100 text-blue-700' :
          performer.platform === 'meta'     ? 'bg-indigo-100 text-indigo-700' :
          performer.platform === 'linkedin' ? 'bg-sky-100 text-sky-700' :
                                              'bg-pink-100 text-pink-700'}`}>
        {performer.platform}
      </span>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {[
          { label: 'CPA',   value: `$${performer.cpa.toFixed(2)}` },
          { label: 'Spend', value: `$${performer.spend.toLocaleString()}` },
          { label: 'Conv.', value: performer.conversions.toString() },
        ].map(({ label, value }) => (
          <div key={label} className="bg-slate-50 rounded-xl p-2 text-center">
            <p className="text-[10px] font-bold text-slate-400 uppercase">{label}</p>
            <p className="text-sm font-black text-slate-800">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
};

const PlatformCard = ({ plat, deltas, deltaLabel }: { plat: PlatformSummaryRow; deltas: PlatformDelta; deltaLabel: string }) => {
  const color = PLATFORM_COLORS[plat.platform] || '#94a3b8';
  return (
    <div className="bg-white p-6 rounded-[2rem] border border-slate-100 shadow-sm">
      <div className="flex items-center gap-2 mb-4">
        <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
        <h4 className="text-sm font-black text-slate-700">{PLATFORM_DISPLAY[plat.platform] || plat.platform}</h4>
      </div>
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: 'Spend', value: `$${(plat.spend ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, delta: deltas.spend },
          { label: 'CPA',   value: `$${(plat.cpa ?? 0).toFixed(2)}`,                                               delta: deltas.cpa },
          { label: 'CTR',   value: `${(plat.ctr ?? 0).toFixed(2)}%`,                                               delta: deltas.ctr },
          { label: 'Conv.', value: String(plat.conversions ?? 0),                                                   delta: deltas.conversions },
        ].map(({ label, value, delta }) => (
          <div key={label} className="bg-slate-50 rounded-xl p-3">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wide mb-1">{label}</p>
            <p className="text-sm font-black text-slate-800">{value}</p>
            <DeltaBadge delta={delta} label={deltaLabel} />
          </div>
        ))}
      </div>
    </div>
  );
};

export const Dashboard: React.FC<DashboardProps> = ({ data }) => {
  const {
    chartData, scorecards, scorecardDeltas = {},
    platformDeltas = {}, comparisonType = 'none',
    currentPeriodLabel = '', priorPeriodLabel = '',
    campaignSummary, hierarchySummary = { campaign: [], adGroup: [], adAsset: [] }, platformSummary = [],
    topPerformer, bottomPerformer, geminiAnalysis,
  } = data;

  const dashboardRef = useRef<HTMLDivElement>(null);
  const [viewMode, setViewMode] = useState<'analyst' | 'exec'>('analyst');
  const [hierarchyLevel, setHierarchyLevel] = useState<'campaign' | 'adGroup' | 'adAsset'>('campaign');
  const deltaLabel =
    comparisonType === 'year_over_year'     ? 'YoY' :
    comparisonType === 'period_over_period' ? 'PoP' :
    comparisonType === 'manual_comparison'  ? 'vs Prior' : '';

  const executiveTakeaways = useMemo(() => {
    const parseDelta = (raw?: string) => {
      if (!raw) return null;
      const m = raw.match(/-?\d+(?:\.\d+)?/);
      return m ? Math.abs(Number(m[0])) : null;
    };

    const labels: Record<string, string> = {
      spend: 'Spend',
      impressions: 'Impressions',
      clicks: 'Clicks',
      conversions: 'Conversions',
      blendedCPA: 'Blended CPA',
      blendedCTR: 'Blended CTR',
    };

    const entries = Object.entries(scorecardDeltas)
      .map(([key, d]) => ({ key, delta: d, magnitude: parseDelta(d?.value) ?? -1 }))
      .filter((d) => d.delta && d.delta.value !== 'N/A');

    const best = entries
      .filter((d) => d.delta.direction === 'positive')
      .sort((a, b) => b.magnitude - a.magnitude)[0];
    const risk = entries
      .filter((d) => d.delta.direction === 'negative')
      .sort((a, b) => b.magnitude - a.magnitude)[0];

    const cpaPlatforms = [...platformSummary]
      .filter((p) => p.conversions > 0)
      .sort((a, b) => a.cpa - b.cpa);
    const bestPlat = cpaPlatforms[0];
    const worstPlat = cpaPlatforms[cpaPlatforms.length - 1];

    const recommendation = bestPlat && worstPlat
      ? `Consider shifting 10-15% budget from ${PLATFORM_DISPLAY[worstPlat.platform] || worstPlat.platform} to ${PLATFORM_DISPLAY[bestPlat.platform] || bestPlat.platform} based on CPA gap (${worstPlat.cpa.toFixed(2)} vs ${bestPlat.cpa.toFixed(2)}).`
      : 'Insufficient platform conversion volume for a reliable reallocation recommendation.';

    return {
      best,
      risk,
      recommendation,
      labels,
    };
  }, [scorecardDeltas, platformSummary]);


  const exportToPDF = async () => {
    if (!dashboardRef.current) return;
    const dashboard = dashboardRef.current;
    try {
      const canvas = await html2canvas(dashboard, {
        scale: 2, useCORS: true, logging: false,
        backgroundColor: '#fcfcfd',
        windowWidth: dashboard.scrollWidth,
        windowHeight: dashboard.scrollHeight,
      });
      const imgData = canvas.toDataURL('image/png');
      const pdf = new jsPDF({ orientation: 'landscape', unit: 'px', format: [canvas.width, canvas.height] });
      pdf.addImage(imgData, 'PNG', 0, 0, canvas.width, canvas.height);
      pdf.save('antigravity-report.pdf');
    } catch (error) {
      console.error('PDF Export Error:', error);
    }
  };

  const activePlatforms = PLATFORMS.filter(p =>
    platformSummary.some((ps) => ps.platform === p)
  );

  const efficiencyData = [...platformSummary]
    .filter((p) => p.conversions > 0)
    .sort((a, b) => a.cpa - b.cpa)
    .map((p) => ({
      platform: PLATFORM_DISPLAY[p.platform] || p.platform,
      cpa: Number(p.cpa.toFixed(2)),
      fill: PLATFORM_COLORS[p.platform] || '#94a3b8',
    }));

  const spendShareData = platformSummary
    .filter((p) => p.spend > 0)
    .map((p) => ({
      name: PLATFORM_DISPLAY[p.platform] || p.platform,
      value: p.spend,
      fill: PLATFORM_COLORS[p.platform] || '#94a3b8',
    }));

  const campaignFallbackHierarchy: HierarchySummaryRow[] = campaignSummary.map((r) => ({
    level: 'campaign',
    platform: r.platform,
    name: r.campaign,
    spend: r.spend,
    impressions: r.impressions,
    clicks: r.clicks,
    conversions: r.conversions,
    cpa: r.cpa,
    ctr: r.ctr,
    cvr: r.cvr,
    cpc: r.cpc,
    roas: 0,
    spend_share: r.spend_share,
  }));

  const activeHierarchyRows = hierarchyLevel === 'campaign'
    ? (hierarchySummary.campaign.length > 0 ? hierarchySummary.campaign : campaignFallbackHierarchy)
    : (hierarchySummary[hierarchyLevel] ?? []);
  const hierarchyByPlatform = activeHierarchyRows.reduce<Record<string, HierarchySummaryRow[]>>((acc, row) => {
    if (!acc[row.platform]) acc[row.platform] = [];
    acc[row.platform].push(row);
    return acc;
  }, {});

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex flex-col md:flex-row md:items-center justify-between mb-6 gap-3">
        <div className="inline-flex bg-white border border-slate-200 rounded-xl p-1 shadow-sm">
          <button
            onClick={() => setViewMode('exec')}
            className={`h-9 px-4 rounded-lg text-xs font-black uppercase tracking-wider transition-colors ${
              viewMode === 'exec' ? 'bg-blue-600 text-white' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Exec View
          </button>
          <button
            onClick={() => setViewMode('analyst')}
            className={`h-9 px-4 rounded-lg text-xs font-black uppercase tracking-wider transition-colors ${
              viewMode === 'analyst' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Analyst View
          </button>
        </div>

        <button
          onClick={exportToPDF}
          className="px-6 h-12 bg-white border border-slate-200 rounded-xl font-bold flex items-center gap-2 hover:bg-slate-50 transition-all text-slate-700 shadow-sm group"
        >
          <Download size={18} className="group-hover:translate-y-0.5 transition-transform" />
          Export PDF
        </button>
      </div>

      <div ref={dashboardRef} className="p-8 bg-[#fcfcfd] rounded-[3rem] space-y-8">

        {/* ── Comparison Banner ────────────────────────────────────────────── */}
        {comparisonType !== 'none' && comparisonType !== '' && (currentPeriodLabel || priorPeriodLabel) && (
          <div className="flex items-center gap-3 p-4 bg-blue-50 border border-blue-100 rounded-2xl">
            <span className={`px-3 py-1 rounded-full text-xs font-black uppercase tracking-wider text-white ${
              comparisonType === 'year_over_year'     ? 'bg-blue-600' :
              comparisonType === 'period_over_period' ? 'bg-violet-600' : 'bg-slate-600'
            }`}>
              {deltaLabel}
            </span>
            <span className="text-sm font-medium text-slate-700">
              <span className="font-bold">{currentPeriodLabel}</span>
              <span className="text-slate-400 mx-2">vs</span>
              <span className="font-bold">{priorPeriodLabel}</span>
            </span>
          </div>
        )}

        {/* ── Executive Strip ─────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-gradient-to-br from-emerald-50 to-teal-50 border border-emerald-100 rounded-2xl p-4">
            <p className="text-[10px] font-black uppercase tracking-widest text-emerald-700 mb-2">Biggest Win</p>
            {executiveTakeaways.best ? (
              <p className="text-sm font-bold text-slate-800">
                {executiveTakeaways.labels[executiveTakeaways.best.key] ?? executiveTakeaways.best.key}: {executiveTakeaways.best.delta.value}
              </p>
            ) : (
              <p className="text-sm font-medium text-slate-500">No statistically meaningful positive shifts yet.</p>
            )}
          </div>

          <div className="bg-gradient-to-br from-rose-50 to-red-50 border border-rose-100 rounded-2xl p-4">
            <p className="text-[10px] font-black uppercase tracking-widest text-rose-700 mb-2">Biggest Risk</p>
            {executiveTakeaways.risk ? (
              <p className="text-sm font-bold text-slate-800">
                {executiveTakeaways.labels[executiveTakeaways.risk.key] ?? executiveTakeaways.risk.key}: {executiveTakeaways.risk.delta.value}
              </p>
            ) : (
              <p className="text-sm font-medium text-slate-500">No material declines detected in reliable metrics.</p>
            )}
          </div>

          <div className="bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-100 rounded-2xl p-4">
            <p className="text-[10px] font-black uppercase tracking-widest text-blue-700 mb-2">Reallocation Signal</p>
            <p className="text-sm font-medium text-slate-700">{executiveTakeaways.recommendation}</p>
          </div>
        </div>

        {/* ── KPI Cards (2 rows of 4) ──────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <KpiCard title="Total Spend"   value={scorecards.totalSpend}       icon={DollarSign}    prefix="$" delta={scorecardDeltas.spend}       deltaLabel={deltaLabel} />
          <KpiCard title="Impressions"   value={scorecards.totalImpressions} icon={Eye}                      delta={scorecardDeltas.impressions} deltaLabel={deltaLabel} />
          <KpiCard title="Clicks"        value={scorecards.totalClicks}      icon={MousePointer2}            delta={scorecardDeltas.clicks}      deltaLabel={deltaLabel} />
          <KpiCard title="Conversions"   value={scorecards.totalConversions} icon={Target}                   delta={scorecardDeltas.conversions} deltaLabel={deltaLabel} />
          <KpiCard title="Blended CPA"   value={scorecards.blendedCPA}       icon={TrendingUp}    prefix="$" delta={scorecardDeltas.blendedCPA}  deltaLabel={deltaLabel} />
          <KpiCard title="Blended CTR"   value={scorecards.blendedCTR}       icon={Percent}       suffix="%" delta={scorecardDeltas.blendedCTR}  deltaLabel={deltaLabel} />
          <KpiCard title="Blended CVR"   value={scorecards.blendedCVR}       icon={Activity}      suffix="%" />
          <KpiCard title="Blended CPC"   value={scorecards.blendedCPC}       icon={BarChart3}     prefix="$" />
        </div>

        {/* ── Per-Platform Performance ─────────────────────────────────────── */}
        {platformSummary.length > 0 && (
          <div>
            <SectionHeader icon={BarChart3} title="Per-Platform Performance" />
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {platformSummary.map((plat) => (
                <PlatformCard
                  key={plat.platform}
                  plat={plat}
                  deltas={platformDeltas[plat.platform] ?? {}}
                  deltaLabel={deltaLabel}
                />
              ))}
            </div>
          </div>
        )}

        {/* ── Hierarchy Summaries ─────────────────────────────────────────── */}
        <div>
          <SectionHeader icon={BarChart3} title="Hierarchy Summaries by Platform" />
          <div className="inline-flex bg-white border border-slate-200 rounded-xl p-1 shadow-sm mb-4">
            {[
              { key: 'campaign', label: 'Campaign' },
              { key: 'adGroup', label: 'Ad Set / Ad Group' },
              { key: 'adAsset', label: 'Ad / Asset' },
            ].map((opt) => (
              <button
                key={opt.key}
                onClick={() => setHierarchyLevel(opt.key as 'campaign' | 'adGroup' | 'adAsset')}
                className={`h-8 px-3 rounded-lg text-[11px] font-black uppercase tracking-wider transition-colors ${
                  hierarchyLevel === opt.key ? 'bg-blue-600 text-white' : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {activeHierarchyRows.length === 0 ? (
            <div className="bg-white border border-slate-100 rounded-2xl p-5 text-sm text-slate-500">
              No data available for this hierarchy level.
            </div>
          ) : (
            <div className="space-y-4">
              {Object.entries(hierarchyByPlatform)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([platform, rows]) => (
                  <div key={platform} className="bg-white border border-slate-100 rounded-2xl p-5 shadow-sm">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="text-sm font-black text-slate-700">{PLATFORM_DISPLAY[platform] || platform}</h4>
                      <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">{rows.length} items</span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="text-slate-400 uppercase tracking-wider">
                            <th className="pb-2 pr-3">Name</th>
                            <th className="pb-2 pr-3 text-right">Spend</th>
                            <th className="pb-2 pr-3 text-right">Conv.</th>
                            <th className="pb-2 pr-3 text-right">CPA</th>
                            <th className="pb-2 pr-3 text-right">CTR</th>
                            <th className="pb-2 text-right">% Spend</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...rows]
                            .sort((a, b) => b.spend - a.spend)
                            .slice(0, 12)
                            .map((r, idx) => (
                              <tr key={`${platform}-${idx}`} className="border-t border-slate-50">
                                <td className="py-2 pr-3 font-medium text-slate-700">{r.name || 'Unnamed'}</td>
                                <td className="py-2 pr-3 text-right tabular-nums">${r.spend.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                                <td className="py-2 pr-3 text-right tabular-nums">{r.conversions.toLocaleString()}</td>
                                <td className="py-2 pr-3 text-right tabular-nums">${r.cpa.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                                <td className="py-2 pr-3 text-right tabular-nums">{r.ctr.toLocaleString(undefined, { maximumFractionDigits: 2 })}%</td>
                                <td className="py-2 text-right tabular-nums">{r.spend_share.toLocaleString(undefined, { maximumFractionDigits: 2 })}%</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* ── Top / Bottom Performers ──────────────────────────────────────── */}
        {(topPerformer || bottomPerformer) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {topPerformer    && <PerformerCard performer={topPerformer}    type="top" />}
            {bottomPerformer && <PerformerCard performer={bottomPerformer} type="bottom" />}
          </div>
        )}

        {/* ── Row 1: Spend over Time + Spend Share Donut ──────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-white p-8 rounded-[2rem] border border-slate-100 shadow-sm">
            <SectionHeader icon={BarChart3} title="Spend by Platform over Time" />
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    {PLATFORMS.map(p => (
                      <linearGradient key={p} id={`grad_${p}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={PLATFORM_COLORS[p]} stopOpacity={0.15} />
                        <stop offset="95%" stopColor={PLATFORM_COLORS[p]} stopOpacity={0} />
                      </linearGradient>
                    ))}
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                  <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} dy={10} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip contentStyle={ChartTooltipStyle} formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, undefined]} />
                  <Legend verticalAlign="top" height={36} />
                  {activePlatforms.map(p => (
                    <Area key={p} type="monotone" dataKey={`${p}_spend`}
                      stroke={PLATFORM_COLORS[p]} strokeWidth={2.5}
                      fillOpacity={1} fill={`url(#grad_${p})`} name={PLATFORM_DISPLAY[p]} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-white p-8 rounded-[2rem] border border-slate-100 shadow-sm">
            <SectionHeader icon={BarChart3} title="Spend Share" />
            <div className="h-[210px]">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={spendShareData} cx="50%" cy="50%" innerRadius={60} outerRadius={90}
                    dataKey="value" paddingAngle={3}>
                    {spendShareData.map((_: unknown, idx: number) => (
                      <Cell key={idx} fill={spendShareData[idx].fill} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={ChartTooltipStyle}
                    formatter={(v: unknown) => [`$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, undefined]} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 space-y-1.5">
              {spendShareData.map((entry) => (
                <div key={entry.name} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: entry.fill }} />
                    <span className="font-medium text-slate-600">{entry.name}</span>
                  </div>
                  <span className="font-bold text-slate-800 tabular-nums">
                    ${entry.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Row 2: CPA over Time + Platform Efficiency Bar ──────────────── */}
        {viewMode === 'analyst' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-white p-8 rounded-[2rem] border border-slate-100 shadow-sm">
            <SectionHeader icon={TrendingUp} title="CPA by Platform over Time" />
            <div className="h-[300px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                  <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} dy={10} />
                  <YAxis axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                  <Tooltip contentStyle={ChartTooltipStyle} formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, undefined]} />
                  <Legend verticalAlign="top" height={36} />
                  {activePlatforms.map(p => (
                    <Line key={p} type="monotone" dataKey={`${p}_cpa`}
                      stroke={PLATFORM_COLORS[p]} strokeWidth={2.5}
                      dot={false} name={PLATFORM_DISPLAY[p]} connectNulls />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="bg-white p-8 rounded-[2rem] border border-slate-100 shadow-sm">
            <SectionHeader icon={BarChart3} title="Platform CPA Ranking" />
            <p className="text-xs text-slate-400 font-medium -mt-4 mb-5">Lower = more efficient</p>
            {efficiencyData.length > 0 ? (
              <div className="h-[260px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={efficiencyData} layout="vertical" margin={{ left: 0, right: 16 }}>
                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#f1f5f9" />
                    <XAxis type="number" axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} tickFormatter={(v) => `$${v}`} />
                    <YAxis type="category" dataKey="platform" axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} width={84} />
                    <Tooltip contentStyle={ChartTooltipStyle} formatter={(v: unknown) => [`$${Number(v).toFixed(2)} CPA`, undefined]} />
                    <Bar dataKey="cpa" radius={[0, 8, 8, 0]}>
                      {efficiencyData.map((_: unknown, idx: number) => (
                        <Cell key={idx} fill={efficiencyData[idx].fill} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="text-sm text-slate-400">No conversion data available for ranking.</p>
            )}
          </div>
        </div>
        )}

        {/* ── Campaign Table ───────────────────────────────────────────────── */}
        {viewMode === 'analyst' && (
          <CampaignTable data={campaignSummary} blendedCPA={scorecards.blendedCPA} />
        )}

        {/* ── Gemini Analysis ──────────────────────────────────────────────── */}
        <div className="bg-slate-900 text-white rounded-[2rem] p-8 shadow-2xl relative overflow-hidden">
          <div className="absolute top-0 right-0 p-8 opacity-10 pointer-events-none">
            <Sparkles size={120} />
          </div>
          <div className="relative z-10">
            <div className="flex items-center gap-3 mb-5">
              <div className="p-2 bg-blue-500 rounded-lg">
                <Sparkles size={18} className="text-white" />
              </div>
              <h3 className="text-xl font-bold">Gemini Insights</h3>
            </div>

            {/* Key Signals strip */}
            {Object.keys(scorecardDeltas).length > 0 && (
              <div className="flex flex-wrap gap-2 mb-6 pb-5 border-b border-slate-700">
                <span className="text-xs font-bold text-slate-400 uppercase tracking-widest self-center mr-1">Key Signals:</span>
                {(['spend', 'conversions', 'blendedCPA', 'blendedCTR'] as const)
                  .filter(key => scorecardDeltas[key] && scorecardDeltas[key].value !== 'N/A')
                  .map(key => {
                    const d = scorecardDeltas[key];
                    const labels: Record<string, string> = { spend: 'Spend', conversions: 'Conv.', blendedCPA: 'CPA', blendedCTR: 'CTR' };
                    return (
                      <span key={key} className={`px-3 py-1.5 rounded-full text-xs font-bold flex items-center gap-1 ${
                        d.direction === 'positive' ? 'bg-emerald-900/60 text-emerald-300' : 'bg-red-900/60 text-red-300'
                      }`}>
                        {d.direction === 'positive' ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                        {labels[key]}: {d.value}
                      </span>
                    );
                  })}
              </div>
            )}

            <div className="prose prose-invert prose-slate max-w-none
              prose-headings:text-white prose-headings:font-bold prose-headings:mt-6 prose-headings:mb-2
              prose-h2:text-base prose-h2:border-b prose-h2:border-slate-700 prose-h2:pb-2
              prose-strong:text-blue-300
              prose-p:text-slate-300 prose-p:leading-relaxed
              prose-li:text-slate-300">
              <ReactMarkdown>{geminiAnalysis}</ReactMarkdown>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};
