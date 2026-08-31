"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, Grid2X2, LayoutList, Rows3, RefreshCcw, Search } from 'lucide-react';
import { useWorkspace } from '@/lib/workspace';
import { apiJson } from '@/lib/api';
import CreativeCard, { Creative, ReviewStatus, ViewMode, platformLabel } from '@/components/CreativeCard';

interface CreativeListResponse {
  total: number;
  limit: number;
  offset: number;
  creatives: Creative[];
}

interface CreativeSummary {
  total: number;
  by_platform: Record<string, number>;
  by_review_status: Record<string, number>;
}

interface SyncJobStatus {
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress_percent: number;
  current_step: string;
  error_message?: string | null;
}

const PLATFORMS = ['meta', 'google', 'microsoft'] as const;

const REVIEW_FILTERS: { id: string; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'unreviewed', label: 'Unreviewed' },
  { id: 'keep', label: 'Keep' },
  { id: 'remove', label: 'Remove' },
  { id: 'change', label: 'Change' },
];

const VIEW_OPTIONS: { id: ViewMode; label: string; Icon: typeof Grid2X2 }[] = [
  { id: 'card', label: 'Card', Icon: Grid2X2 },
  { id: 'compact', label: 'Compact', Icon: LayoutList },
  { id: 'expanded', label: 'Expanded', Icon: Rows3 },
];

const PAGE_SIZE = 60;
// The sync runs on the task queue; poll its SyncJob rather than holding the
// request open. Give up after 15 minutes so a stuck job can't poll forever.
const POLL_INTERVAL_MS = 4000;
const POLL_CEILING_MS = 15 * 60 * 1000;

