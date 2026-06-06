"use client";

import React, { useState, useEffect } from 'react';
import { UploadZone } from '@/components/UploadZone';
import { Dashboard } from '@/components/Dashboard';
import { ConnectionsManager } from '@/components/ConnectionsManager';
import Link from 'next/link';
import {
  ShieldCheck, Zap, ArrowLeft, Share2, FileText,
  History, Calendar, Layers, Globe, MessageSquare, LogOut, Settings, Activity as ActivityIcon
} from 'lucide-react';
import { useAuth } from '@/lib/auth';
import { WorkspaceSwitcher } from '@/components/WorkspaceSwitcher';
import { filterReportByPlatforms } from '@/lib/dashboard-filter';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

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

type DeltaDirection = 'positive' | 'negative' | 'neutral';

interface DeltaValue {
  value: string;
  direction: DeltaDirection;
}

interface PlatformDeltaValue {
  spend?: DeltaValue;
  conversions?: DeltaValue;
  cpa?: DeltaValue;
  ctr?: DeltaValue;
  blendedCPA?: DeltaValue;
  blendedCTR?: DeltaValue;
}

interface Scorecards {
  totalSpend: number;
  totalImpressions: number;
  totalClicks: number;
  totalConversions: number;
  blendedCPA: number;
  blendedCTR: number;
  blendedCVR: number;
  blendedCPC: number;
  blendedCPM: number;
  blendedROAS: number | null;
}

interface ChartDataPoint {
  date: string;
  [key: string]: string | number | null;
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
  revenue: number;
  roas: number;
}

interface PlatformSummaryRow {
  platform: string;
  spend: number;
  cpa: number;
  ctr: number;
  conversions: number;
  revenue: number;
  roas: number;
}

interface SavedView {
  id: number;
  name: string;
  visibility: 'private' | 'workspace';
  is_default: boolean;
  config: {
    platform_filter?: string[];
    [k: string]: unknown;
  };
}

interface DashboardBudget {
  id: number;
  name: string;
  scope_type: 'workspace' | 'platform' | 'connection';
  scope_key: string | null;
  is_active: boolean;
  pacing: {
    period_label: string;
    amount: number;
    spent: number;
    pct_used: number;
    status: 'on_pace' | 'under_pace' | 'over_pace' | 'exhausted';
  } | null;
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
  revenue: number;
  roas: number;
  spend_share: number;
}

interface HierarchySummary {
  campaign: HierarchySummaryRow[];
  adGroup: HierarchySummaryRow[];
  adAsset: HierarchySummaryRow[];
}

const normalizeHierarchySummary = (
  source: HierarchySummary | undefined,
  campaignRows: CampaignSummaryRow[],
): HierarchySummary => {
  const campaignFallback: HierarchySummaryRow[] = campaignRows.map((r) => ({
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
    revenue: r.revenue ?? 0,
    roas: r.roas ?? 0,
    spend_share: r.spend_share,
  }));

  return {
    campaign: source?.campaign?.length ? source.campaign : campaignFallback,
    adGroup: source?.adGroup ?? [],
    adAsset: source?.adAsset ?? [],
  };
};

interface PerformerInfo {
  campaign: string;
  platform: string;
  cpa: number;
  spend: number;
  conversions: number;
}

interface DashboardData {
  id?: number;
  chartData: ChartDataPoint[];
  scorecards: Scorecards;
  scorecardDeltas: Record<string, DeltaValue>;
  platformDeltas: Record<string, PlatformDeltaValue>;
  comparisonType: string;
  currentPeriodLabel: string;
  priorPeriodLabel: string;
  campaignSummary: CampaignSummaryRow[];
  hierarchySummary: HierarchySummary;
  platformSummary: PlatformSummaryRow[];
  topPerformer: PerformerInfo | null;
  bottomPerformer: PerformerInfo | null;
  geminiAnalysis: string;
}

interface ApiHistoryReport {
  id: number;
  created_at: string;
  chart_data: ChartDataPoint[];
  scorecards: Scorecards;
  scorecard_deltas?: Record<string, DeltaValue>;
  platform_deltas?: Record<string, PlatformDeltaValue>;
  comparison_type?: string;
  current_period_label?: string;
  prior_period_label?: string;
  campaign_summary: CampaignSummaryRow[];
  hierarchy_summary?: HierarchySummary;
  platform_summary?: PlatformSummaryRow[];
  top_performer?: PerformerInfo | null;
  bottom_performer?: PerformerInfo | null;
  gemini_analysis: string;
}

