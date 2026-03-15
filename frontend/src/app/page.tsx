"use client";

import React, { useState, useEffect } from 'react';
import { UploadZone } from '@/components/UploadZone';
import { Dashboard } from '@/components/Dashboard';
import { ConnectionsManager } from '@/components/ConnectionsManager';
import { 
  ShieldCheck, Zap, ArrowLeft, Share2,
  History, Calendar, Layers, Globe
} from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

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
    roas: 0,
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
  const [reportData, setReportData] = useState<DashboardData | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStep, setUploadStep] = useState<string>('');
  const [history, setHistory] = useState<ApiHistoryReport[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [activeTab, setActiveTab] = useState<'upload' | 'accounts'>('upload');

  useEffect(() => {
    fetchHistory();
  }, []);

  const fetchHistory = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/reports`);
      const data = await response.json();
      setHistory(data);
    } catch (error) {
      console.error('Error fetching history:', error);
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
      const result = await response.json();
      
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
    if (!reportData) return;

    const summary = [
      `Spend: $${reportData.scorecards.totalSpend.toLocaleString()}`,
      `Conversions: ${reportData.scorecards.totalConversions.toLocaleString()}`,
      `Blended CPA: $${reportData.scorecards.blendedCPA.toLocaleString()}`,
    ].join(' | ');

    const text = `Antigravity Performance Snapshot\n${summary}`;

    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Antigravity Performance Snapshot',
          text,
          url: window.location.href,
        });
        return;
      } catch {
        // Fallback to clipboard below.
      }
    }

    try {
      await navigator.clipboard.writeText(`${text}\n${window.location.href}`);
      alert('Report summary link copied to clipboard.');
    } catch {
      alert('Unable to share automatically. Please copy the page URL manually.');
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
        throw new Error('Download failed');
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
    <main className="min-h-screen bg-[#fcfcfd] text-slate-900 selection:bg-blue-100 selection:text-blue-900 flex flex-col">
      {/* Premium Header */}
      <nav className="border-b border-slate-100 bg-white/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200 cursor-pointer" onClick={reset}>
              <Zap className="text-white w-6 h-6 fill-white" />
            </div>
            <h1 className="text-2xl font-black tracking-tight text-slate-900">
              ANTIGRAVITY<span className="text-blue-600">.</span>
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
            <div className="h-4 w-px bg-slate-200"></div>
            <div className="flex items-center gap-2 text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-full">
              <ShieldCheck size={16} />
              <span>System Online</span>
            </div>
          </div>
        </div>
      </nav>

      <div className="flex-1 flex relative overflow-hidden">
        {/* History Sidebar */}
        <aside className={`
          absolute lg:relative z-40 h-[calc(100vh-80px)] w-80 bg-white border-r border-slate-100 transition-all duration-500 ease-in-out
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
                      className={`h-12 px-8 rounded-2xl font-bold flex items-center gap-2 transition-all ${activeTab === 'upload' ? 'bg-white shadow-xl shadow-blue-100 text-blue-600' : 'text-slate-400 hover:text-slate-600'}`}
                    >
                      <Layers size={18} />
                      File Upload
                    </button>
                    <button 
                      onClick={() => setActiveTab('accounts')}
                      className={`h-12 px-8 rounded-2xl font-bold flex items-center gap-2 transition-all ${activeTab === 'accounts' ? 'bg-white shadow-xl shadow-blue-100 text-blue-600' : 'text-slate-400 hover:text-slate-600'}`}
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
                      className="px-6 h-12 bg-white border border-slate-200 text-slate-700 rounded-xl font-bold flex items-center gap-2 hover:bg-slate-50 shadow-sm transition-all"
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

                <Dashboard data={reportData} />
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