export default function CreativesPage() {
  const { active, loading: workspaceLoading } = useWorkspace();
  const workspaceId = active?.id ?? null;

  const [creatives, setCreatives] = useState<Creative[]>([]);
  const [summary, setSummary] = useState<CreativeSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [platform, setPlatform] = useState<string>('all');
  const [reviewFilter, setReviewFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [view, setView] = useState<ViewMode>('card');

  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<SyncJobStatus | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollDeadline = useRef<number>(0);

  const load = useCallback(
    async (nextOffset: number, replace: boolean) => {
      if (!workspaceId) return;
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          limit: String(PAGE_SIZE),
          offset: String(nextOffset),
        });
        if (platform !== 'all') params.set('platform', platform);
        if (reviewFilter !== 'all') params.set('review_status', reviewFilter);
        if (search.trim()) params.set('search', search.trim());

        const data = await apiJson<CreativeListResponse>(
          `/api/workspaces/${workspaceId}/creatives?${params.toString()}`,
        );
        setTotal(data.total);
        setOffset(nextOffset);
        setCreatives((prev) => (replace ? data.creatives : [...prev, ...data.creatives]));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Could not load creatives');
      } finally {
        setLoading(false);
      }
    },
    [workspaceId, platform, reviewFilter, search],
  );

  const loadSummary = useCallback(async () => {
    if (!workspaceId) return;
    try {
      setSummary(await apiJson<CreativeSummary>(`/api/workspaces/${workspaceId}/creatives/summary`));
    } catch {
      // The summary only drives filter counts; a failure here is not worth
      // blocking the gallery over.
    }
  }, [workspaceId]);

  useEffect(() => {
    void load(0, true);
  }, [load]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  // Debounce the search box so typing doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 350);
    return () => clearTimeout(t);
  }, [searchInput]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = null;
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const startSync = async () => {
    if (!workspaceId || syncing) return;
    setSyncing(true);
    setError(null);
    setSyncStatus({ status: 'pending', progress_percent: 0, current_step: 'queued' });

    try {
      const { sync_job_id: jobId } = await apiJson<{ sync_job_id: number }>(
        `/api/workspaces/${workspaceId}/creatives/sync`,
        { method: 'POST', body: JSON.stringify({}) },
      );

      pollDeadline.current = Date.now() + POLL_CEILING_MS;
      stopPolling();
      pollTimer.current = setInterval(async () => {
        if (Date.now() > pollDeadline.current) {
          stopPolling();
          setSyncing(false);
          setSyncStatus({
            status: 'failed',
            progress_percent: 0,
            current_step: '',
            error_message: 'Sync is taking longer than expected — check the activity log.',
          });
          return;
        }
        try {
          const job = await apiJson<SyncJobStatus>(`/api/sync-jobs/${jobId}`);
          setSyncStatus(job);
          if (job.status === 'completed' || job.status === 'failed') {
            stopPolling();
            setSyncing(false);
            await load(0, true);
            await loadSummary();
          }
        } catch {
          // A single failed poll is not fatal; the deadline bounds the loop.
        }
      }, POLL_INTERVAL_MS);
    } catch (e) {
      setSyncing(false);
      setSyncStatus(null);
      setError(e instanceof Error ? e.message : 'Could not start the creative sync');
    }
  };

  const review = useCallback(
    async (creative: Creative, status: ReviewStatus, comment?: string): Promise<boolean> => {
      if (!workspaceId) return false;
      try {
        const updated = await apiJson<Creative>(
          `/api/workspaces/${workspaceId}/creatives/${creative.id}/review`,
          {
            method: 'PATCH',
            body: JSON.stringify({ review_status: status, review_comment: comment ?? '' }),
          },
        );
        setCreatives((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
        void loadSummary();
        return true;
      } catch {
        return false;
      }
    },
    [workspaceId, loadSummary],
  );

  if (workspaceLoading) {
    return <div className="p-8 text-sm text-gray-500">Loading workspace…</div>;
  }
  if (!workspaceId) {
    return <div className="p-8 text-sm text-gray-500">Select a workspace to view its ad creatives.</div>;
  }

  const hasMore = creatives.length < total;

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 py-5">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Link href="/" className="text-gray-400 hover:text-gray-600" aria-label="Back to dashboard">
                <ArrowLeft size={18} />
              </Link>
              <div>
                <h1 className="text-lg font-semibold text-gray-900">Ad Creatives</h1>
                <p className="text-xs text-gray-500">
                  {summary ? `${summary.total} creatives in ${active?.name ?? 'this workspace'}` : 'Loading…'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border border-gray-200 overflow-hidden">
                {VIEW_OPTIONS.map(({ id, label, Icon }) => (
                  <button
                    key={id}
                    onClick={() => setView(id)}
                    title={label}
                    aria-label={label}
                    className={`px-2.5 py-1.5 transition-colors ${
                      view === id ? 'bg-gray-900 text-white' : 'text-gray-500 hover:bg-gray-100'
                    }`}
                  >
                    <Icon size={16} />
                  </button>
                ))}
              </div>

              <button
                onClick={startSync}
                disabled={syncing}
                className="flex items-center gap-2 text-sm bg-gray-900 text-white px-3.5 py-2 rounded-lg font-medium hover:bg-gray-800 disabled:opacity-60"
              >
                <RefreshCcw size={15} className={syncing ? 'animate-spin' : undefined} />
                {syncing ? 'Syncing…' : 'Sync creatives'}
              </button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 mt-4">
            <FilterChip active={platform === 'all'} onClick={() => setPlatform('all')} label="All platforms" />
            {PLATFORMS.map((p) => (
              <FilterChip
                key={p}
                active={platform === p}
                onClick={() => setPlatform(p)}
                label={platformLabel(p)}
                count={summary?.by_platform[p]}
              />
            ))}

            <span className="w-px h-5 bg-gray-200 mx-1" />

            {REVIEW_FILTERS.map((f) => (
              <FilterChip
                key={f.id}
                active={reviewFilter === f.id}
                onClick={() => setReviewFilter(f.id)}
                label={f.label}
                count={f.id === 'all' ? undefined : summary?.by_review_status[f.id]}
              />
            ))}

            <div className="relative ml-auto">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search headline, copy, campaign…"
                className="text-sm border border-gray-200 rounded-lg pl-8 pr-3 py-1.5 w-64 focus:outline-none focus:ring-1 focus:ring-gray-400"
              />
            </div>
          </div>

          {syncStatus && (
            <div
              className={`mt-3 text-xs rounded-lg px-3 py-2 border ${
                syncStatus.status === 'failed'
                  ? 'bg-red-50 border-red-200 text-red-700'
                  : 'bg-blue-50 border-blue-200 text-blue-700'
              }`}
            >
              {syncStatus.status === 'failed'
                ? syncStatus.error_message || 'Creative sync failed.'
                : `${syncStatus.current_step || syncStatus.status} — ${syncStatus.progress_percent}%`}
            </div>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        {error && (
          <div className="mb-4 text-sm bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2">
            {error}
          </div>
        )}

        {!loading && creatives.length === 0 ? (
          <EmptyState hasFilters={platform !== 'all' || reviewFilter !== 'all' || Boolean(search)} />
        ) : (
          <div
            className={
              view === 'card'
                ? 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5'
                : 'flex flex-col gap-3'
            }
          >
            {creatives.map((c) => (
              <CreativeCard
                key={c.id}
                creative={c}
                view={view}
                onReview={(status, comment) => review(c, status, comment)}
              />
            ))}
          </div>
        )}

        {loading && <p className="text-sm text-gray-500 mt-6">Loading creatives…</p>}

        {hasMore && !loading && (
          <div className="flex justify-center mt-6">
            <button
              onClick={() => load(offset + PAGE_SIZE, false)}
              className="text-sm border border-gray-300 rounded-lg px-4 py-2 hover:bg-gray-100"
            >
              Load more ({creatives.length} of {total})
            </button>
          </div>
        )}
      </main>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-3 py-1.5 rounded-full font-medium border transition-colors ${
        active
          ? 'bg-gray-900 border-gray-900 text-white'
          : 'border-gray-200 text-gray-600 hover:bg-gray-100'
      }`}
    >
      {label}
      {typeof count === 'number' && <span className="ml-1.5 opacity-60">{count}</span>}
    </button>
  );
}

function EmptyState({ hasFilters }: { hasFilters: boolean }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg py-16 text-center">
      <p className="text-sm font-medium text-gray-900">
        {hasFilters ? 'No creatives match these filters.' : 'No creatives yet.'}
      </p>
      <p className="text-xs text-gray-500 mt-1">
        {hasFilters
          ? 'Clear a filter to see more.'
          : 'Connect Meta, Google Ads or Microsoft Ads, then run a sync to pull in ad creatives.'}
      </p>
    </div>
  );
}
