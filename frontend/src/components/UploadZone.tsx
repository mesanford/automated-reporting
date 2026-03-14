"use client";

import React, { useCallback, useState } from 'react';
import { useDropzone } from 'react-dropzone';
import { Upload, FileText, X, Plus, ChevronDown, ChevronUp, PlayCircle } from 'lucide-react';

interface UploadZoneProps {
  onUpload: (currentFiles: File[], comparisonFiles: File[]) => void;
  isUploading: boolean;
  uploadStep?: string;
}

export const UploadZone: React.FC<UploadZoneProps> = ({ onUpload, isUploading, uploadStep }) => {
  const [currentFiles, setCurrentFiles] = useState<File[]>([]);
  const [comparisonFiles, setComparisonFiles] = useState<File[]>([]);
  const [showComparison, setShowComparison] = useState(false);

  const onDropCurrent = useCallback((accepted: File[]) => {
    setCurrentFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...accepted.filter(f => !names.has(f.name))];
    });
  }, []);

  const onDropComparison = useCallback((accepted: File[]) => {
    setComparisonFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...accepted.filter(f => !names.has(f.name))];
    });
  }, []);

  const { getRootProps: getCurrentProps, getInputProps: getCurrentInputProps, isDragActive: isCurrentDrag } = useDropzone({
    onDrop: onDropCurrent,
    accept: { 'text/csv': ['.csv'] },
    disabled: isUploading,
  });

  const { getRootProps: getComparisonProps, getInputProps: getComparisonInputProps, isDragActive: isComparisonDrag } = useDropzone({
    onDrop: onDropComparison,
    accept: { 'text/csv': ['.csv'] },
    disabled: isUploading,
  });

  const handleAnalyze = () => {
    if (currentFiles.length === 0) return;
    onUpload(currentFiles, comparisonFiles);
  };

  return (
    <div className="space-y-4">
      {/* Current Period Drop Zone */}
      <div>
        <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-3">Current Period</p>
        <div
          {...getCurrentProps()}
          className={`
            relative group cursor-pointer overflow-hidden
            p-10 border-2 border-dashed rounded-3xl transition-all duration-300
            flex flex-col items-center justify-center gap-4
            ${isCurrentDrag ? 'border-blue-500 bg-blue-50/50' : 'border-slate-200 hover:border-blue-400 hover:bg-slate-50/50'}
            ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}
          `}
        >
          <input {...getCurrentInputProps()} />
          <div className="p-4 rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 shadow-[0_8px_20px_rgb(59,130,246,0.25)] group-hover:scale-110 transition-transform duration-300">
            <Upload className="w-7 h-7 text-white" />
          </div>
          <div className="text-center">
            <h3 className="text-lg font-semibold text-slate-800">
              {isCurrentDrag ? 'Drop here' : currentFiles.length > 0 ? 'Add more files' : 'Drop your ad reports'}
            </h3>
            <p className="mt-1 text-sm text-slate-500">CSV exports from Google Ads, Meta, or LinkedIn</p>
          </div>
        </div>

        {currentFiles.length > 0 && (
          <div className="mt-3 space-y-2">
            {currentFiles.map((f, i) => (
              <div key={i} className="flex items-center justify-between bg-blue-50 border border-blue-100 rounded-2xl px-4 py-2.5">
                <div className="flex items-center gap-3">
                  <FileText size={15} className="text-blue-500 flex-shrink-0" />
                  <span className="text-sm font-medium text-slate-700 truncate max-w-[280px]">{f.name}</span>
                  <span className="text-xs text-slate-400">{(f.size / 1024).toFixed(0)} KB</span>
                </div>
                {!isUploading && (
                  <button
                    onClick={(e) => { e.stopPropagation(); setCurrentFiles(prev => prev.filter((_, idx) => idx !== i)); }}
                    className="p-1 hover:bg-blue-200 rounded-lg transition-colors"
                  >
                    <X size={13} className="text-slate-500" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Comparison Period Toggle */}
      {currentFiles.length > 0 && !isUploading && (
        <button
          onClick={() => setShowComparison(v => !v)}
          className="w-full flex items-center justify-between px-5 py-3 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-2xl text-sm font-bold text-slate-500 transition-colors"
        >
          <span className="flex items-center gap-2">
            <Plus size={15} />
            Compare vs. another period (optional)
          </span>
          {showComparison ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
      )}

      {/* Comparison Period Drop Zone */}
      {showComparison && (
        <div>
          <p className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-3">Comparison Period</p>
          <div
            {...getComparisonProps()}
            className={`
              relative cursor-pointer overflow-hidden
              p-8 border-2 border-dashed rounded-3xl transition-all duration-300
              flex flex-col items-center justify-center gap-3
              ${isComparisonDrag ? 'border-violet-500 bg-violet-50/50' : 'border-slate-200 hover:border-violet-400 hover:bg-violet-50/20'}
              ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}
            `}
          >
            <input {...getComparisonInputProps()} />
            <div className="p-3 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 shadow-[0_4px_12px_rgb(139,92,246,0.2)]">
              <Upload className="w-5 h-5 text-white" />
            </div>
            <p className="text-sm font-medium text-slate-500">
              {isComparisonDrag ? 'Drop comparison files here' : 'Drop prior-period CSVs'}
            </p>
            <p className="text-xs text-slate-400 text-center max-w-xs">
              Leave empty and the app will auto-split your current data for Period-over-Period or Year-over-Year comparison.
            </p>
          </div>

          {comparisonFiles.length > 0 && (
            <div className="mt-3 space-y-2">
              {comparisonFiles.map((f, i) => (
                <div key={i} className="flex items-center justify-between bg-violet-50 border border-violet-100 rounded-2xl px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <FileText size={15} className="text-violet-500 flex-shrink-0" />
                    <span className="text-sm font-medium text-slate-700 truncate max-w-[280px]">{f.name}</span>
                    <span className="text-xs text-slate-400">{(f.size / 1024).toFixed(0)} KB</span>
                  </div>
                  {!isUploading && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setComparisonFiles(prev => prev.filter((_, idx) => idx !== i)); }}
                      className="p-1 hover:bg-violet-200 rounded-lg transition-colors"
                    >
                      <X size={13} className="text-slate-500" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Analyze button */}
      {currentFiles.length > 0 && !isUploading && (
        <button
          onClick={handleAnalyze}
          className="w-full h-14 bg-gradient-to-r from-blue-600 to-indigo-600 text-white font-black text-sm uppercase tracking-wider rounded-2xl flex items-center justify-center gap-3 shadow-xl shadow-blue-200 hover:shadow-blue-300 hover:from-blue-700 hover:to-indigo-700 transition-all"
        >
          <PlayCircle size={20} />
          Run Analysis
          {comparisonFiles.length > 0 && (
            <span className="text-xs font-bold bg-white/20 px-2 py-0.5 rounded-full">+ Comparison Period</span>
          )}
        </button>
      )}

      {/* Upload progress */}
      {isUploading && (
        <div className="flex flex-col items-center justify-center gap-3 py-6">
          <div className="w-10 h-10 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
          <p className="text-sm font-bold text-blue-600 animate-pulse uppercase tracking-wider">
            {uploadStep || 'Analyzing...'}
          </p>
        </div>
      )}
    </div>
  );
};