export default function Home() {
  const { user, signOut } = useAuth();
  const [reportData, setReportData] = useState<DashboardData | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStep, setUploadStep] = useState<string>('');
  const [history, setHistory] = useState<ApiHistoryReport[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [activeTab, setActiveTab] = useState<'upload' | 'accounts'>('upload');

  const [budgets, setBudgets] = useState<DashboardBudget[]>([]);
  const [customKpis, setCustomKpis] = useState<import('@/components/Dashboard').KpiCustomEntry[]>([]);

  // Saved views + platform filter. The filter is the only knob right now,
  // but the SavedView config is JSON, so future knobs (KPI focus,
  // hierarchy default, etc.) are additive.
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [activeViewId, setActiveViewId] = useState<number | null>(null);
  const [platformFilter, setPlatformFilter] = useState<string[]>([]);

  useEffect(() => {
    fetchHistory();
    fetchBudgets();
    fetchSavedViews();
  }, []);

  const fetchSavedViews = async () => {
    try {
      const wsId = typeof window !== 'undefined'
        ? Number(window.localStorage.getItem('antigravity:active_workspace_id') || 0)
        : 0;
      if (!wsId) return;
      const { apiJson } = await import('@/lib/api');
      const rows = await apiJson<SavedView[]>(`/api/workspaces/${wsId}/views`);
      setSavedViews(rows);
      // Auto-apply the user's default view, if any.
      const def = rows.find((v) => v.is_default);
      if (def) {
        setActiveViewId(def.id);
        setPlatformFilter(def.config?.platform_filter ?? []);
      }
    } catch {
      // Silent — dashboard works fine without saved views.
    }
  };

  // When a report is loaded, fetch the workspace's custom KPIs evaluated
  // against this specific report. Failure is silent — built-in scorecards
  // still render either way.
  useEffect(() => {
    const reportId = reportData?.id;
    if (!reportId) {
      setCustomKpis([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { apiJson } = await import('@/lib/api');
        const rows = await apiJson<import('@/components/Dashboard').KpiCustomEntry[]>(
          `/api/reports/${reportId}/kpis`,
        );
        if (!cancelled) setCustomKpis(rows);
      } catch {
        if (!cancelled) setCustomKpis([]);
      }
    })();
    return () => { cancelled = true; };
  }, [reportData?.id]);

  const fetchHistory = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/reports`);
      const data = await parseApiResponse(response);
      setHistory(Array.isArray(data) ? (data as ApiHistoryReport[]) : []);
    } catch (error) {
      console.error('Error fetching history:', error);
    }
  };

  const fetchBudgets = async () => {
    // Use the workspace-scoped budgets endpoint. The workspace is implicit
    // via the X-Workspace-Id header that lib/api.ts attaches; we can grab
    // the active id from localStorage to avoid an extra fetch.
    try {
      const wsId = typeof window !== 'undefined'
        ? Number(window.localStorage.getItem('antigravity:active_workspace_id') || 0)
        : 0;
      if (!wsId) return;
      const { apiJson } = await import('@/lib/api');
      const rows = await apiJson<DashboardBudget[]>(`/api/workspaces/${wsId}/budgets`);
      setBudgets(rows.filter((b) => b.is_active && b.pacing));
    } catch {
      // Silent — banner just doesn't render if anything goes sideways.
    }
  };

  const handleUpload = async (currentFiles: File[], comparisonFiles: File[]) => {
    setIsUploading(true);
    setUploadStep('Reading files...');

    const formData = new FormData();
    currentFiles.forEach(file => formData.append('files', file));
    comparisonFiles.forEach(file => formData.append('comparison_files', file));

    try {
      setUploadStep('Normalizing data...');
      const response = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        body: formData,
      });

      setUploadStep('Gemini analysis...');
      const result = await parseApiResponse(response) as {
        status?: string;
        message?: string;
        hierarchySummary?: HierarchySummary;
        campaignSummary?: CampaignSummaryRow[];
      } & DashboardData;
      
      if (result.status === 'success') {
        setUploadStep('Complete!');
        setReportData({
          ...result,
          hierarchySummary: normalizeHierarchySummary(result.hierarchySummary, result.campaignSummary ?? []),
        });
        fetchHistory(); // Refresh history
      } else {
        alert(result.message || 'Upload failed');
      }
    } catch (error) {
      console.error('Error uploading:', error);
      alert('Could not connect to the backend server. Make sure it is running on localhost:8000.');
    } finally {
      setIsUploading(false);
      setUploadStep('');
    }
  };

  const onSyncComplete = (result: unknown) => {
    const next = result as DashboardData;
    setReportData({
      ...next,
      hierarchySummary: normalizeHierarchySummary(next.hierarchySummary, next.campaignSummary ?? []),
    });
    fetchHistory();
  };

  const selectReport = (report: ApiHistoryReport) => {
    setReportData({
      id:              report.id,
      chartData:       report.chart_data,
      scorecards:      report.scorecards,
      scorecardDeltas: report.scorecard_deltas  ?? {},
      campaignSummary: report.campaign_summary,
      hierarchySummary: normalizeHierarchySummary(report.hierarchy_summary, report.campaign_summary),
      platformSummary: report.platform_summary  ?? [],
      topPerformer:    report.top_performer     ?? null,
      bottomPerformer: report.bottom_performer  ?? null,
      geminiAnalysis:  report.gemini_analysis,
    platformDeltas:      report.platform_deltas       ?? {},
    comparisonType:      report.comparison_type        ?? 'none',
    currentPeriodLabel:  report.current_period_label   ?? '',
    priorPeriodLabel:    report.prior_period_label     ?? '',
    });
    setShowHistory(false);
  };

  const reset = () => setReportData(null);

  const handleShareReport = async () => {
    if (!reportData?.id) {
      alert('This report needs to be saved before it can be shared.');
      return;
    }
    const wsId = typeof window !== 'undefined'
      ? Number(window.localStorage.getItem('antigravity:active_workspace_id') || 0)
      : 0;
    if (!wsId) {
      alert('No active workspace.');
      return;
    }
    try {
      const { apiJson } = await import('@/lib/api');
      const res = await apiJson<{ token: string; expires_at: string }>(
        `/api/workspaces/${wsId}/reports/${reportData.id}/share`,
        {
          method: 'POST',
          body: JSON.stringify({ expires_in_days: 30 }),
        },
      );
      const url = `${window.location.origin}/share/${res.token}`;
      try {
        await navigator.clipboard.writeText(url);
        alert(
          `Public link copied to clipboard. Expires ${new Date(res.expires_at).toLocaleDateString()}.\n\n${url}`,
        );
      } catch {
        prompt('Public link (copy manually):', url);
      }
    } catch (err) {
      alert(
        err instanceof Error
          ? `Failed to mint share link: ${err.message}`
          : 'Failed to mint share link.',
      );
    }
  };

  const handleDownloadFullReport = async () => {
    if (!reportData?.id) {
      alert('This report is missing an ID and cannot be downloaded yet.');
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/api/reports/${reportData.id}/markdown`);
      if (!response.ok) {
        throw new Error(`Download failed (${response.status})`);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `antigravity-report-${reportData.id}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Error downloading markdown report:', error);
      alert('Could not download report markdown.');
    }
  };

  return (
    <main className="min-h-screen flex flex-col selection:bg-blue-100 selection:text-blue-900" style={{backgroundColor: 'var(--background)', color: 'var(--foreground)'}}>
      {/* Premium Header */}
      <nav className="border-b border-slate-100 bg-background/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200 cursor-pointer" onClick={reset}>
              <Zap className="text-white w-6 h-6 fill-white" />
            </div>
            <h1 className="text-2xl font-black tracking-tight text-slate-900">
              MM Sanford Internal Reporting<span className="text-blue-600">.</span>
            </h1>
          </div>
          
          <div className="hidden md:flex items-center gap-8 font-semibold text-sm text-slate-500">
            <button
              onClick={() => setShowHistory(!showHistory)}
              className={`flex items-center gap-2 transition-colors ${showHistory ? 'text-blue-600' : 'hover:text-blue-600'}`}
            >
              <History size={18} />
              History
            </button>
            <Link
              href="/chat"
              className="flex items-center gap-2 transition-colors hover:text-blue-600"
            >
              <MessageSquare size={18} />
              Chat
            </Link>
            <Link
              href="/reports"
              className="flex items-center gap-2 transition-colors hover:text-blue-600"
            >
              <FileText size={18} />
              Reports
            </Link>
            <Link
              href="/activity"
              className="flex items-center gap-2 transition-colors hover:text-blue-600"
            >
              <ActivityIcon size={18} />
              Activity
            </Link>
            <Link
              href="/settings"
              className="flex items-center gap-2 transition-colors hover:text-blue-600"
            >
              <Settings size={18} />
              Settings
            </Link>
            <div className="h-4 w-px bg-slate-200"></div>
            <div className="flex items-center gap-2 text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-full">
              <ShieldCheck size={16} />
              <span>System Online</span>
            </div>
            <WorkspaceSwitcher />
            {user && (
              <button
                onClick={() => void signOut()}
                title={user.email ?? user.uid}
                className="flex items-center gap-2 transition-colors hover:text-blue-600"
              >
                <LogOut size={16} />
                <span className="hidden lg:inline truncate max-w-[140px]">{user.email ?? 'Sign out'}</span>
              </button>
            )}
          </div>
        </div>
      </nav>

      <div className="flex-1 flex relative overflow-hidden">
        {/* History Sidebar */}
        <aside className={`
          absolute lg:relative z-40 h-[calc(100vh-80px)] w-80 bg-background border-r border-slate-100 transition-all duration-500 ease-in-out
          ${showHistory ? 'translate-x-0 opacity-100' : '-translate-x-full lg:-ml-80 opacity-0'}
        `}>
          <div className="p-6 h-full flex flex-col">
            <h3 className="text-xs font-black text-slate-400 uppercase tracking-[0.2em] mb-6 px-2">Analysis History</h3>
            <div className="flex-1 overflow-y-auto space-y-4 pr-2 custom-scrollbar">
              {history.length === 0 ? (
                <div className="text-center py-12 px-4 border border-dashed border-slate-100 rounded-2xl">
                  <p className="text-xs font-medium text-slate-400">No reports generated yet</p>
                </div>
              ) : (
                history.map((report) => (
                  <button
                    key={report.id}
                    onClick={() => selectReport(report)}
                    className="w-full p-4 rounded-2xl border border-slate-50 hover:border-blue-100 hover:bg-blue-50/30 transition-all text-left group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2 text-blue-600">
                        <Calendar size={14} />
                        <span className="text-[10px] font-black uppercase tracking-widest">
                          {new Date(report.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <span className="text-[10px] font-bold text-slate-300">ID: {report.id}</span>
                    </div>
                    <p className="text-sm font-bold text-slate-700 line-clamp-1 group-hover:text-blue-700">
                      Report - {new Date(report.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </p>
                    <div className="mt-3 flex items-center gap-3">
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-100 text-slate-500 rounded-md">
                        ${report.scorecards.totalSpend.toLocaleString()}
                      </span>
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded-md">
                        {report.scorecards.totalConversions} Conv.
                      </span>
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>
        </aside>

        {/* Dashboard / Main Content */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-7xl mx-auto px-6 py-12">
            {!reportData ? (
              <div className="max-w-3xl mx-auto py-12 animate-in fade-in slide-in-from-top-4 duration-1000">
                {/* Onboarding card: shown when the workspace has no reports yet.
                    Dismissed via localStorage so returning users don't see it. */}
                <OnboardingCard
                  hasReports={history.length > 0}
                  onJumpToConnections={() => setActiveTab('accounts')}
                />

                <div className="text-center mb-12">
                  <div className="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 text-blue-600 rounded-full text-sm font-bold mb-6">
                    <Zap size={16} />
                    <span>AI-POWERED AD INTELLIGENCE</span>
                  </div>
                  <h2 className="text-5xl font-black text-slate-900 leading-[1.1] mb-6">
                    Turn raw ad data into <br />
                    <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600">
                      strategic gold.
                    </span>
                  </h2>
                  
                  {/* Tab Switcher */}
                  <div className="flex items-center justify-center gap-4 mt-8">
                    <button 
                      onClick={() => setActiveTab('upload')}
                      className={`h-12 px-8 rounded-2xl font-bold flex items-center gap-2 transition-all ${activeTab === 'upload' ? 'bg-background shadow-xl shadow-blue-100 text-blue-600' : 'text-slate-400 hover:text-slate-600'}`}
                    >
                      <Layers size={18} />
                      File Upload
                    </button>
                    <button 
                      onClick={() => setActiveTab('accounts')}
                      className={`h-12 px-8 rounded-2xl font-bold flex items-center gap-2 transition-all ${activeTab === 'accounts' ? 'bg-background shadow-xl shadow-blue-100 text-blue-600' : 'text-slate-400 hover:text-slate-600'}`}
                    >
                      <Globe size={18} />
                      Connected Accounts
                    </button>
                  </div>
                </div>

                <div className="mt-12">
                  {activeTab === 'upload' ? (
                    <UploadZone onUpload={handleUpload} isUploading={isUploading} uploadStep={uploadStep} />
                  ) : (
                    <ConnectionsManager 
                      onSyncComplete={onSyncComplete} 
                      isSyncing={isUploading} 
                      setIsSyncing={setIsUploading}
                      setUploadStep={setUploadStep}
                    />
                  )}
                </div>
                
                <div className="mt-12 grid grid-cols-3 gap-8 border-t border-slate-100 pt-12 text-center">
                  <div>
                    <p className="text-3xl font-black text-slate-900 mb-1">0.5s</p>
                    <p className="text-sm font-bold text-slate-400 uppercase tracking-widest">Normalization</p>
                  </div>
                  <div>
                    <p className="text-3xl font-black text-slate-900 mb-1">100%</p>
                    <p className="text-sm font-bold text-slate-400 uppercase tracking-widest">Cross-Channel</p>
                  </div>
                  <div>
                    <p className="text-3xl font-black text-slate-900 mb-1">Zero</p>
                    <p className="text-sm font-bold text-slate-400 uppercase tracking-widest">Manual Work</p>
                  </div>
                </div>
              </div>
            ) : (
              <div>
                <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-12">
                  <div>
                    <button 
                      onClick={reset}
                      className="flex items-center gap-2 text-slate-500 hover:text-blue-600 font-bold text-sm mb-4 transition-colors"
                    >
                      <ArrowLeft size={16} />
                      BACK TO UPLOAD
                    </button>
                    <h2 className="text-4xl font-black text-slate-900">Performance Snapshot</h2>
                    <p className="text-slate-500 font-medium mt-2">Analysis generated by Gemini 2.5 Pro</p>
                  </div>
                  
                  <div className="flex items-center gap-3">
                    <button
                      onClick={handleDownloadFullReport}
                      className="px-6 h-12 bg-background border border-slate-200 text-foreground rounded-xl font-bold flex items-center gap-2 hover:opacity-90 shadow-sm transition-all"
                    >
                      Download Full Report (.md)
                    </button>
                    <button
                      onClick={handleShareReport}
                      className="px-6 h-12 bg-blue-600 text-white rounded-xl font-bold flex items-center gap-2 hover:bg-blue-700 shadow-lg shadow-blue-200 transition-all"
                    >
                      <Share2 size={16} />
                      Share Report
                    </button>
                  </div>
                </div>

                <BudgetBanner budgets={budgets} />

                <ViewToolbar
                  views={savedViews}
                  activeViewId={activeViewId}
                  onSelectView={(id) => {
                    setActiveViewId(id);
                    const v = savedViews.find((sv) => sv.id === id);
                    setPlatformFilter(v?.config?.platform_filter ?? []);
                  }}
                  platformFilter={platformFilter}
                  availablePlatforms={(reportData?.platformSummary ?? []).map(
                    (p) => p.platform,
                  )}
                  onTogglePlatform={(p) =>
                    setPlatformFilter((prev) =>
                      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
                    )
                  }
                  onClearFilter={() => {
                    setPlatformFilter([]);
                    setActiveViewId(null);
                  }}
                  onSaveView={async () => {
                    const name = prompt('Name for this view?');
                    if (!name) return;
                    const wsId = Number(window.localStorage.getItem('antigravity:active_workspace_id') || 0);
                    const { apiJson } = await import('@/lib/api');
                    await apiJson(`/api/workspaces/${wsId}/views`, {
                      method: 'POST',
                      body: JSON.stringify({
                        name,
                        config: { platform_filter: platformFilter },
                      }),
                    });
                    await fetchSavedViews();
                  }}
                />

                <Dashboard
                  data={
                    platformFilter.length > 0
                      ? filterReportByPlatforms(
                          reportData as unknown as Parameters<typeof filterReportByPlatforms>[0],
                          new Set(platformFilter.map((p) => p.toLowerCase())),
                        ) as unknown as typeof reportData
                      : reportData
                  }
                  customKpis={customKpis}
                  baseCurrency={typeof window !== 'undefined'
                    ? (window.localStorage.getItem('antigravity:active_workspace_currency') || 'USD')
                    : 'USD'}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Subtle Background Elements */}
      <div className="fixed inset-0 pointer-events-none -z-10 overflow-hidden">
        <div className="absolute top-[-10%] right-[-10%] w-[50%] h-[50%] bg-blue-50/50 rounded-full blur-[120px]"></div>
        <div className="absolute bottom-[-10%] left-[-10%] w-[50%] h-[50%] bg-indigo-50/50 rounded-full blur-[120px]"></div>
      </div>
    </main>
  );
}

const PACE_TONE: Record<NonNullable<DashboardBudget['pacing']>['status'], { bar: string; pill: string; label: string }> = {
  on_pace:    { bar: 'bg-emerald-500', pill: 'bg-emerald-100 text-emerald-700', label: 'on pace' },
  under_pace: { bar: 'bg-blue-500',    pill: 'bg-blue-100 text-blue-700',       label: 'under pace' },
  over_pace:  { bar: 'bg-amber-500',   pill: 'bg-amber-100 text-amber-700',     label: 'over pace' },
  exhausted:  { bar: 'bg-red-500',     pill: 'bg-red-100 text-red-700',         label: 'exhausted' },
};

function BudgetBanner({ budgets }: { budgets: DashboardBudget[] }) {
  if (budgets.length === 0) return null;
  return (
    <Link
      href="/settings"
      className="block mb-8 border border-slate-100 rounded-2xl p-4 bg-white shadow-sm hover:border-blue-200 transition-colors"
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs uppercase tracking-wider font-black text-slate-400">
          Budgets — {budgets[0]?.pacing?.period_label ?? ''}
        </span>
        <span className="text-xs text-slate-400">manage in settings →</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {budgets.slice(0, 6).map((b) => {
          const p = b.pacing!;
          const tone = PACE_TONE[p.status];
          const pct = Math.min(100, Math.round(p.pct_used * 100));
          return (
            <div key={b.id} className="border border-slate-50 rounded-xl p-3">
              <div className="flex items-center justify-between mb-2">
                <div className="font-bold text-sm text-slate-800 truncate">{b.name}</div>
                <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${tone.pill}`}>
                  {tone.label}
                </span>
              </div>
              <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div className={`h-full ${tone.bar} transition-all`} style={{ width: `${pct}%` }} />
              </div>
              <div className="text-[11px] text-slate-500 mt-1">
                ${p.spent.toLocaleString()} / ${p.amount.toLocaleString()} ({pct}%)
              </div>
            </div>
          );
        })}
      </div>
    </Link>
  );
}

