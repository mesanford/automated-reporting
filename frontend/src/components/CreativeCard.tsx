"use client";

import React, { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api';

export type ViewMode = 'card' | 'compact' | 'expanded';
export type ReviewStatus = 'keep' | 'remove' | 'change';

export interface Creative {
  id: number;
  workspace_id: number;
  platform: string;
  ad_id: string;
  account_id: string | null;
  ad_name: string | null;
  headline: string | null;
  ad_text: string | null;
  campaign_name: string | null;
  ad_group_name: string | null;
  creative_type: string | null;
  group_type: string | null;
  final_url: string | null;
  /** API path for the mirrored asset, or null when there is no media. */
  asset_url: string | null;
  /** Set instead of asset_url for YouTube video assets. */
  embed_url: string | null;
  review_status: ReviewStatus | null;
  review_comment: string | null;
  updated_at: string | null;
}

const PLATFORM_LABEL: Record<string, string> = {
  meta: 'Meta',
  google: 'Google',
  microsoft: 'Microsoft',
};

export function platformLabel(platform: string): string {
  return PLATFORM_LABEL[platform] ?? platform;
}

// ── Media ─────────────────────────────────────────────────────────────────────
//
// Mirrored assets are private and served through an authenticated endpoint, so
// they cannot go straight into an <img src>. Fetch them with the auth header
// and hand the element a blob URL instead.

function useAuthedAsset(assetUrl: string | null) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [contentType, setContentType] = useState<string>('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!assetUrl) return;
    let revoked = false;
    let created: string | null = null;

    (async () => {
      try {
        const res = await apiFetch(assetUrl);
        if (!res.ok) throw new Error(`asset ${res.status}`);
        const blob = await res.blob();
        if (revoked) return;
        created = URL.createObjectURL(blob);
        setContentType(blob.type);
        setObjectUrl(created);
      } catch {
        if (!revoked) setFailed(true);
      }
    })();

    return () => {
      revoked = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [assetUrl]);

  return { objectUrl, contentType, failed };
}

function MediaBlock({ creative, className }: { creative: Creative; className: string }) {
  const { objectUrl, contentType, failed } = useAuthedAsset(creative.asset_url);

  if (creative.embed_url) {
    return (
      <iframe
        src={creative.embed_url}
        className={className}
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
        allowFullScreen
      />
    );
  }

  if (!creative.asset_url || failed) {
    return (
      <div className={`${className} flex items-center justify-center bg-gray-100 text-gray-400 italic text-xs`}>
        {failed ? 'Media unavailable' : 'No Media'}
      </div>
    );
  }

  if (!objectUrl) {
    return <div className={`${className} bg-gray-100 animate-pulse`} />;
  }

  if (contentType.startsWith('video/')) {
    return <video src={objectUrl} className={`${className} object-contain`} controls muted />;
  }

  // eslint-disable-next-line @next/next/no-img-element
  return <img src={objectUrl} alt={creative.ad_name ?? creative.ad_id} className={`${className} object-contain`} />;
}

// ── Review bar ────────────────────────────────────────────────────────────────

function ReviewBar({
  creative,
  onReview,
}: {
  creative: Creative;
  onReview: (status: ReviewStatus, comment?: string) => Promise<boolean>;
}) {
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (status: ReviewStatus, reviewComment?: string) => {
    setSaving(true);
    setError(null);
    const ok = await onReview(status, reviewComment);
    if (!ok) setError('Could not save review');
    setSaving(false);
    return ok;
  };

  const startChange = () => {
    setComment(creative.review_comment ?? '');
    setCommenting(true);
  };

  const submitComment = async () => {
    if (await save('change', comment)) setCommenting(false);
  };

  const current = creative.review_status;

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-400 uppercase font-medium mr-1">Review</span>

        <button
          onClick={() => save('keep')}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === 'keep'
              ? 'bg-green-500 border-green-500 text-white'
              : 'border-green-400 text-green-700 hover:bg-green-50'
          }`}
        >
          Keep
        </button>

        <button
          onClick={() => save('remove')}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === 'remove'
              ? 'bg-red-500 border-red-500 text-white'
              : 'border-red-400 text-red-700 hover:bg-red-50'
          }`}
        >
          Remove
        </button>

        <button
          onClick={startChange}
          disabled={saving}
          className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-colors ${
            current === 'change'
              ? 'bg-yellow-400 border-yellow-400 text-white'
              : 'border-yellow-400 text-yellow-700 hover:bg-yellow-50'
          }`}
        >
          Change
        </button>
      </div>

      {error && (
        <p className="mt-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2.5 py-1.5">
          {error}
        </p>
      )}

      {current === 'change' && creative.review_comment && !commenting && (
        <div
          className="mt-2 text-xs text-yellow-800 bg-yellow-50 border border-yellow-200 rounded px-2.5 py-1.5 cursor-pointer hover:bg-yellow-100 transition-colors"
          onClick={startChange}
          title="Click to edit comment"
        >
          {creative.review_comment}
        </div>
      )}

      {commenting && (
        <div className="mt-2 flex flex-col gap-1.5">
          <textarea
            autoFocus
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Describe the requested change…"
            rows={2}
            className="w-full text-xs border border-yellow-300 rounded px-2.5 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-yellow-400"
          />
          <div className="flex gap-1.5">
            <button
              onClick={submitComment}
              disabled={saving || !comment.trim()}
              className="text-xs bg-yellow-400 text-white px-3 py-1 rounded font-medium hover:bg-yellow-500 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => setCommenting(false)}
              className="text-xs text-gray-500 px-3 py-1 rounded hover:bg-gray-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Shared bits ───────────────────────────────────────────────────────────────

function PlatformBadge({ platform, size = 'sm' }: { platform: string; size?: 'xs' | 'sm' }) {
  return (
    <span
      className={`inline-flex items-center bg-gray-900 text-white font-bold rounded ${
        size === 'xs' ? 'text-[9px] px-1.5 py-0.5' : 'text-[10px] px-2 py-1'
      }`}
    >
      {platformLabel(platform)}
    </span>
  );
}

function LandingLink({ url }: { url: string | null }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 hover:text-blue-800 text-xs font-medium whitespace-nowrap"
    >
      View →
    </a>
  );
}

function statusBorderClass(status: ReviewStatus | null) {
  if (status === 'keep') return 'border-green-400';
  if (status === 'remove') return 'border-red-400';
  if (status === 'change') return 'border-yellow-400';
  return 'border-gray-200';
}

interface ViewProps {
  creative: Creative;
  onReview: (status: ReviewStatus, comment?: string) => Promise<boolean>;
}

function CardView({ creative, onReview }: ViewProps) {
  return (
    <div
      className={`bg-white rounded-lg shadow-md overflow-hidden border-2 hover:shadow-lg transition-shadow duration-300 ${statusBorderClass(
        creative.review_status,
      )}`}
    >
      <div className="relative h-48 bg-gray-100 flex items-center justify-center overflow-hidden">
        <MediaBlock creative={creative} className="w-full h-full" />
        <div className="absolute top-2 right-2">
          <PlatformBadge platform={creative.platform} />
        </div>
      </div>

      <div className="p-4">
        <h3 className="text-sm font-semibold text-gray-900 line-clamp-1 mb-1">
          {creative.headline ?? creative.ad_name ?? creative.ad_id}
        </h3>
        <p className="text-xs text-gray-600 line-clamp-3 h-12 mb-3">
          {creative.ad_text ?? 'No description available.'}
        </p>

        <div className="flex justify-between items-center pt-2 border-t border-gray-100">
          <span className="text-[10px] text-gray-400 uppercase font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>

        <ReviewBar creative={creative} onReview={onReview} />
      </div>
    </div>
  );
}

function CompactView({ creative, onReview }: ViewProps) {
  const headline = creative.headline ?? creative.ad_name ?? creative.ad_id;

  return (
    <div
      className={`bg-white border-l-4 border-r border-t border-b rounded-lg px-4 py-3 hover:bg-gray-50 transition-colors ${statusBorderClass(
        creative.review_status,
      )}`}
    >
      <div className="flex items-center gap-4">
        <div className="w-12 h-12 flex-shrink-0 rounded overflow-hidden bg-gray-100">
          <MediaBlock creative={creative} className="w-full h-full" />
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{headline}</p>
          {creative.ad_text && <p className="text-xs text-gray-500 truncate mt-0.5">{creative.ad_text}</p>}
          {creative.review_status === 'change' && creative.review_comment && (
            <p className="text-xs text-yellow-700 truncate mt-0.5 italic">
              &ldquo;{creative.review_comment}&rdquo;
            </p>
          )}
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          <PlatformBadge platform={creative.platform} size="xs" />
          <span className="text-[10px] text-gray-400 hidden sm:block">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>
      </div>

      <ReviewBar creative={creative} onReview={onReview} />
    </div>
  );
}

function ExpandedView({ creative, onReview }: ViewProps) {
  const headline = creative.headline ?? creative.ad_name ?? creative.ad_id;

  return (
    <div
      className={`bg-white border-2 rounded-lg overflow-hidden hover:shadow-md transition-shadow flex gap-0 ${statusBorderClass(
        creative.review_status,
      )}`}
    >
      <div className="w-72 flex-shrink-0 bg-gray-100 flex items-center justify-center">
        <MediaBlock creative={creative} className="w-full h-full min-h-52 max-h-72 object-contain" />
      </div>

      <div className="flex-1 p-6 flex flex-col justify-between min-w-0">
        <div>
          <div className="flex items-start justify-between gap-3 mb-3">
            <h3 className="text-base font-semibold text-gray-900 leading-snug">{headline}</h3>
            <PlatformBadge platform={creative.platform} />
          </div>

          {creative.ad_text && (
            <p className="text-sm text-gray-700 leading-relaxed line-clamp-6">{creative.ad_text}</p>
          )}

          {creative.campaign_name && (
            <p className="text-xs text-gray-400 mt-3">Campaign: {creative.campaign_name}</p>
          )}
          {creative.ad_name && creative.ad_name !== headline && (
            <p className="text-xs text-gray-400 mt-1">Ad name: {creative.ad_name}</p>
          )}
        </div>

        <div className="flex items-center justify-between pt-4 border-t border-gray-100 mt-4">
          <span className="text-xs text-gray-400 font-medium">ID: {creative.ad_id}</span>
          <LandingLink url={creative.final_url} />
        </div>

        <ReviewBar creative={creative} onReview={onReview} />
      </div>
    </div>
  );
}

function CreativeCard({ creative, view = 'card', onReview }: ViewProps & { view?: ViewMode }) {
  if (view === 'compact') return <CompactView creative={creative} onReview={onReview} />;
  if (view === 'expanded') return <ExpandedView creative={creative} onReview={onReview} />;
  return <CardView creative={creative} onReview={onReview} />;
}

export default React.memo(CreativeCard, (prev, next) => {
  if (prev.view !== next.view) return false;
  const a = prev.creative;
  const b = next.creative;
  return (
    a.id === b.id &&
    a.ad_name === b.ad_name &&
    a.headline === b.headline &&
    a.ad_text === b.ad_text &&
    a.asset_url === b.asset_url &&
    a.embed_url === b.embed_url &&
    a.final_url === b.final_url &&
    a.review_status === b.review_status &&
    a.review_comment === b.review_comment
  );
});
