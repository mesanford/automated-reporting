"use client";

import React, { useState, useEffect } from 'react';
import { AlertCircle, CheckCircle2, Clock } from 'lucide-react';

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

interface SyncStatus {
  status: 'idle' | 'pending' | 'running' | 'completed' | 'failed';
  progress_percent: number;
  current_step: string;
  total_steps: number;
  accounts_synced: number;
  total_accounts: number;
  error_message?: string;
  recent_logs: string[];
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  retry_count: number;
  max_retries: number;
}

interface SyncMonitorProps {
  connectionId: number;
  isVisible: boolean;
}

export const SyncMonitor: React.FC<SyncMonitorProps> = ({ connectionId, isVisible }) => {
  const [status, setStatus] = useState<SyncStatus | null>(null);

  useEffect(() => {
    if (!isVisible || !connectionId) {
      return;
    }

    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/api/sync/${connectionId}/status`);
        if (response.ok) {
          const data = await response.json();
          setStatus(data);
        }
      } catch (error) {
        console.error('Error polling sync status:', error);
      }
    }, 1000);

    return () => clearInterval(pollInterval);
  }, [connectionId, isVisible]);

  if (!isVisible || !status || status.status === 'idle') {
    return null;
  }

  const isRunning = status.status === 'running' || status.status === 'pending';
  const isFailed = status.status === 'failed';
  const isCompleted = status.status === 'completed';

  return (
    <div className="fixed bottom-6 right-6 z-[110] w-96 bg-background rounded-[2rem] border border-slate-200 shadow-2xl p-6 space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-300">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {isRunning && (
            <div className="w-3 h-3 bg-blue-600 rounded-full animate-pulse" />
          )}
          {isCompleted && (
            <CheckCircle2 size={16} className="text-emerald-600" />
          )}
          {isFailed && (
            <AlertCircle size={16} className="text-red-600" />
          )}
          <span className="text-sm font-bold uppercase tracking-widest text-slate-700">
            {status.status === 'running' ? 'Syncing...' : status.status === 'completed' ? 'Completed' : 'Failed'}
          </span>
        </div>
      </div>

      {/* Progress Bar */}
      {isRunning && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs font-bold text-slate-500">
            <span>{status.current_step}</span>
            <span>{status.progress_percent}%</span>
          </div>
          <div className="w-full h-2 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-blue-500 to-indigo-600 transition-all duration-500"
              style={{ width: `${status.progress_percent}%` }}
            />
          </div>
        </div>
      )}

      {/* Account Progress */}
      {status.total_accounts > 0 && isRunning && (
        <div className="text-xs text-slate-600 font-medium">
          Synced {status.accounts_synced} of {status.total_accounts} accounts
        </div>
      )}

      {/* Error Message */}
      {isFailed && status.error_message && (
        <div className="bg-red-50 border border-red-100 rounded-xl p-3">
          <p className="text-xs font-bold text-red-700">{status.error_message}</p>
          {status.retry_count < status.max_retries && (
            <p className="text-[10px] text-red-600 mt-1">
              Will retry: {status.retry_count}/{status.max_retries}
            </p>
          )}
        </div>
      )}

      {/* Recent Logs */}
      {status.recent_logs.length > 0 && (
        <div className="max-h-40 overflow-y-auto text-[10px] font-mono text-slate-500 bg-slate-50 rounded-lg p-2 space-y-0.5">
          {status.recent_logs
            .filter((log) => log.trim())
            .slice(-8)
            .map((log, idx) => (
              <div key={idx}>{log}</div>
            ))}
        </div>
      )}

      {/* Completion Status */}
      {isCompleted && (
        <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-3">
          <p className="text-xs font-bold text-emerald-700">Sync completed successfully</p>
          <p className="text-[10px] text-emerald-600 mt-1">
            Synced {status.accounts_synced} account{status.accounts_synced !== 1 ? 's' : ''}
          </p>
        </div>
      )}

      {/* Timestamps */}
      <div className="text-[10px] text-slate-400 space-y-0.5 border-t border-slate-100 pt-3">
        {status.started_at && (
          <div className="flex items-center gap-2">
            <Clock size={10} />
            Started: {new Date(status.started_at).toLocaleTimeString()}
          </div>
        )}
        {status.completed_at && (
          <div className="flex items-center gap-2">
            <CheckCircle2 size={10} />
            Completed: {new Date(status.completed_at).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  );
};
