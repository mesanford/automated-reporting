"use client";

import React from 'react';
import { usePathname } from 'next/navigation';
import { Zap, LogIn, AlertTriangle } from 'lucide-react';
import { useAuth } from '@/lib/auth';

const DEV_USER_ID = process.env.NEXT_PUBLIC_DEV_USER_ID ?? '';

/** Routes that the AuthGate must never block. The public share viewer is
 * intentionally anonymous — that's the entire product feature. */
const PUBLIC_PATH_PREFIXES = ['/share/'];

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? '';
  const { user, loading, configured, signIn } = useAuth();

  // Public routes pass through without consulting auth state at all.
  if (PUBLIC_PATH_PREFIXES.some((p) => pathname.startsWith(p))) {
    return <>{children}</>;
  }

  // Dev path: no Firebase config, but NEXT_PUBLIC_DEV_USER_ID is set → skip the gate.
  if (!configured && DEV_USER_ID) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background text-foreground">
        <div className="text-sm font-semibold text-slate-400">Loading…</div>
      </div>
    );
  }

  if (!configured) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-6">
        <div className="max-w-md text-center space-y-4 border border-amber-200 bg-amber-50 rounded-2xl p-8">
          <AlertTriangle className="mx-auto text-amber-600" size={32} />
          <h2 className="text-lg font-black text-amber-900">Firebase not configured</h2>
          <p className="text-sm text-amber-800">
            Set <code className="font-mono">NEXT_PUBLIC_FIREBASE_*</code> in <code>.env.local</code>, or set{' '}
            <code className="font-mono">NEXT_PUBLIC_DEV_USER_ID</code> to bypass auth in local development.
          </p>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-6">
        <div className="max-w-sm w-full text-center space-y-6 border border-slate-100 bg-white rounded-3xl p-10 shadow-sm">
          <div className="w-14 h-14 mx-auto bg-gradient-to-br from-blue-600 to-indigo-700 rounded-2xl flex items-center justify-center shadow-lg shadow-blue-200">
            <Zap className="text-white w-7 h-7 fill-white" />
          </div>
          <div>
            <h2 className="text-2xl font-black text-slate-900">Sign in</h2>
            <p className="text-sm text-slate-500 mt-1">
              MM Sanford Internal Reporting
            </p>
          </div>
          <button
            onClick={() => void signIn()}
            className="w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-blue-600 text-white font-bold hover:bg-blue-700 transition-colors"
          >
            <LogIn size={16} />
            Continue with Google
          </button>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
