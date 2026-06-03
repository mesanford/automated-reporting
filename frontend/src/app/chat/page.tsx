"use client";

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import ReactMarkdown from 'react-markdown';
import {
  MessageSquare, Send, Plus, Trash2, ChevronRight, ChevronDown, Wrench, Zap, ArrowLeft, Settings,
  BarChart3, Activity as ActivityIcon,
} from 'lucide-react';
import {
  ResponsiveContainer, LineChart, BarChart as ReBarChart, Line, Bar,
  XAxis, YAxis, Tooltip, Legend, CartesianGrid,
} from 'recharts';
import { streamSse } from '@/lib/sse';
import { API_BASE, apiFetch, apiJson } from '@/lib/api';
import { WorkspaceSwitcher } from '@/components/WorkspaceSwitcher';

interface ConversationSummary {
  id: number;
  title: string;
  created_at: string | null;
  updated_at: string | null;
}

interface ChatMessage {
  id: number;
  conversation_id: number;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_payload?: unknown;
  created_at?: string | null;
}

interface StreamingState {
  text: string;
  toolEvents: Array<{
    type: 'tool_call' | 'tool_result';
    name: string;
    call_id: string;
    args?: unknown;
    result?: unknown;
  }>;
}

const emptyStreaming: StreamingState = { text: '', toolEvents: [] };

