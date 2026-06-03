"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { apiJson } from './api';
import { useAuth } from './auth';

export interface Workspace {
  id: number;
  name: string;
  slug: string;
  role: string | null;
  base_currency: string;
  created_at: string | null;
}

interface WorkspaceState {
  workspaces: Workspace[];
  active: Workspace | null;
  loading: boolean;
  setActive: (w: Workspace) => void;
  refresh: () => Promise<void>;
  createWorkspace: (name: string) => Promise<Workspace>;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

const STORAGE_KEY = 'antigravity:active_workspace_id';

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [active, setActiveState] = useState<Workspace | null>(null);
  const [loading, setLoading] = useState(true);

  const setActive = useCallback((w: Workspace) => {
    setActiveState(w);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, String(w.id));
      window.localStorage.setItem('antigravity:active_workspace_currency', w.base_currency);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const list = await apiJson<Workspace[]>('/api/workspaces');
      setWorkspaces(list);

      // Preserve the persisted active workspace if it's still in the list.
      const persisted =
        typeof window !== 'undefined'
          ? Number(window.localStorage.getItem(STORAGE_KEY) || 0)
          : 0;
      const next =
        list.find((w) => w.id === persisted) ?? list[0] ?? null;
      setActiveState(next);
      if (next && typeof window !== 'undefined') {
        window.localStorage.setItem(STORAGE_KEY, String(next.id));
        window.localStorage.setItem('antigravity:active_workspace_currency', next.base_currency);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      // Dev mode (no Firebase) also runs through here; the backend
      // auto-provisions Personal on the first call.
    }
    void refresh();
  }, [authLoading, user, refresh]);

  const createWorkspace = useCallback(
    async (name: string): Promise<Workspace> => {
      const w = await apiJson<Workspace>('/api/workspaces', {
        method: 'POST',
        body: JSON.stringify({ name }),
      });
      await refresh();
      setActive(w);
      return w;
    },
    [refresh, setActive],
  );

  return (
    <WorkspaceContext.Provider
      value={{ workspaces, active, loading, setActive, refresh, createWorkspace }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used inside <WorkspaceProvider>');
  return ctx;
}

/** Read the active workspace id without subscribing — for `api.ts`. */
export function getActiveWorkspaceId(): number | null {
  if (typeof window === 'undefined') return null;
  const stored = Number(window.localStorage.getItem(STORAGE_KEY) || 0);
  return stored || null;
}
