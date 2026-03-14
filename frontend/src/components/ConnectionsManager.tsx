"use client";

import React, { useState, useEffect } from 'react';
import { Plus, RefreshCcw, CheckCircle2, Zap } from 'lucide-react';

interface Connection {
  id: number;
  platform: string;
  account_name: string;
  account_id: string;
  is_active: number;
}

interface ConnectionsManagerProps {
  onSyncComplete: (reportData: unknown) => void;
  isSyncing: boolean;
  setIsSyncing: (val: boolean) => void;
  setUploadStep: (step: string) => void;
}

const PLATFORMS = [
  { id: 'google', name: 'Google Ads', color: 'blue' },
  { id: 'meta', name: 'Meta Ads', color: 'indigo' },
  { id: 'linkedin', name: 'LinkedIn Ads', color: 'sky' },
  { id: 'tiktok', name: 'TikTok Ads', color: 'pink' },
  { id: 'microsoft', name: 'Microsoft Ads', color: 'emerald' }
];

export const ConnectionsManager: React.FC<ConnectionsManagerProps> = ({ 
  onSyncComplete, 
  isSyncing, 
  setIsSyncing,
  setUploadStep 
}) => {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [showAddModal, setShowAddModal] = useState(false);

  useEffect(() => {
    fetchConnections();
  }, []);

  const fetchConnections = async () => {
    try {
      const response = await fetch('http://localhost:8000/api/connections');
      const data = await response.json();
      setConnections(data);
    } catch (error) {
      console.error('Error fetching connections:', error);
    }
  };

  const addMockConnection = async (platform: string) => {
    try {
      const accountName = `${platform.charAt(0).toUpperCase() + platform.slice(1)} - Main Account`;
      const response = await fetch(`http://localhost:8000/api/connections?platform=${platform}&account_name=${encodeURIComponent(accountName)}`, {
        method: 'POST'
      });
      if (response.ok) {
        fetchConnections();
        setShowAddModal(false);
      }
    } catch (error) {
      console.error('Error adding connection:', error);
    }
  };

  const syncNow = async (id: number) => {
    setIsSyncing(true);
    setUploadStep('Connecting to API...');
    try {
      const response = await fetch(`http://localhost:8000/api/sync/${id}`, { method: 'POST' });
      const result = await response.json();
      if (result.status === 'success') {
        onSyncComplete(result);
      } else {
        alert(result.message || 'Sync failed');
      }
    } catch (error) {
      console.error('Sync error:', error);
      alert('Sync failed. Is the backend running?');
    } finally {
      setIsSyncing(false);
      setUploadStep('');
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-xl font-bold text-slate-800">Connected Accounts</h3>
        <button 
          onClick={() => setShowAddModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-bold hover:bg-blue-700 transition-colors shadow-lg shadow-blue-100"
        >
          <Plus size={16} />
          Connect New
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {connections.length === 0 ? (
          <div className="col-span-full py-12 text-center border-2 border-dashed border-slate-100 rounded-3xl">
            <p className="text-slate-400 font-medium">No accounts connected yet.</p>
            <p className="text-xs text-slate-400 mt-1">Connect a platform to enable one-click syncing.</p>
          </div>
        ) : (
          connections.map((conn) => (
            <div key={conn.id} className="p-6 bg-white border border-slate-100 rounded-[2rem] shadow-sm hover:border-blue-100 transition-all group">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center
                    ${conn.platform === 'google' ? 'bg-blue-50 text-blue-600' : 
                      conn.platform === 'meta' ? 'bg-indigo-50 text-indigo-600' :
                      conn.platform === 'linkedin' ? 'bg-sky-50 text-sky-600' :
                      conn.platform === 'microsoft' ? 'bg-emerald-50 text-emerald-600' :
                      'bg-pink-50 text-pink-600'}
                  `}>
                    <Zap size={20} className="fill-current" />
                  </div>
                  <div>
                    <p className="text-sm font-black text-slate-800 uppercase tracking-tight">{conn.platform}</p>
                    <p className="text-xs font-bold text-slate-400">{conn.account_id}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2 text-emerald-500 bg-emerald-50 px-2 py-1 rounded-lg">
                  <CheckCircle2 size={12} />
                  <span className="text-[10px] font-black uppercase">Active</span>
                </div>
              </div>
              
              <h4 className="text-lg font-bold text-slate-700 mb-6">{conn.account_name}</h4>
              
              <button 
                onClick={() => syncNow(conn.id)}
                disabled={isSyncing}
                className="w-full h-12 border-2 border-slate-50 hover:border-blue-600 hover:text-blue-600 rounded-2xl flex items-center justify-center gap-2 text-sm font-bold text-slate-400 transition-all group-hover:border-blue-100"
              >
                <RefreshCcw size={16} className={isSyncing ? 'animate-spin text-blue-600' : ''} />
                {isSyncing ? 'Syncing...' : 'Sync Now'}
              </button>
            </div>
          ))
        )}
      </div>

      {showAddModal && (
        <div className="fixed inset-0 z-[100] bg-slate-900/40 backdrop-blur-sm flex items-center justify-center p-6">
          <div className="bg-white w-full max-w-md rounded-[2.5rem] shadow-2xl p-8 animate-in fade-in zoom-in duration-300">
            <h3 className="text-2xl font-black text-slate-900 mb-2">Connect Platform</h3>
            <p className="text-slate-500 font-medium mb-8">Select a platform to connect (Mock Auth Flow)</p>
            
            <div className="grid grid-cols-2 gap-4 mb-8">
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => addMockConnection(p.id)}
                  className="p-6 border-2 border-slate-50 hover:border-blue-600 hover:bg-blue-50/30 rounded-3xl flex flex-col items-center gap-3 transition-all text-center group"
                >
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center bg-${p.color}-50 text-${p.color}-600 group-hover:scale-110 transition-transform`}>
                    <Zap size={24} className="fill-current" />
                  </div>
                  <span className="text-sm font-black text-slate-700">{p.name}</span>
                </button>
              ))}
            </div>
            
            <button 
              onClick={() => setShowAddModal(false)}
              className="w-full py-4 text-slate-400 font-bold hover:text-slate-600 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