export default function ChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState<StreamingState>(emptyStreaming);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadConversations();
  }, []);

  useEffect(() => {
    if (activeId != null) {
      void loadMessages(activeId);
    } else {
      setMessages([]);
    }
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, streaming]);

  async function loadConversations() {
    try {
      const data = await apiJson<ConversationSummary[]>('/api/chat/conversations');
      setConversations(data);
      if (activeId == null && data.length > 0) setActiveId(data[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conversations');
    }
  }

  async function loadMessages(id: number) {
    try {
      const data = await apiJson<{ messages: ChatMessage[] }>(`/api/chat/conversations/${id}`);
      setMessages(data.messages || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conversation');
    }
  }

  async function newConversation() {
    try {
      const conv = await apiJson<ConversationSummary>('/api/chat/conversations', {
        method: 'POST',
        body: '{}',
      });
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create conversation');
    }
  }

  async function deleteConversation(id: number) {
    try {
      await apiFetch(`/api/chat/conversations/${id}`, { method: 'DELETE' });
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeId === id) setActiveId(null);
    } catch {
      // ignore
    }
  }

  async function sendMessage() {
    const content = input.trim();
    if (!content || isStreaming) return;

    let convId = activeId;
    if (convId == null) {
      try {
        const conv = await apiJson<ConversationSummary>('/api/chat/conversations', {
          method: 'POST',
          body: '{}',
        });
        convId = conv.id;
        setConversations((prev) => [conv, ...prev]);
        setActiveId(conv.id);
      } catch {
        setError('Failed to create conversation');
        return;
      }
    }

    setInput('');
    setIsStreaming(true);
    setStreaming(emptyStreaming);
    setError(null);

    // Optimistically render the user message
    const optimisticUser: ChatMessage = {
      id: Date.now() * -1,
      conversation_id: convId,
      role: 'user',
      content,
    };
    setMessages((prev) => [...prev, optimisticUser]);

    try {
      for await (const event of streamSse(
        `${API_BASE}/api/chat/conversations/${convId}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        },
      )) {
        const type = event.type as string;
        if (type === 'token') {
          const text = (event.text as string) || '';
          setStreaming((s) => ({ ...s, text: s.text + text }));
        } else if (type === 'tool_call') {
          setStreaming((s) => ({
            ...s,
            toolEvents: [
              ...s.toolEvents,
              {
                type: 'tool_call',
                name: event.name as string,
                call_id: event.call_id as string,
                args: event.args,
              },
            ],
          }));
        } else if (type === 'tool_result') {
          setStreaming((s) => ({
            ...s,
            toolEvents: [
              ...s.toolEvents,
              {
                type: 'tool_result',
                name: event.name as string,
                call_id: event.call_id as string,
                result: event.result,
              },
            ],
          }));
        } else if (type === 'error') {
          setError((event.message as string) || 'Stream error');
        } else if (type === 'done') {
          // Reload persisted messages to replace optimistic + streaming state.
          await loadMessages(convId);
          void loadConversations();
          break;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stream failed');
    } finally {
      setIsStreaming(false);
      setStreaming(emptyStreaming);
    }
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  }

  // Group consecutive tool_call + tool_result pairs by call_id for nicer rendering.
  const persistedToolEvents = messages
    .filter((m) => m.role !== 'user' && (m.tool_name || m.role === 'tool'))
    .map((m) => ({
      type: m.role === 'tool' ? ('tool_result' as const) : ('tool_call' as const),
      name: m.tool_name || '',
      call_id: m.tool_call_id || String(m.id),
      args: m.role === 'tool' ? undefined : m.tool_payload,
      result: m.role === 'tool' ? m.tool_payload : undefined,
    }));

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <nav className="border-b border-slate-100 bg-background/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="w-10 h-10 bg-gradient-to-br from-blue-600 to-indigo-700 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200"
            >
              <Zap className="text-white w-6 h-6 fill-white" />
            </Link>
            <h1 className="text-2xl font-black tracking-tight text-slate-900 flex items-center gap-2">
              <MessageSquare className="text-blue-600" size={22} />
              Conversational Analytics
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <WorkspaceSwitcher />
            <Link
              href="/activity"
              className="flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-blue-600"
            >
              <ActivityIcon size={16} />
              Activity
            </Link>
            <Link
              href="/settings"
              className="flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-blue-600"
            >
              <Settings size={16} />
              Settings
            </Link>
            <Link
              href="/"
              className="flex items-center gap-2 text-sm font-semibold text-slate-500 hover:text-blue-600"
            >
              <ArrowLeft size={16} />
              Back to dashboard
            </Link>
          </div>
        </div>
      </nav>

      <div className="flex-1 flex max-w-7xl mx-auto w-full">
        {/* Sidebar */}
        <aside className="w-72 border-r border-slate-100 p-4 flex flex-col gap-3">
          <button
            onClick={newConversation}
            className="flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-bold hover:bg-blue-700 transition-colors"
          >
            <Plus size={16} />
            New conversation
          </button>
          <div className="flex-1 overflow-y-auto space-y-1">
            {conversations.length === 0 && (
              <p className="text-xs text-slate-400 px-2 py-4">No conversations yet.</p>
            )}
            {conversations.map((c) => (
              <div
                key={c.id}
                className={`group flex items-center justify-between gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm ${
                  activeId === c.id ? 'bg-blue-50 text-blue-700' : 'hover:bg-slate-50 text-slate-700'
                }`}
                onClick={() => setActiveId(c.id)}
              >
                <span className="truncate flex-1">{c.title}</span>
                <button
                  className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500"
                  onClick={(e) => {
                    e.stopPropagation();
                    void deleteConversation(c.id);
                  }}
                  aria-label="Delete conversation"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </aside>

        {/* Thread */}
        <main className="flex-1 flex flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
            {messages.length === 0 && !isStreaming && (
              <div className="text-center py-16 text-slate-400">
                <MessageSquare size={36} className="mx-auto mb-3 text-slate-300" />
                <p className="text-sm">
                  Ask a question about your ad performance — e.g. <em>“Which platform had the worst CPA last month?”</em>
                </p>
              </div>
            )}

            {messages.map((m) => {
              if (m.role === 'user') return <UserBubble key={m.id} content={m.content} />;
              if (m.role === 'assistant' && !m.tool_name && m.content) {
                return <AssistantBubble key={m.id} content={m.content} />;
              }
              return null;
            })}

            {persistedToolEvents.length > 0 && (
              <ToolEventList events={persistedToolEvents} />
            )}

            {isStreaming && (
              <>
                {streaming.toolEvents.length > 0 && <ToolEventList events={streaming.toolEvents} />}
                {streaming.text && <AssistantBubble content={streaming.text} streaming />}
                {!streaming.text && streaming.toolEvents.length === 0 && (
                  <div className="text-xs text-slate-400 italic">Thinking…</div>
                )}
              </>
            )}

            {error && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg p-3">
                {error}
              </div>
            )}
          </div>

          <div className="border-t border-slate-100 p-4">
            <div className="flex gap-2 items-end">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onComposerKeyDown}
                rows={2}
                placeholder="Ask about your campaigns, platforms, or spend…"
                disabled={isStreaming}
                className="flex-1 resize-none rounded-xl border border-slate-200 px-4 py-2 text-sm focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:opacity-50"
              />
              <button
                onClick={() => void sendMessage()}
                disabled={isStreaming || !input.trim()}
                className="px-4 py-3 rounded-xl bg-blue-600 text-white font-bold disabled:bg-slate-300 hover:bg-blue-700 transition-colors flex items-center gap-2"
              >
                <Send size={16} />
                {isStreaming ? 'Streaming…' : 'Send'}
              </button>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-2xl rounded-2xl px-4 py-3 bg-blue-600 text-white text-sm whitespace-pre-wrap">
        {content}
      </div>
    </div>
  );
}

function AssistantBubble({ content, streaming }: { content: string; streaming?: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-3xl rounded-2xl px-4 py-3 bg-white border border-slate-100 shadow-sm text-sm prose prose-sm max-w-none">
        <ReactMarkdown>{content}</ReactMarkdown>
        {streaming && <span className="inline-block w-1.5 h-4 bg-blue-500 animate-pulse align-middle ml-0.5" />}
      </div>
    </div>
  );
}

type ToolEvent = {
  type: 'tool_call' | 'tool_result';
  name: string;
  call_id: string;
  args?: unknown;
  result?: unknown;
};

function ToolEventList({ events }: { events: ToolEvent[] }) {
  // Pair calls with results by call_id, preserving order.
  const pairs = new Map<string, { name: string; args?: unknown; result?: unknown }>();
  for (const e of events) {
    const existing = pairs.get(e.call_id) || { name: e.name };
    if (e.type === 'tool_call') existing.args = e.args;
    if (e.type === 'tool_result') existing.result = e.result;
    existing.name = e.name || existing.name;
    pairs.set(e.call_id, existing);
  }
  return (
    <div className="space-y-2">
      {Array.from(pairs.entries()).map(([id, p]) => (
        <ToolCallCard key={id} name={p.name} args={p.args} result={p.result} />
      ))}
    </div>
  );
}

interface ChartSpec {
  kind: 'chart_spec';
  chart_type: 'line' | 'bar';
  title: string;
  x_label: string;
  y_label: string;
  data: Array<Record<string, unknown>>;
}

function isChartSpec(v: unknown): v is ChartSpec {
  return Boolean(
    v && typeof v === 'object' && (v as { kind?: string }).kind === 'chart_spec'
  );
}

function InlineChart({ spec }: { spec: ChartSpec }) {
  // Pivot multi-series data: detect a `series` column. If present, build
  // one numeric key per series so Recharts can stack lines/bars.
  const hasSeries = spec.data.some((d) => 'series' in d);
  const data = hasSeries
    ? Object.values(
        spec.data.reduce<Record<string, Record<string, unknown>>>((acc, row) => {
          const x = String(row.x ?? '');
          if (!acc[x]) acc[x] = { x };
          acc[x][String(row.series)] = row.y;
          return acc;
        }, {}),
      )
    : spec.data.map((d) => ({ x: d.x, y: d.y }));

  const seriesNames = hasSeries
    ? Array.from(new Set(spec.data.map((d) => String(d.series))))
    : ['y'];
  const palette = ['#2563eb', '#16a34a', '#dc2626', '#a855f7', '#f59e0b'];

  const Chart = spec.chart_type === 'bar' ? ReBarChart : LineChart;
  const Series = spec.chart_type === 'bar' ? Bar : Line;

  return (
    <div className="border border-slate-100 rounded-xl bg-white p-3">
      <div className="flex items-center gap-2 mb-2">
        <BarChart3 size={14} className="text-blue-500" />
        <span className="text-xs font-bold text-slate-700">{spec.title}</span>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <Chart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="x" tick={{ fontSize: 10 }} label={{ value: spec.x_label, position: 'insideBottom', offset: -5, fontSize: 10 }} />
            <YAxis tick={{ fontSize: 10 }} label={{ value: spec.y_label, angle: -90, position: 'insideLeft', fontSize: 10 }} />
            <Tooltip contentStyle={{ fontSize: 11 }} />
            {hasSeries && <Legend wrapperStyle={{ fontSize: 10 }} />}
            {seriesNames.map((name, i) => (
              <Series
                key={name}
                type="monotone"
                dataKey={name}
                stroke={palette[i % palette.length]}
                fill={palette[i % palette.length]}
                strokeWidth={2}
                dot={false}
              />
            ))}
          </Chart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ToolCallCard({ name, args, result }: { name: string; args?: unknown; result?: unknown }) {
  // If this is a render_chart result, draw the chart instead of the JSON dump.
  if (name === 'render_chart' && isChartSpec(result)) {
    return <InlineChart spec={result} />;
  }
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-slate-100 rounded-xl bg-slate-50 text-xs">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-slate-600 hover:text-slate-900"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Wrench size={14} className="text-blue-500" />
        <span className="font-semibold">{name}</span>
        {result === undefined && <span className="text-slate-400 italic">running…</span>}
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-2">
          {args !== undefined && (
            <div>
              <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1">Args</div>
              <pre className="bg-white border border-slate-100 rounded p-2 overflow-x-auto text-[11px]">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
          {result !== undefined && (
            <div>
              <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1">Result</div>
              <pre className="bg-white border border-slate-100 rounded p-2 overflow-x-auto text-[11px] max-h-64">
                {JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
