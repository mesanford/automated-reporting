"use client";

import React, { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { apiJson } from '@/lib/api';
import { useWorkspace } from '@/lib/workspace';
import ReportPrintView, { PrintableReport } from '@/components/ReportPrintView';

/**
 * Operator-facing print preview. Renders the same component the public share
 * route uses, so what you check here is what the recipient gets.
 */
export default function PrintableReportPage() {
  const params = useParams<{ id: string }>();
  const { active } = useWorkspace();
  const [report, setReport] = useState<PrintableReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params?.id) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiJson<PrintableReport>(`/api/reports/${params.id}`);
        if (!cancelled) setReport(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load report');
      }
    })();
    return () => { cancelled = true; };
  }, [params?.id]);

  if (error) return <Shell><p className="text-red-600">{error}</p></Shell>;
  if (!report) return <Shell><p className="text-slate-400">Loading…</p></Shell>;

  return (
    <ReportPrintView
      report={report}
      currency={active?.base_currency ?? 'USD'}
      workspaceName={active?.name}
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
