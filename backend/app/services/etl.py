import pandas as pd
import io
from typing import List, Dict, Optional, Tuple

UNIVERSAL_COLUMNS = [
    'date', 'platform', 'campaign', 'ad_group', 'ad_asset', 'spend', 'impressions', 'clicks', 'conversions', 'revenue'
]

# Platform-specific column mappings to the Universal Schema.
# Revenue columns are optional — they map ad platform "conversion value" fields.
PLATFORM_MAPPINGS = {
    'google': {
        'Day': 'date',
        'Campaign': 'campaign',
        'Cost': 'spend',
        'Impressions': 'impressions',
        'Clicks': 'clicks',
        'Conversions': 'conversions',
        'Conv. Value': 'revenue',
    },
    'meta': {
        'Reporting Starts': 'date',
        'Campaign Name': 'campaign',
        'Amount Spent (USD)': 'spend',
        'Impressions': 'impressions',
        'Link Clicks': 'clicks',
        'Results': 'conversions',
        'Purchase ROAS (Return on Ad Spend)': 'revenue',
    },
    'linkedin': {
        'Day': 'date',
        'Campaign Name': 'campaign',
        'Total Spent (USD)': 'spend',
        'Impressions': 'impressions',
        'Clicks': 'clicks',
        'Conversions': 'conversions',
        'Conversion Value (USD)': 'revenue',
    },
    'tiktok': {
        'Date': 'date',
        'Campaign name': 'campaign',
        'Cost': 'spend',
        'Impressions': 'impressions',
        'Clicks': 'clicks',
        'Conversions': 'conversions',
        'Total Revenue': 'revenue',
    },
}

HIERARCHY_ALIASES = {
    'google': {
        'ad_group': ['Ad group', 'Ad Group', 'Ad group name', 'Asset group', 'Asset Group'],
        'ad_asset': ['Ad', 'Ad name', 'Asset', 'Asset name', 'Asset group'],
    },
    'meta': {
        'ad_group': ['Ad Set Name', 'Ad set name', 'Ad Set'],
        'ad_asset': ['Ad Name', 'Ad name', 'Creative Name'],
    },
    'linkedin': {
        'ad_group': ['Campaign Group Name', 'Campaign Group', 'Ad Set Name'],
        'ad_asset': ['Creative Name', 'Ad Name', 'Ad'],
    },
    'tiktok': {
        'ad_group': ['Ad Group Name', 'Ad Group', 'Ad group name'],
        'ad_asset': ['Ad Name', 'Ad name', 'Asset Name'],
    },
    'microsoft': {
        'ad_group': ['Ad group', 'Ad Group', 'Asset group'],
        'ad_asset': ['Ad', 'Ad name', 'Asset'],
    },
}

MIN_PRIOR_SPEND = 100
MIN_PRIOR_IMPRESSIONS = 1000
MIN_PRIOR_CLICKS = 50
MIN_PRIOR_CONVERSIONS = 10
MIN_CAMPAIGN_CONVERSIONS_FOR_RANK = 5


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _first_existing_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    return None


def detect_platform(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    for platform, mapping in PLATFORM_MAPPINGS.items():
        matches = len(set(mapping.keys()).intersection(cols))
        if matches >= 3:
            return platform
    return "unknown"


def process_csv(file_content: bytes, filename: str) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(file_content))
    platform = detect_platform(df)

    if platform == "unknown":
        return pd.DataFrame(columns=UNIVERSAL_COLUMNS)

    mapping = PLATFORM_MAPPINGS[platform]
    df = df.rename(columns=mapping)
    df['platform'] = platform

    hierarchy = HIERARCHY_ALIASES.get(platform, {})
    ad_group_source = _first_existing_column(df, hierarchy.get('ad_group', []))
    ad_asset_source = _first_existing_column(df, hierarchy.get('ad_asset', []))

    if ad_group_source:
        df['ad_group'] = df[ad_group_source]
    if ad_asset_source:
        df['ad_asset'] = df[ad_asset_source]

    for col in UNIVERSAL_COLUMNS:
        if col not in df.columns:
            df[col] = '' if col in ('campaign', 'ad_group', 'ad_asset', 'date', 'platform') else 0

    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    for col in ['spend', 'impressions', 'clicks', 'conversions', 'revenue']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    for col in ['campaign', 'ad_group', 'ad_asset']:
        df[col] = df[col].fillna('').astype(str).str.strip()

    return df[UNIVERSAL_COLUMNS]


