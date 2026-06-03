/**
 * Client-side filtering of a Report by platform.
 *
 * The Dashboard component is dumb — it just renders what it's given. To
 * apply a saved view's `platform_filter`, we re-derive the data
 * structure here in the parent before handing it to <Dashboard>.
 *
 * Why client-side rather than a server query parameter? The Report is
 * already materialized in the parent's state, and the filter is purely a
 * presentation concern; round-tripping to the server would be slower
 * and wouldn't change the underlying data.
 */

interface PlatformDeltaValue {
  spend?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
  conversions?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
  cpa?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
  ctr?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
  blendedCPA?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
  blendedCTR?: { value: string; direction: 'positive' | 'negative' | 'neutral' };
}

interface FilterableReport {
  chartData: Array<Record<string, unknown>>;
  scorecards: {
    totalSpend: number;
    totalImpressions: number;
    totalClicks: number;
    totalConversions: number;
    totalRevenue?: number;
    blendedCPA: number;
    blendedCTR: number;
    blendedCVR: number;
    blendedCPC: number;
    blendedCPM: number;
    blendedROAS: number | null;
  };
  scorecardDeltas: Record<string, unknown>;
  platformDeltas: Record<string, PlatformDeltaValue>;
  campaignSummary: Array<Record<string, unknown>>;
  hierarchySummary: {
    campaign: Array<Record<string, unknown>>;
    adGroup: Array<Record<string, unknown>>;
    adAsset: Array<Record<string, unknown>>;
  };
  platformSummary: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

function safe(v: unknown): number {
  const n = typeof v === 'string' ? parseFloat(v) : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Re-derive scorecards by summing rows from the filtered platform_summary.
 * Blended metrics are re-computed from the aggregated totals (not averaged
 * across rows — that would be wrong).
 */
function deriveScorecards(platformRows: Array<Record<string, unknown>>): FilterableReport['scorecards'] {
  let spend = 0, impressions = 0, clicks = 0, conversions = 0, revenue = 0;
  for (const r of platformRows) {
    spend += safe(r.spend);
    impressions += safe(r.impressions);
    clicks += safe(r.clicks);
    conversions += safe(r.conversions);
    revenue += safe(r.revenue);
  }
  const round = (n: number) => Math.round(n * 100) / 100;
  return {
    totalSpend: round(spend),
    totalImpressions: Math.round(impressions),
    totalClicks: Math.round(clicks),
    totalConversions: Math.round(conversions),
    totalRevenue: round(revenue),
    blendedCPA: conversions ? round(spend / conversions) : 0,
    blendedCTR: impressions ? round((clicks / impressions) * 100) : 0,
    blendedCVR: clicks ? round((conversions / clicks) * 100) : 0,
    blendedCPC: clicks ? round(spend / clicks) : 0,
    blendedCPM: impressions ? round((spend / impressions) * 1000) : 0,
    blendedROAS: spend > 0 && revenue > 0 ? round(revenue / spend) : null,
  };
}

/**
 * Filter a report to only the selected platforms. When `selected` is
 * empty or contains all known platforms, returns the input unchanged.
 */
export function filterReportByPlatforms<T extends FilterableReport>(
  report: T,
  selected: Set<string>,
): T {
  if (selected.size === 0) return report;

  const platformRows = (report.platformSummary || []).filter(
    (r) => selected.has(String(r.platform).toLowerCase()),
  );

  // If the filter would match nothing in this report, fall through —
  // showing an empty dashboard is more confusing than showing the
  // unfiltered one.
  if (platformRows.length === 0) return report;

  const allPlatforms = new Set(
    (report.platformSummary || []).map((r) => String(r.platform).toLowerCase()),
  );
  if (selected.size === allPlatforms.size && [...selected].every((p) => allPlatforms.has(p))) {
    return report;
  }

  // Chart data uses `${platform}_${metric}` columns; drop the unselected ones.
  const allowedSuffixes = ['_spend', '_cpa', '_ctr', '_roas', '_revenue'];
  const filteredChart = (report.chartData || []).map((row) => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(row)) {
      if (k === 'date') {
        out[k] = v;
        continue;
      }
      const matchedPlatform = [...selected].find((p) =>
        allowedSuffixes.some((s) => k === `${p}${s}`),
      );
      if (matchedPlatform) out[k] = v;
    }
    return out;
  });

  const filteredCampaigns = (report.campaignSummary || []).filter(
    (c) => selected.has(String(c.platform).toLowerCase()),
  );

  const filteredHierarchy = {
    campaign: (report.hierarchySummary?.campaign || []).filter(
      (r) => selected.has(String(r.platform).toLowerCase()),
    ),
    adGroup: (report.hierarchySummary?.adGroup || []).filter(
      (r) => selected.has(String(r.platform).toLowerCase()),
    ),
    adAsset: (report.hierarchySummary?.adAsset || []).filter(
      (r) => selected.has(String(r.platform).toLowerCase()),
    ),
  };

  // Filter per-platform deltas to the selected set (blended deltas stay,
  // they're no longer accurate but better than showing nothing).
  const filteredPlatformDeltas: typeof report.platformDeltas = {};
  for (const [k, v] of Object.entries(report.platformDeltas || {})) {
    if (selected.has(k.toLowerCase())) filteredPlatformDeltas[k] = v;
  }

  return {
    ...report,
    chartData: filteredChart,
    scorecards: deriveScorecards(platformRows),
    platformSummary: platformRows,
    platformDeltas: filteredPlatformDeltas,
    campaignSummary: filteredCampaigns,
    hierarchySummary: filteredHierarchy,
  };
}
