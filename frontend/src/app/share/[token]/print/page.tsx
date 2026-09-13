"use client";

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { AlertTriangle, Lock } from 'lucide-react';
import ReportPrintView, { PrintableReport } from '@/components/ReportPrintView';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

interface ShareResponse {
  report: PrintableReport;
  workspace?: { name: string | null; base_currency: string | null };
  share: { expires_at: string; view_count: number };
}

type State =
  | { kind: 'loading' }
  | { kind: 'gone'; message: string }
  | { kind: 'notfound' }
  | { kind: 'ok'; data: ShareResponse };

/**
 * Printable view of a shared report. Anonymous by design — it reads the same
 * public share endpoint the share page does, so a recipient can produce a PDF
 * without an account. Revoked and expired links are refused here exactly as
 * they are on the share page; this route is not a way around that.
 */
export default function SharePrintPage() {
  const params = useParams<{ token: string }>();
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    if (!params?.token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/share/${params.token}`);
        if (cancelled) return;
        if (res.status === 410) {
          const body = await res.json().catch(() => ({}));
          setState({ kind: 'gone', message: body.detail || 'This link is no longer valid.' });
          return;
        }
        if (!res.ok) {
          setState({ kind: 'notfound' });
          return;
        }
        setState({ kind: 'ok', data: (await res.json()) as ShareResponse });
      } catch {
        if (!cancelled) setState({ kind: 'notfound' });
      }
    })();
    return () => { cancelled = true; };
  }, [params?.token]);

  if (state.kind === 'loading') return <Shell><p className="text-slate-400">Loading…</p></Shell>;
  if (state.kind === 'gone') {
    return <ErrorState icon={<Lock size={28} />} title="Link no longer valid" message={state.message} />;
  }
  if (state.kind === 'notfound') {
    return (
      <ErrorState
        icon={<AlertTriangle size={28} />}
        title="Link not found"
        message="The recipient may have a typo, or the link was never minted."
      />
    );
  }

  return (
    <ReportPrintView
      report={state.data.report}
      currency={state.data.workspace?.base_currency ?? 'USD'}
      workspaceName={state.data.workspace?.name}
      autoPrint
    />
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white min-h-screen">
      <main className="max-w-4xl mx-auto px-6 py-10">{children}</main>
    </div>
  );
}

function ErrorState({
  icon, title, message,
}: { icon: React.ReactNode; title: string; message: string }) {
  return (
    <Shell>
      <div className="max-w-md mx-auto text-center py-20">
        <div className="w-14 h-14 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center mx-auto">
          {icon}
        </div>
        <h2 className="text-2xl font-black text-slate-900 mt-4">{title}</h2>
        <p className="text-sm text-slate-500 mt-2">{message}</p>
      </div>
    </Shell>
  );
}
