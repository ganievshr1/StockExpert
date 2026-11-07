import { request } from "./api";

export type B1Scenario = "concurrent_10" | "sequential_10" | "single_user_parallel" | "confirmation";
export type B1Profile = "shared" | "split";

export interface B1SessionOption {
  session_id: string;
  title: string;
  normalized_rows?: number;
  normalized_requirements?: number;
  residual_rows?: number;
}

export interface B1Config {
  routing_mode: B1Profile;
  runtime_profile?: string;
  ready_bases: B1SessionOption[];
  requirement_sessions: B1SessionOption[];
  background_sessions: B1SessionOption[];
  protocol?: { pairs: number; warmup_seconds: number; duration_seconds: number; rate_per_minute: number; requests_per_run: number; poll_seconds: number; timeout_seconds: number };
  note?: string;
}

export interface B1Summary {
  scheduled?: number;
  started?: number;
  completed?: number;
  missed?: number;
  elapsed_seconds?: number;
  p95_seconds?: number | null;
  scenario_makespan_seconds?: number | null;
  error_rate?: number | null;
  batch_rows_per_minute?: number | null;
  success?: number;
  failed?: number;
  valid?: boolean;
  confirmed?: boolean | null;
  reason?: string;
  [key: string]: unknown;
}

export interface B1Experiment {
  id: string;
  title?: string;
  created_at?: string;
  status: string;
  scenario: B1Scenario;
  scope: "diagnostic" | "confirmation";
  progress?: number;
  expected_routing_mode?: B1Profile;
  error?: string | null;
  summary?: B1Summary | null;
  protocol?: { timeout_seconds?: number };
  plan?: {
    index: number;
    pair_index: number;
    routing_mode: B1Profile;
    status: string;
    summary?: B1Summary | null;
  }[];
  current_run?: {
    index: number;
    pair_index: number;
    routing_mode: B1Profile;
    phase: string;
    scheduled?: number;
    started?: number;
    completed?: number;
    success?: number;
    failed?: number;
    missed?: number;
    inflight?: number;
    progress?: number;
    elapsed_seconds?: number;
    summary?: B1Summary | null;
  } | null;
}

export interface B1CreatePayload {
  scenario: B1Scenario;
  base_session_id: string;
  requirement_session_id: string;
  background_session_id?: string;
  campaign_title?: string;
  diagnostic_timeout_seconds?: number;
}

const root = "/developer/b1/experiments";
const post = (value?: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});

export const b1Api = {
  config: () => request<B1Config>("/developer/b1/config"),
  list: () => request<{ items: B1Experiment[] }>(root),
  create: (payload: B1CreatePayload) => request<B1Experiment>(root, post(payload)),
  detail: (id: string) => request<B1Experiment>(`${root}/${encodeURIComponent(id)}`),
  cancel: (id: string) => request<B1Experiment>(`${root}/${encodeURIComponent(id)}/cancel`, post()),
  continue: (id: string) => request<B1Experiment>(`${root}/${encodeURIComponent(id)}/continue`, post()),
  exportPath: (id: string, format: "json" | "csv" | "zip") =>
    `${root}/${encodeURIComponent(id)}/export?format=${format}`,
};
