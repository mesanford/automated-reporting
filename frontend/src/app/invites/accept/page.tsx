"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Zap, Check, AlertTriangle, ArrowRight, Loader2 } from 'lucide-react';
import { apiJson } from '@/lib/api';
import { useWorkspace } from '@/lib/workspace';

interface AcceptResponse {
  status: string;
  workspace_id: number;
  role: string;
}

type State =
  | { kind: 'missing-token' }
  | { kind: 'accepting' }
  | { kind: 'success'; result: AcceptResponse }
  | { kind: 'error'; message: string };

export default function AcceptInvitePage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refresh, workspaces, setActive } = useWorkspace();
  const [state, setState] = useState<State>(() =>
    params.get('token') ? { kind: 'accepting' } : { kind: 'missing-token' },
  );

  useEffect(() => {
    const token = params.get('token');
    if (!token) return;

    let cancelled = false;
    (async () => {
      try {
        const result = await apiJson<AcceptResponse>(
          `/api/workspaces/invites/accept?token=${encodeURIComponent(token)}`,
          { method: 'POST' },
        );
        if (cancelled) return;
        // Pull the workspaces list so the new workspace is in `workspaces`.
        await refresh();
        setState({ kind: 'success', result });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'Failed to accept invite.',
        });
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.get('token')]);

  // When the accept succeeds, also switch the active workspace.
  useEffect(() => {
    if (state.kind !== 'success') return;
    const w = workspaces.find((w) => w.id === state.result.workspace_id);
    if (w) setActive(w);
  }, [state, workspaces, setActive]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="max-w-md w-full border border-slate-100 bg-white rounded-3xl p-10 shadow-sm text-center">
        <div className="w-14 h-14 mx-auto bg-gradient-to-br from-blue-600 to-indigo-700 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-200">
          <Zap className="text-white w-7 h-7 fill-white" />
        </div>

        {state.kind === 'missing-token' && <MissingToken />}
        {state.kind === 'accepting' && <Accepting />}
        {state.kind === 'success' && (
          <Success result={state.result} onGo={() => router.push('/')} />
        )}
        {state.kind === 'error' && <ErrorView message={state.message} />}
      </div>
    </div>
  );
}

function MissingToken() {
  return (
    <>
      <h2 className="text-2xl font-black text-slate-900 mt-6">Invite link is incomplete</h2>
      <p className="text-sm text-slate-500 mt-2">
        This page needs a <code className="font-mono">?token=…</code> parameter. Ask whoever
        invited you to resend the link.
      </p>
      <Link
        href="/"
        className="inline-flex items-center gap-2 mt-6 px-4 py-2 rounded-xl bg-blue-600 text-white text-sm font-bold hover:bg-blue-700"
      >
        Back to dashboard
        <ArrowRight size={14} />
      </Link>
    </>
  );
}

function Accepting() {
  return (
    <>
      <h2 className="text-2xl font-black text-slate-900 mt-6">Accepting invite…</h2>
      <p className="text-sm text-slate-500 mt-2">Adding you to the workspace.</p>
      <Loader2 className="mx-auto mt-6 animate-spin text-blue-600" size={24} />
    </>
  );
}

function Success({
  result,
  onGo,
}: {
  result: AcceptResponse;
  onGo: () => void;
}) {
  return (
    <>
      <div className="mx-auto mt-6 w-12 h-12 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center">
        <Check size={22} />
      </div>
      <h2 className="text-2xl font-black text-slate-900 mt-4">You&apos;re in</h2>
      <p className="text-sm text-slate-500 mt-2">
        Joined as <span className="font-semibold text-slate-700">{result.role}</span>. The new
        workspace is now active.
      </p>
      <button
        onClick={onGo}
        className="inline-flex items-center gap-2 mt-6 px-4 py-2 rounded-xl bg-blue-600 text-white text-sm font-bold hover:bg-blue-700"
      >
        Go to dashboard
        <ArrowRight size={14} />
      </button>
    </>
  );
}

function ErrorView({ message }: { message: string }) {
  return (
    <>
      <div className="mx-auto mt-6 w-12 h-12 rounded-full bg-red-100 text-red-600 flex items-center justify-center">
        <AlertTriangle size={22} />
      </div>
      <h2 className="text-2xl font-black text-slate-900 mt-4">Couldn&apos;t accept the invite</h2>
      <p className="text-sm text-red-600 mt-2">{message}</p>
      <p className="text-xs text-slate-400 mt-3">
        Invites expire after 7 days and can only be used once. Ask the workspace owner to send a new one.
      </p>
      <Link
        href="/"
        className="inline-flex items-center gap-2 mt-6 px-4 py-2 rounded-xl bg-slate-100 text-slate-700 text-sm font-bold hover:bg-slate-200"
      >
        Back to dashboard
      </Link>
    </>
  );
}