def _compute_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add CTR, CVR, CPC, CPM, CPA, ROAS columns to an aggregated DataFrame."""
    df = df.copy()
    df['ctr']  = df.apply(lambda r: _safe_divide(r['clicks'],       r['impressions']) * 100,  axis=1)
    df['cvr']  = df.apply(lambda r: _safe_divide(r['conversions'],  r['clicks'])      * 100,  axis=1)
    df['cpc']  = df.apply(lambda r: _safe_divide(r['spend'],        r['clicks']),              axis=1)
    df['cpm']  = df.apply(lambda r: _safe_divide(r['spend'],        r['impressions']) * 1000, axis=1)
    df['cpa']  = df.apply(lambda r: _safe_divide(r['spend'],        r['conversions']),         axis=1)
    df['roas'] = df.apply(lambda r: _safe_divide(r['revenue'],      r['spend']),               axis=1)
    return df


def _period_label(df: pd.DataFrame) -> str:
    """Format a human-readable date range for a DataFrame."""
    if df.empty:
        return "N/A"
    min_d, max_d = df['date'].min(), df['date'].max()
    return min_d if min_d == max_d else f"{min_d} – {max_d}"


def _compute_deltas(current_df: pd.DataFrame, prior_df: pd.DataFrame) -> Dict:
    """
    Compute blended + per-platform metric deltas between two DataFrames.
    Returns {"blended": {...}, "byPlatform": {platform: {...}}}.
    """
    def _delta(
        curr: float,
        prev: float,
        lower_is_better: bool = False,
        min_prior: float = 0.0,
        medium_prior: float = 0.0,
        high_prior: float = 0.0,
        sample_size: Optional[float] = None,
        min_sample: float = 0.0,
    ) -> Dict:
        baseline = sample_size if sample_size is not None else prev
        if prev == 0 or baseline < min_prior or baseline < min_sample:
            return {"value": "N/A", "direction": "neutral", "confidence": "low"}

        pct = ((curr - prev) / prev) * 100
        sign = "+" if pct >= 0 else ""
        improving = (pct <= 0) if lower_is_better else (pct >= 0)

        confidence = "low"
        if high_prior and baseline >= high_prior:
            confidence = "high"
        elif medium_prior and baseline >= medium_prior:
            confidence = "medium"

        return {
            "value": f"{sign}{pct:.1f}%",
            "direction": "positive" if improving else "negative",
            "confidence": confidence,
        }

    def _df_deltas(c: pd.DataFrame, p: pd.DataFrame) -> Dict:
        c_spend  = float(c['spend'].sum())
        p_spend  = float(p['spend'].sum())
        c_conv   = float(c['conversions'].sum())
        p_conv   = float(p['conversions'].sum())
        c_clicks = float(c['clicks'].sum())
        p_clicks = float(p['clicks'].sum())
        c_impr   = float(c['impressions'].sum())
        p_impr   = float(p['impressions'].sum())
        c_cpa    = _safe_divide(c_spend, c_conv)
        p_cpa    = _safe_divide(p_spend, p_conv)
        c_ctr    = _safe_divide(c_clicks, c_impr) * 100
        p_ctr    = _safe_divide(p_clicks, p_impr) * 100
        return {
            "spend": _delta(
                c_spend,
                p_spend,
                min_prior=MIN_PRIOR_SPEND,
                medium_prior=500,
                high_prior=1500,
            ),
            "impressions": _delta(
                c_impr,
                p_impr,
                min_prior=MIN_PRIOR_IMPRESSIONS,
                medium_prior=5000,
                high_prior=20000,
            ),
            "clicks": _delta(
                c_clicks,
                p_clicks,
                min_prior=MIN_PRIOR_CLICKS,
                medium_prior=250,
                high_prior=1000,
            ),
            "conversions": _delta(
                c_conv,
                p_conv,
                min_prior=MIN_PRIOR_CONVERSIONS,
                medium_prior=25,
                high_prior=75,
            ),
            "blendedCPA": _delta(
                c_cpa,
                p_cpa,
                lower_is_better=True,
                sample_size=p_conv,
                min_sample=MIN_PRIOR_CONVERSIONS,
                medium_prior=25,
                high_prior=75,
            ),
            "blendedCTR": _delta(
                c_ctr,
                p_ctr,
                sample_size=p_impr,
                min_sample=MIN_PRIOR_IMPRESSIONS,
                medium_prior=5000,
                high_prior=20000,
            ),
        }

    blended = _df_deltas(current_df, prior_df)

    by_platform: Dict = {}
    platforms = set(current_df['platform'].unique()) | set(prior_df['platform'].unique())
    for plat in platforms:
        c_plat = current_df[current_df['platform'] == plat]
        p_plat = prior_df[prior_df['platform'] == plat]
        by_platform[plat] = _df_deltas(c_plat, p_plat)

    return {"blended": blended, "byPlatform": by_platform}


def _auto_split_periods(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, str, str, str]:
    """
    Automatically choose how to split the data for period comparison.
    - ≥ 300 days span AND ≥ 2 calendar years → year-over-year (most recent year vs all prior)
    - Otherwise                               → period-over-period (split at midpoint)
    Returns (current_df, prior_df, comparison_type, current_label, prior_label).
    """
    dates = sorted(df['date'].unique())
    if len(dates) < 2:
        return df, pd.DataFrame(columns=df.columns), "none", _period_label(df), "N/A"

    min_date = pd.to_datetime(dates[0])
    max_date = pd.to_datetime(dates[-1])
    span_days = (max_date - min_date).days

    if span_days >= 300:
        years = sorted(df['date'].apply(lambda d: str(d)[:4]).unique())
        if len(years) >= 2:
            latest_year = years[-1]
            prior_years = years[:-1]
            current_df  = df[df['date'].apply(lambda d: str(d)[:4]) == latest_year].copy()
            prior_df    = df[df['date'].apply(lambda d: str(d)[:4]).isin(prior_years)].copy()
            return current_df, prior_df, "year_over_year", latest_year, " / ".join(prior_years)

    # Period-over-period: split at the midpoint of the sorted date list
    mid = len(dates) // 2
    current_df = df[df['date'].isin(dates[mid:])].copy()
    prior_df   = df[df['date'].isin(dates[:mid])].copy()
    return current_df, prior_df, "period_over_period", _period_label(current_df), _period_label(prior_df)


def _build_hierarchy_summary(
    df: pd.DataFrame,
    level_col: str,
    level_key: str,
    total_spend: float,
) -> List[Dict]:
    if level_col not in df.columns:
        return []

    level_df = df[df[level_col].astype(str).str.strip() != ''].copy()
    if level_df.empty:
        return []

    agg = level_df.groupby(['platform', level_col]).agg(
        spend=('spend', 'sum'),
        impressions=('impressions', 'sum'),
        clicks=('clicks', 'sum'),
        conversions=('conversions', 'sum'),
        revenue=('revenue', 'sum'),
    ).reset_index()
    agg = _compute_derived_metrics(agg)
    agg['spend_share'] = agg['spend'].apply(lambda x: round(_safe_divide(x, total_spend) * 100, 2))
    agg = agg.rename(columns={level_col: 'name'})
    agg['level'] = level_key
    return agg.round(2).to_dict(orient='records')


def aggregate_data(
    dataframes: List[pd.DataFrame],
    comparison_dataframes: Optional[List[pd.DataFrame]] = None,
) -> Dict:
    if not dataframes:
        return {
            "chartData": [],
            "scorecards": {
                "totalSpend": 0, "totalImpressions": 0, "totalClicks": 0,
                "totalConversions": 0, "blendedCPA": 0, "blendedCTR": 0,
                "blendedCVR": 0, "blendedCPC": 0, "blendedCPM": 0, "blendedROAS": None,
            },
            "scorecardDeltas": {},
            "platformDeltas": {},
            "comparisonType": "none",
            "currentPeriodLabel": "N/A",
            "priorPeriodLabel": "N/A",
            "campaignSummary": [],
            "hierarchySummary": {"campaign": [], "adGroup": [], "adAsset": []},
            "platformSummary": [],
            "topPerformer": None,
            "bottomPerformer": None,
            "geminiInput": {},
        }

    combined_df = pd.concat(dataframes, ignore_index=True).fillna(0)
    for col in UNIVERSAL_COLUMNS:
        if col not in combined_df.columns:
            combined_df[col] = 0

    # ── Period Comparison setup ───────────────────────────────────────────────
    if comparison_dataframes is not None:
        if comparison_dataframes:
            comparison_combined = pd.concat(comparison_dataframes, ignore_index=True).fillna(0)
        else:
            comparison_combined = pd.DataFrame(columns=combined_df.columns)
        for col in UNIVERSAL_COLUMNS:
            if col not in comparison_combined.columns:
                comparison_combined[col] = 0
        current_period_df  = combined_df
        prior_period_df    = comparison_combined
        comparison_type    = "manual_comparison"
        current_label      = _period_label(combined_df)
        prior_label        = _period_label(comparison_combined)
    else:
        current_period_df, prior_period_df, comparison_type, current_label, prior_label = (
            _auto_split_periods(combined_df)
        )

    if not prior_period_df.empty:
        delta_result     = _compute_deltas(current_period_df, prior_period_df)
        scorecard_deltas = delta_result["blended"]
        platform_deltas  = delta_result["byPlatform"]
    else:
        scorecard_deltas = {}
        platform_deltas  = {}

    # ── Scorecards (always computed on the full combined_df) ─────────────────
    total_spend       = float(combined_df['spend'].sum())
    total_impressions = int(combined_df['impressions'].sum())
    total_clicks      = int(combined_df['clicks'].sum())
    total_conversions = int(combined_df['conversions'].sum())
    total_revenue     = float(combined_df['revenue'].sum())

    scorecards = {
        "totalSpend":       round(total_spend, 2),
        "totalImpressions": total_impressions,
        "totalClicks":      total_clicks,
        "totalConversions": total_conversions,
        "blendedCPA":       round(_safe_divide(total_spend, total_conversions), 2),
        "blendedCTR":       round(_safe_divide(total_clicks, total_impressions) * 100, 2),
        "blendedCVR":       round(_safe_divide(total_conversions, total_clicks) * 100, 2),
        "blendedCPC":       round(_safe_divide(total_spend, total_clicks), 2),
        "blendedCPM":       round(_safe_divide(total_spend, total_impressions) * 1000, 2),
        "blendedROAS":      round(_safe_divide(total_revenue, total_spend), 2) if total_revenue > 0 else None,
    }

    # ── WoW Deltas ────────────────────────────────────────────────────────────
    # (already computed above via _compute_deltas)

    # ── Multi-metric time-series charts (spend + CPA + CTR, pivoted by platform) ─
    daily = combined_df.groupby(['date', 'platform']).agg(
        spend=('spend', 'sum'), impressions=('impressions', 'sum'),
        clicks=('clicks', 'sum'), conversions=('conversions', 'sum'),
        revenue=('revenue', 'sum'),
    ).reset_index()
    daily = _compute_derived_metrics(daily)

    def _pivot_metric(metric: str) -> pd.DataFrame:
        pt = daily.pivot_table(index='date', columns='platform', values=metric, fill_value=0).reset_index()
        pt.columns = ['date'] + [f"{c}_{metric}" for c in pt.columns if c != 'date']
        return pt

    chart_df = _pivot_metric('spend')
    for m in ('cpa', 'ctr'):
        chart_df = chart_df.merge(_pivot_metric(m), on='date', how='left')

    all_platforms = ['google', 'meta', 'linkedin', 'tiktok', 'microsoft']
    for p in all_platforms:
        for suffix in ('_spend', '_cpa', '_ctr'):
            col = f"{p}{suffix}"
            if col not in chart_df.columns:
                chart_df[col] = 0

    chart_data = chart_df.fillna(0).to_dict(orient='records')

    # ── Platform Summary (one aggregated row per platform) ────────────────────
    plat_agg = combined_df.groupby('platform').agg(
        spend=('spend', 'sum'), impressions=('impressions', 'sum'),
        clicks=('clicks', 'sum'), conversions=('conversions', 'sum'),
        revenue=('revenue', 'sum'),
    ).reset_index()
    plat_agg = _compute_derived_metrics(plat_agg)
    plat_agg['spend_share'] = plat_agg['spend'].apply(
        lambda x: round(_safe_divide(x, total_spend) * 100, 1)
    )
    plat_agg = plat_agg.round(2)
    # Merge per-platform deltas into each row
    plat_rows = plat_agg.to_dict(orient='records')
    for row in plat_rows:
        row['deltas'] = platform_deltas.get(row['platform'], {})
    platform_summary = plat_rows

    # ── Campaign Summary with full derived metrics ────────────────────────────
    camp_agg = combined_df.groupby(['platform', 'campaign']).agg(
        spend=('spend', 'sum'), impressions=('impressions', 'sum'),
        clicks=('clicks', 'sum'), conversions=('conversions', 'sum'),
        revenue=('revenue', 'sum'),
    ).reset_index()
    camp_agg = _compute_derived_metrics(camp_agg)
    camp_agg['spend_share'] = camp_agg['spend'].apply(
        lambda x: round(_safe_divide(x, total_spend) * 100, 1)
    )
    campaign_summary = camp_agg.round(2).to_dict(orient='records')

    hierarchy_summary = {
        "campaign": _build_hierarchy_summary(combined_df, 'campaign', 'campaign', total_spend),
        "adGroup": _build_hierarchy_summary(combined_df, 'ad_group', 'adGroup', total_spend),
        "adAsset": _build_hierarchy_summary(combined_df, 'ad_asset', 'adAsset', total_spend),
    }

    # ── Top / Bottom Performers (by CPA among campaigns with conversions) ────
    active = camp_agg[camp_agg['conversions'] >= MIN_CAMPAIGN_CONVERSIONS_FOR_RANK]
    top_performer: Optional[Dict] = None
    bottom_performer: Optional[Dict] = None
    if not active.empty:
        top_row    = active.loc[active['cpa'].idxmin()]
        bottom_row = active.loc[active['cpa'].idxmax()]
        top_performer = {
            "campaign":    top_row['campaign'],
            "platform":    top_row['platform'],
            "cpa":         round(float(top_row['cpa']), 2),
            "spend":       round(float(top_row['spend']), 2),
            "conversions": int(top_row['conversions']),
        }
        bottom_performer = {
            "campaign":    bottom_row['campaign'],
            "platform":    bottom_row['platform'],
            "cpa":         round(float(bottom_row['cpa']), 2),
            "spend":       round(float(bottom_row['spend']), 2),
            "conversions": int(bottom_row['conversions']),
        }

    # ── Structured Gemini Input ───────────────────────────────────────────────
    gemini_input = {
        "date_range":         f"{combined_df['date'].min()} to {combined_df['date'].max()}",
        "comparison_type":    comparison_type,
        "current_period":     current_label,
        "prior_period":       prior_label,
        "scorecards":         scorecards,
        "period_deltas":      scorecard_deltas,
        "platform_summary": [
            {k: v for k, v in row.items()
             if k in ('platform', 'spend', 'impressions', 'clicks', 'conversions',
                      'cpa', 'ctr', 'cvr', 'cpc', 'roas', 'spend_share')}
            for row in platform_summary
        ],
        "platform_deltas":    platform_deltas,
        "top_campaign":       top_performer,
        "worst_campaign":     bottom_performer,
        "campaign_count":     len(campaign_summary),
        "hierarchy_counts": {
            "campaign": len(hierarchy_summary["campaign"]),
            "ad_group": len(hierarchy_summary["adGroup"]),
            "ad_asset": len(hierarchy_summary["adAsset"]),
        },
    }

    return {
        "chartData":          chart_data,
        "scorecards":         scorecards,
        "scorecardDeltas":    scorecard_deltas,
        "platformDeltas":     platform_deltas,
        "comparisonType":     comparison_type,
        "currentPeriodLabel": current_label,
        "priorPeriodLabel":   prior_label,
        "campaignSummary":    campaign_summary,
        "hierarchySummary":   hierarchy_summary,
        "platformSummary":    platform_summary,
        "topPerformer":       top_performer,
        "bottomPerformer":    bottom_performer,
        "geminiInput":        gemini_input,
    }
