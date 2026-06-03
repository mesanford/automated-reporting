import { getCurrentIdToken } from './auth';
import { getActiveWorkspaceId } from './workspace';

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

/** Set this to a non-empty string to send X-User-Id (dev/mock auth) when no Firebase token is present. */
const DEV_USER_ID = process.env.NEXT_PUBLIC_DEV_USER_ID ?? '';

export async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};

  const token = await getCurrentIdToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  else if (DEV_USER_ID) headers['X-User-Id'] = DEV_USER_ID;

  const wsId = getActiveWorkspaceId();
  if (wsId) headers['X-Workspace-Id'] = String(wsId);

  return headers;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const auth = await authHeaders();
  for (const [k, v] of Object.entries(auth)) headers.set(k, v);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}

export async function apiJson<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init);
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (!res.ok) {
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return payload as T;
}
