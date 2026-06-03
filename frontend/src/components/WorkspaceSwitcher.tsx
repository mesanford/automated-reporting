"use client";

import React, { useState } from 'react';
import { Briefcase, ChevronDown, Plus } from 'lucide-react';
import { useWorkspace } from '@/lib/workspace';

export function WorkspaceSwitcher() {
  const { workspaces, active, setActive, createWorkspace, loading } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');

  if (loading) {
    return <span className="text-xs text-slate-400">Loading workspaces…</span>;
  }

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await createWorkspace(newName.trim());
      setNewName('');
      setOpen(false);
      // Reload so all data refetches under the new workspace.
      window.location.reload();
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-100 hover:border-blue-200 text-sm font-semibold text-slate-700"
      >
        <Briefcase size={14} className="text-blue-600" />
        <span className="truncate max-w-[140px]">{active?.name ?? 'No workspace'}</span>
        <ChevronDown size={14} className="text-slate-400" />
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 bg-white border border-slate-100 rounded-xl shadow-lg p-2 z-50">
          <div className="text-[10px] uppercase tracking-wider text-slate-400 px-3 py-2 font-bold">
            Workspaces
          </div>
          <div className="max-h-60 overflow-y-auto">
            {workspaces.length === 0 && (
              <div className="px-3 py-2 text-sm text-slate-400">None yet.</div>
            )}
            {workspaces.map((w) => (
              <button
                key={w.id}
                onClick={() => {
                  setActive(w);
                  setOpen(false);
                  window.location.reload();
                }}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between ${
                  active?.id === w.id
                    ? 'bg-blue-50 text-blue-700'
                    : 'hover:bg-slate-50 text-slate-700'
                }`}
              >
                <span className="truncate">{w.name}</span>
                <span className="text-[10px] uppercase tracking-wider text-slate-400">
                  {w.role ?? 'member'}
                </span>
              </button>
            ))}
          </div>
          <div className="border-t border-slate-100 mt-2 pt-2 px-3 pb-2">
            <div className="flex gap-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="New workspace name"
                className="flex-1 px-2 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:border-blue-400"
              />
              <button
                onClick={() => void handleCreate()}
                disabled={creating || !newName.trim()}
                className="px-2 py-1.5 rounded-lg bg-blue-600 text-white text-sm font-bold disabled:bg-slate-300"
                aria-label="Create workspace"
              >
                <Plus size={14} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