const ONBOARDING_DISMISSED_KEY = 'antigravity:onboarding_dismissed';

function OnboardingCard({
  hasReports,
  onJumpToConnections,
}: {
  hasReports: boolean;
  onJumpToConnections: () => void;
}) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    setDismissed(window.localStorage.getItem(ONBOARDING_DISMISSED_KEY) === '1');
  }, []);

  // Returning users with reports have already onboarded — hide implicitly.
  if (hasReports || dismissed) return null;

  function dismiss() {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(ONBOARDING_DISMISSED_KEY, '1');
    }
    setDismissed(true);
  }

  return (
    <div className="mb-8 border border-blue-100 rounded-2xl p-6 bg-gradient-to-br from-blue-50 to-indigo-50 shadow-sm relative">
      <button
        onClick={dismiss}
        className="absolute top-3 right-3 text-slate-400 hover:text-slate-700 text-xs font-bold"
      >
        Dismiss
      </button>
      <div className="flex items-center gap-2 mb-4">
        <Zap size={16} className="text-blue-600" />
        <span className="text-xs uppercase tracking-wider font-black text-blue-700">
          Welcome — three steps to your first report
        </span>
      </div>
      <ol className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <OnboardStep
          n={1}
          title="Connect a platform"
          body="Authenticate Google Ads, Meta, LinkedIn, TikTok, or Microsoft. OAuth happens in your browser; we encrypt the refresh token at rest."
          actionLabel="Open Accounts"
          onAction={onJumpToConnections}
        />
        <OnboardStep
          n={2}
          title="Run a sync"
          body="Pick a date range and click Sync. The first run takes a minute; the dashboard updates when the report is ready."
        />
        <OnboardStep
          n={3}
          title="Ask Gemini"
          body="Use the Chat tab to ask 'Which platform had the worst CPA last week?' — answers stream live with inline charts."
        />
      </ol>
      <p className="text-[11px] text-slate-500 mt-4">
        Already familiar? Set up alerts, budgets, and recurring syncs in <Link href="/settings" className="text-blue-600 hover:underline">Settings</Link>.
      </p>
    </div>
  );
}

