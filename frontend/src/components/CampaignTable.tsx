"use client";

import React, { useState, useMemo } from 'react';
import { ChevronUp, ChevronDown, Award, AlertTriangle } from 'lucide-react';

interface CampaignRow {
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

type SortKey = keyof CampaignRow;
type SortDir = 'asc' | 'desc';

interface CampaignTableProps {
  data: CampaignRow[];
  blendedCPA: number;
}

const PlatformBadge = ({ platform }: { platform: string }) => (
  <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider
    ${platform === 'google'    ? 'bg-blue-100 text-blue-700' :
      platform === 'meta'     ? 'bg-indigo-100 text-indigo-700' :
      platform === 'linkedin' ? 'bg-sky-100 text-sky-700' :
      platform === 'tiktok'   ? 'bg-pink-100 text-pink-700' :
                                'bg-slate-100 text-slate-600'}`}>
    {platform}
  </span>
);

const CpaCell = ({ cpa, blendedCPA }: { cpa: number; blendedCPA: number }) => {
  const ratio = blendedCPA > 0 ? cpa / blendedCPA : 1;
  const cls =
    ratio < 0.8  ? 'text-emerald-700 bg-emerald-50' :
    ratio < 1.15 ? 'text-slate-700'                 :
    ratio < 1.5  ? 'text-amber-700  bg-amber-50'    :
                   'text-red-700    bg-red-50';
  return (
    <span className={`px-2.5 py-1 rounded-lg text-sm font-bold tabular-nums inline-block ${cls}`}>
      ${cpa.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
    </span>
  );
};

export const CampaignTable: React.FC<CampaignTableProps> = ({ data, blendedCPA }) => {
  const [sortKey, setSortKey] = useState<SortKey>('spend');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(key === 'campaign' || key === 'platform' ? 'asc' : 'desc');
    }
  };

  const sorted = useMemo(() => {
    return [...data].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (typeof av === 'string' && typeof bv === 'string')
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
  }, [data, sortKey, sortDir]);

  const renderSortIcon = (col: SortKey) => {
    if (sortKey !== col) return <ChevronDown size={11} className="text-slate-300 ml-0.5" />;
    return sortDir === 'asc'
      ? <ChevronUp   size={11} className="text-blue-600 ml-0.5" />
      : <ChevronDown size={11} className="text-blue-600 ml-0.5" />;
  };

  const renderHeaderCell = (col: SortKey, label: string, right = false) => (
    <th
      key={col}
      className={`px-5 py-4 text-xs font-bold text-slate-400 uppercase tracking-[0.15em] cursor-pointer hover:text-slate-600 select-none transition-colors whitespace-nowrap ${right ? 'text-right' : ''}`}
      onClick={() => handleSort(col)}
    >
      <span className={`inline-flex items-center gap-0.5 ${right ? 'justify-end w-full' : ''}`}>
        {label}{renderSortIcon(col)}
      </span>
    </th>
  );

  const activeCampaigns = data.filter(d => d.conversions > 0);
  const minCPA = activeCampaigns.length > 0 ? Math.min(...activeCampaigns.map(d => d.cpa)) : -Infinity;
  const maxCPA = activeCampaigns.length > 0 ? Math.max(...activeCampaigns.map(d => d.cpa)) : Infinity;

  return (
    <div className="bg-white rounded-[2rem] border border-slate-100 shadow-sm overflow-hidden">
      <div className="px-8 py-6 border-b border-slate-50 flex items-center justify-between">
        <h3 className="text-xl font-bold text-slate-800">Campaign Drill-Down</h3>
        <span className="px-4 py-1.5 bg-slate-50 text-slate-500 rounded-full text-xs font-bold uppercase tracking-widest">
          {data.length} Campaigns
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-50/50">
              {renderHeaderCell('platform', 'Platform')}
              {renderHeaderCell('campaign', 'Campaign')}
              {renderHeaderCell('spend', 'Spend', true)}
              {renderHeaderCell('spend_share', '% Budget', true)}
              {renderHeaderCell('impressions', 'Impr.', true)}
              {renderHeaderCell('ctr', 'CTR', true)}
              {renderHeaderCell('clicks', 'Clicks', true)}
              {renderHeaderCell('cvr', 'CVR', true)}
              {renderHeaderCell('conversions', 'Conv.', true)}
              {renderHeaderCell('cpc', 'CPC', true)}
              {renderHeaderCell('cpa', 'CPA', true)}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {sorted.map((row, idx) => {
              const isTop   = row.conversions > 0 && row.cpa === minCPA;
              const isWorst = row.conversions > 0 && row.cpa === maxCPA && row.cpa !== minCPA;
              return (
                <tr key={idx} className={`hover:bg-slate-50/80 transition-colors ${
                  isTop ? 'bg-emerald-50/30' : isWorst ? 'bg-red-50/20' : ''
                }`}>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-1.5">
                      <PlatformBadge platform={row.platform} />
                      {isTop   && <Award         size={13} className="text-emerald-500 flex-shrink-0" />}
                      {isWorst && <AlertTriangle size={13} className="text-red-400    flex-shrink-0" />}
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-sm font-bold text-slate-700 max-w-[180px] truncate">
                    {row.campaign}
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    ${row.spend.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-500 text-right tabular-nums">
                    {(row.spend_share ?? 0).toFixed(1)}%
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    {(row.impressions ?? 0).toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    {(row.ctr ?? 0).toFixed(2)}%
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    {(row.clicks ?? 0).toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    {(row.cvr ?? 0).toFixed(2)}%
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    {(row.conversions ?? 0).toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-sm font-medium text-slate-600 text-right tabular-nums">
                    ${(row.cpc ?? 0).toFixed(2)}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <CpaCell cpa={row.cpa} blendedCPA={blendedCPA} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
