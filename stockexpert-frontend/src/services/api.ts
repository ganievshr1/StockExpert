import type {
  LLMModels,
  NormalizedBase,
  User,
  SystemConfig,
  ProcurementSession,
  DocumentRecord,
  JobStatus,
  Metrics,
  SchemaDraft,
  CandidateItem,
  Page,
  Item,
  ExpertDecision,
  ReportSummary,
  AuditEvent,
} from "../types";
export const API_BASE = (
  import.meta.env.VITE_API_BASE_URL || "/api/v1"
).replace(/\/$/, "");
let token =
  sessionStorage.getItem("stockexpert.token") ||
  localStorage.getItem("stockexpert.token") ||
  "";
export function getToken() {
  return token;
}
export function setToken(value: string, remember = false) {
  token = value;
  sessionStorage.removeItem("stockexpert.token");
  localStorage.removeItem("stockexpert.token");
  if (value)
    (remember ? localStorage : sessionStorage).setItem(
      "stockexpert.token",
      value,
    );
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
  blob = false,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const r = await fetch(API_BASE + path, { ...options, headers });
  if (!r.ok) {
    let detail: unknown;
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = r.statusText;
    }
    if (r.status === 401 && path !== "/auth/token") {
      setToken("");
      window.dispatchEvent(new Event("stockexpert:unauthorized"));
    }
    const message = Array.isArray(detail)
      ? detail
          .map((d) => `${d.loc?.join(".") || ""}: ${d.msg || "Ошибка"}`)
          .join("; ")
      : typeof detail === "string"
        ? detail
        : `Ошибка HTTP ${r.status}`;
    throw new ApiError(r.status, message);
  }
  return (blob ? await r.blob() : await r.json()) as T;
}
const json = (method: string, value?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});
const sessionPath = (sid: string) =>
  `/procurement/sessions/${encodeURIComponent(sid)}`;
