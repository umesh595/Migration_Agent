import type {
  AdminUser,
  Finding,
  PatchAuditEntry,
  ReviewQualityScore,
  SessionState,
  SessionSummary,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const ACCESS_TOKEN_KEY = "migration_agent.access_token";
const REFRESH_TOKEN_KEY = "migration_agent.refresh_token";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return getAccessToken() !== null;
}

async function extractDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return response.statusText || `request failed with status ${response.status}`;
  }
}

/** One-shot refresh on a 401. If refresh itself fails, callers see the original
 * 401 and the caller (AuthProvider) is responsible for redirecting to /login —
 * this module never touches routing. */
async function tryRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return false;

  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!response.ok) return false;

  const data = await response.json();
  setTokens(data.access_token, data.refresh_token);
  return true;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const token = getAccessToken();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 401 && retry) {
    const refreshed = await tryRefresh();
    if (refreshed) return request<T>(path, init, false);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// --- Auth ---
// No signup: accounts are admin-provisioned only (FR-A5). See app/admin.

export async function login(email: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }
  const data = await response.json();
  setTokens(data.access_token, data.refresh_token);
}

export interface CurrentUser {
  id: string;
  email: string;
  is_admin: boolean;
}

export function me(): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/me");
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await request<void>("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

// --- Sessions ---

export function listSessions(): Promise<SessionSummary[]> {
  return request<SessionSummary[]>("/sessions");
}

export function createSession(name: string): Promise<SessionSummary> {
  return request<SessionSummary>("/sessions", { method: "POST", body: JSON.stringify({ name }) });
}

export function getSessionState(sessionId: string): Promise<SessionState> {
  return request<SessionState>(`/sessions/${sessionId}/state`);
}

export function acceptModel(sessionId: string): Promise<{ status: string; model_version: number; session_status: string }> {
  return request(`/sessions/${sessionId}/model/accept`, { method: "POST" });
}

export function approvePlan(sessionId: string): Promise<{ status: string; plan_version: number; session_status: string }> {
  return request(`/sessions/${sessionId}/plan/approve`, { method: "POST" });
}

export function getFindings(sessionId: string): Promise<{ findings: Finding[] }> {
  return request(`/sessions/${sessionId}/findings`);
}

export function getAudit(sessionId: string): Promise<{ records: PatchAuditEntry[] }> {
  return request(`/sessions/${sessionId}/audit`);
}

export function getReviewQuality(sessionId: string): Promise<{ scores: ReviewQualityScore[] }> {
  return request(`/sessions/${sessionId}/review-quality`);
}

export async function downloadExport(sessionId: string, format: "markdown" | "docx"): Promise<void> {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/export?format=${format}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new ApiError(response.status, await extractDetail(response));
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  const filename = match?.[1] ?? `migration-plan.${format === "docx" ? "docx" : "md"}`;

  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

// --- SSE turn streaming ---
// A browser EventSource can't attach an Authorization header, so this streams
// the POST response body directly and parses the SSE framing by hand — matching
// what the backend actually serves (a streamed POST, not a GET EventSource).

export interface SseEvent {
  event: string;
  id?: string;
  data: unknown;
}

export async function* streamMessage(sessionId: string, message: string): AsyncGenerator<SseEvent> {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message }),
  });

  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await extractDetail(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function parseRawEvent(rawEvent: string): SseEvent | null {
    let eventName = "message";
    let id: string | undefined;
    const dataLines: string[] = [];
    for (const line of rawEvent.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return null;

    let parsed: unknown = dataLines.join("\n");
    try {
      parsed = JSON.parse(dataLines.join("\n"));
    } catch {
      // Leave as raw string; the caller decides what to do with unparseable data.
    }
    return { event: eventName, id, data: parsed };
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let match = /\r?\n\r?\n/.exec(buffer);
    while (match?.index !== undefined) {
      const rawEvent = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);

      const parsedEvent = parseRawEvent(rawEvent);
      if (parsedEvent) yield parsedEvent;
      match = /\r?\n\r?\n/.exec(buffer);
      continue;

      let eventName = "message";
      let id: string | undefined;
      const dataLines: string[] = [];
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("id:")) id = line.slice(3).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length > 0) {
        let parsed: unknown = dataLines.join("\n");
        try {
          parsed = JSON.parse(dataLines.join("\n"));
        } catch {
          // leave as raw string — the caller decides what to do with unparseable data
        }
        yield { event: eventName, id, data: parsed };
      }
      match = /\r?\n\r?\n/.exec(buffer);
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    const parsedEvent = parseRawEvent(buffer);
    if (parsedEvent) yield parsedEvent;
  }
}

// --- Admin (FR-A5) ---

export function adminListUsers(): Promise<AdminUser[]> {
  return request<AdminUser[]>("/admin/users");
}

export function adminCreateUser(email: string, password: string, isAdmin: boolean): Promise<AdminUser> {
  return request<AdminUser>("/admin/users", {
    method: "POST",
    body: JSON.stringify({ email, password, is_admin: isAdmin }),
  });
}

export function adminSetActive(userId: string, active: boolean): Promise<AdminUser> {
  return request<AdminUser>(`/admin/users/${userId}/active`, {
    method: "PATCH",
    body: JSON.stringify({ active }),
  });
}

export function adminResetPassword(
  userId: string
): Promise<{ id: string; email: string; temporary_password: string }> {
  return request(`/admin/users/${userId}/reset-password`, { method: "POST" });
}