function OnboardStep({
  n, title, body, actionLabel, onAction,
}: {
  n: number;
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <li className="border border-blue-100 rounded-xl p-4 bg-background">
      <div className="text-xs font-black text-blue-700 mb-1">Step {n}</div>
      <div className="text-sm font-bold text-slate-900">{title}</div>
      <div className="text-xs text-slate-600 mt-2">{body}</div>
      {actionLabel && (
        <button
          onClick={onAction}
          className="mt-3 text-xs font-bold text-blue-600 hover:text-blue-800"
        >
          {actionLabel} →
        </button>
      )}
    </li>
  );
}

function ViewToolbar({
  views,
  activeViewId,
  onSelectView,
  platformFilter,
  availablePlatforms,
  onTogglePlatform,
  onClearFilter,
  onSaveView,
}: {
  views: SavedView[];
  activeViewId: number | null;
  onSelectView: (id: number) => void;
  platformFilter: string[];
  availablePlatforms: string[];
  onTogglePlatform: (p: string) => void;
  onClearFilter: () => void;
  onSaveView: () => Promise<void> | void;
}) {
  const allPlatforms = Array.from(new Set(availablePlatforms.map((p) => p.toLowerCase()))).sort();
  if (allPlatforms.length === 0 && views.length === 0) return null;

  return (
    <div className="mb-6 border border-slate-100 rounded-2xl bg-white shadow-sm p-3 flex flex-wrap items-center gap-3">
      {/* Saved view selector */}
      {views.length > 0 && (
        <label className="flex items-center gap-2 text-xs text-slate-500">
          <span className="font-bold uppercase tracking-wider">View</span>
          <select
            value={activeViewId ?? ''}
            onChange={(e) => {
              const id = Number(e.target.value);
              if (id) onSelectView(id);
              else onClearFilter();
            }}
            className="text-sm font-semibold rounded-lg border border-slate-200 px-2 py-1.5"
          >
            <option value="">All data</option>
            {views.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}{v.visibility === 'workspace' ? ' (team)' : ''}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* Platform chips */}
      {allPlatforms.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-bold uppercase tracking-wider text-slate-500 mr-1">
            Platforms
          </span>
          {allPlatforms.map((p) => {
            const active = platformFilter.includes(p);
            return (
              <button
                key={p}
                onClick={() => onTogglePlatform(p)}
                className={`text-xs font-bold px-2.5 py-1 rounded-full border transition-colors ${
                  active
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-slate-600 border-slate-200 hover:border-blue-300'
                }`}
              >
                {p}
              </button>
            );
          })}
          {platformFilter.length > 0 && (
            <button
              onClick={onClearFilter}
              className="text-xs text-slate-500 hover:text-blue-600 ml-1"
            >
              clear
            </button>
          )}
        </div>
      )}

      <div className="ml-auto">
        {platformFilter.length > 0 && (
          <button
            onClick={() => void onSaveView()}
            className="text-xs font-bold text-blue-600 hover:text-blue-800 underline"
          >
            Save as view
          </button>
        )}
      </div>
    </div>
  );
}