export interface SessionDeletionResult {
  deleted_session_ids: string[];
  failed: {
    session_id: string;
    title: string;
    status_code: number;
    detail: string;
  }[];
}
export const api = {
  deleteSession: (sid: string) =>
    request<{ deleted: boolean; session_id: string }>(
      sessionPath(sid),
      json("DELETE"),
    ),
  deleteAllSessions: () =>
    request<SessionDeletionResult>("/procurement/sessions", json("DELETE")),
  replaceDocument: (sid: string, did: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentRecord>(
      sessionPath(sid) + "/documents/" + did + "/replace",
      { method: "POST", body },
    );
  },
  clearStock: (sid: string) =>
    request(`/normalized-bases/${sid}/stock`, json("DELETE")),
  useStock: (sid: string, mid: string) =>
    request(`/matching/${sid}/candidates/${mid}/use`, json("POST")),
  cancel: (sid: string) => request(sessionPath(sid) + "/cancel", json("POST")),
  clearFiles: (sid: string) =>
    request(sessionPath(sid) + "/files", json("DELETE")),
  deleteDocument: (sid: string, did: string) =>
    request(sessionPath(sid) + "/documents/" + did, json("DELETE")),

  login: (username: string, password: string) =>
    request<{ access_token: string }>("/auth/token", {
      method: "POST",
      body: new URLSearchParams({ username, password }),
    }),
  me: () => request<User>("/auth/me"),
  config: () => request<SystemConfig>("/system/config"),
  models: () => request<LLMModels>("/system/llm-models"),
  selectModel: (sid: string, profileId: string) =>
    request<ProcurementSession>(
      sessionPath(sid) + "/llm",
      json("PUT", { llm_profile_id: profileId }),
    ),
  selectNormalization: (sid: string, method: "llm" | "regex") =>
    request<ProcurementSession>(
      sessionPath(sid) + "/normalization-method",
      json("PUT", { normalization_method: method }),
    ),
  sessions: (offset = 0) =>
    request<ProcurementSession[]>(
      `/procurement/sessions?limit=50&offset=${offset}`,
    ),
  selectTaxonomy: (sid: string, enabled: boolean) => request<ProcurementSession>(`/procurement/sessions/${sid}/taxonomy-mode`, json("PUT", {taxonomy_enabled: enabled})),
  bases: (offset = 0) =>
    request<NormalizedBase[]>(`/normalized-bases?limit=100&offset=${offset}`),
  create: (
    title: string,
    baseSessionId?: string,
    llmProfileId?: string,
    normalizationMethod: "llm" | "regex" = "regex",
    taxonomyEnabled = false,
  ) =>
    request<ProcurementSession>(
      "/procurement/sessions",
      json("POST", {
        title,
        normalization_method: normalizationMethod,
        taxonomy_enabled: taxonomyEnabled,
        ...(baseSessionId ? { base_session_id: baseSessionId } : {}),
        ...(llmProfileId ? { llm_profile_id: llmProfileId } : {}),
      }),
    ),
  session: (sid: string) => request<ProcurementSession>(sessionPath(sid)),
  documents: (sid: string) =>
    request<DocumentRecord[]>(sessionPath(sid) + "/documents"),
  metrics: (sid: string) => request<Metrics>(sessionPath(sid) + "/metrics"),
  status: (sid: string) => request<JobStatus>(`/matching/${sid}/status`),
  upload: (sid: string, kind: "residuals" | "requirement", file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentRecord>(sessionPath(sid) + `/upload-${kind}`, {
      method: "POST",
      body,
    });
  },
  prepare: (sid: string) => request(`/pipeline/${sid}/prepare`, json("POST")),
  approveMapping: (sid: string) =>
    request<ProcurementSession>(
      `/pipeline/${sid}/approve-mapping`,
      json("POST"),
    ),
  rebuildTaxonomy: (sid: string) =>
    request(`/taxonomy/${sid}/rebuild`, json("POST")),
  saveSchema: (sid: string, schema: SchemaDraft) =>
    request<SchemaDraft>(`/pipeline/${sid}/schema`, json("PUT", schema)),
  approve: (sid: string, version: string) =>
    request<ProcurementSession>(
      `/taxonomy/${sid}/approve`,
      json("POST", {
        freeze_version: version,
        comments: "Проверено экспертом в StockExpert",
      }),
    ),
  run: (sid: string, size: number) =>
    request(
      `/pipeline/${sid}/run`,
      json("POST", { target_shortlist_size: size }),
    ),
  normalize: (sid: string) =>
    request(
      `/pipeline/${sid}/start-normalization`,
      json("POST", { batch_size: 1, grammar_strict: true }),
    ),
  match: (sid: string, size: number) =>
    request(
      `/matching/${sid}/run`,
      json("POST", { target_shortlist_size: size }),
    ),
  retry: (sid: string, jid: string) =>
    request(`/pipeline/${sid}/jobs/${jid}/retry`, json("POST")),
  requirements: (sid: string) =>
    request<{ items: Item[] }>(`/matching/${sid}/benchmark`),
  candidates: (sid: string, params: URLSearchParams, signal?: AbortSignal) =>
    request<Page<CandidateItem>>(`/matching/${sid}/candidates?${params}`, {
      signal,
    }),
  passports: (sid: string, offset: number, baseType = "") =>
    request<Page<Item>>(
      `/pipeline/${sid}/passports?limit=20&offset=${offset}${baseType ? "&base_type=" + encodeURIComponent(baseType) : ""}`,
    ),
  decide: (
    sid: string,
    mid: string,
    decision: ExpertDecision,
    comment: string,
  ) =>
    request<CandidateItem>(
      `/matching/${sid}/decide`,
      json("POST", { item_id: mid, decision, allocated_qty: 0, comment }),
    ),
  summary: (sid: string) =>
    request<ReportSummary>(`/reports/${sid}/summary?include_items=false`),
  audit: (sid: string, offset = 0) =>
    request<{ events: AuditEvent[] }>(
      `/audit/${sid}/events?limit=50&offset=${offset}`,
    ),
  trail: (sid: string) =>
    request<{ chain_valid: boolean; root_hash: string }>(`/audit/${sid}/trail`),
  verify: (sid: string, hash: string) =>
    request<{ verified: boolean; chain_valid: boolean; root_hash: string }>(
      `/audit/${sid}/verify-hash`,
      json("POST", { expected_root_hash: hash }),
    ),
  download: async (
    sid: string,
    kind: "excel" | "pdf" | "audit" | "metrics",
  ) => {
    const path =
      kind === "metrics"
        ? `/metrics/${sid}/export`
        : kind === "audit"
          ? `/audit/${sid}/export/csv`
          : `/reports/${sid}/export/${kind}`;
    const blob = await request<Blob>(path, {}, true);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stockexpert-${sid.slice(0, 8)}.${kind === "excel" || kind === "metrics" ? "xlsx" : kind === "pdf" ? "pdf" : "csv"}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
