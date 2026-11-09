import { request } from "./api";
import type {
  AuditEvent,
  Page,
  ProcurementSession,
  SchemaDraft,
  Feature,
} from "../types";
import type { Quality } from "./quality";
import type { Evaluation } from "./developer";

export interface Efficiency {
  ready: boolean;
  job_id: string | null;
  stock_rows: number;
  reused_passports: number;
  reuse_rate: number | null;
  requirements: number;
  requirements_with_candidates: number;
  requirement_coverage: number | null;
  pairs_before: number;
  pairs_top150: number;
  shortlisted_pairs: number;
  reduction_rate: number | null;
  latest_job_seconds: number | null;
  session_processing_seconds: number | null;
  available_stock_rows: number;
  unavailable_stock_rows: number;
  available_shortlisted_pairs: number;
  compression_factor: number | null;
  contradiction_rate: number | null;
  new_stock_passports: number | null;
  new_requirement_passports: number | null;
  vectors_reused: number | null;
  vectors_created: number | null;
  known_rules_top150: number;
}
export interface WorkspaceOverview {
  organization: string;
  total_sessions: number;
  active_sessions: number;
  latest_session_id: string | null;
  latest_session_title: string | null;
  efficiency: Efficiency | null;
  needs_review_pairs: number;
  generated_at: string;
}
export interface Measurement {
  expert_evaluations?: {
    id: string;
    reference_title: string;
    reference_version: string;
    status: string;
    updated_at: string;
    result: Evaluation["result"];
  }[];
  generated_at: string;
  session_id: string;
  session_title: string;
  quality: Quality;
  efficiency: Efficiency;
  targets: {
    metric: string;
    label: string;
    target: number;
    value: number | null;
    reason: string;
  }[];
  extraction: {
    note?: string | null;
    eligible_rows: number;
    normalized_rows: number;
    fill_rate: number | null;
    evidence_rate: number | null;
    fields: {
      key: string;
      label: string;
      unit: string;
      filled: number;
      confirmed: number;
      missing: number;
      fill_rate: number | null;
    }[];
  };
  taxonomy: {
    coverage: number | null;
    categories: { id: string; label: string; count: number }[];
  };
  matching: {
    pairs_evaluated: number;
    shortlisted_pairs: number;
    contradicted_pairs: number;
    needs_review_pairs: number;
    requirements: {
      requirement_id: string;
      before: number;
      retrieved: number;
      top150: number;
      shortlisted: number;
      compression_factor: number | null;
      reduction_rate: number | null;
    }[];
  };
  timing: {
    measured: boolean;
    processing_seconds: number;
    stages: { stage: string; label: string; seconds: number }[];
    segments: {
      id: string;
      job_id: string;
      attempt: string;
      label: string;
      seconds: number;
      outcome: string;
    }[];
  };
}
export interface AuditPageResult {
  total: number;
  events: (AuditEvent & { author: string })[];
  authors: { id: number; name: string }[];
  event_types: string[];
}
export type ReportSections = Record<
  "summary" | "candidates" | "evidence" | "history",
  boolean
>;

const post = (value: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
export const workspaceApi = {
  overview: (days = 0) =>
    request<WorkspaceOverview>(`/workspace/overview?days=${days}`),
  sessions: (params: URLSearchParams) =>
    request<Page<ProcurementSession>>(`/workspace/sessions?${params}`),
  measurement: (sid: string) => request<Measurement>(`/metrics/${sid}`),
  audit: (sid: string, params: URLSearchParams) =>
    request<AuditPageResult>(`/audit/${sid}/search?${params}`),
  category: (sid: string, name: string, parent_id: string | null) =>
    request<SchemaDraft>(
      `/taxonomy/${sid}/categories`,
      post({ name, parent_id }),
    ),
  feature: (sid: string, category_id: string, feature: Feature) =>
    request<SchemaDraft>(
      `/taxonomy/${sid}/features`,
      post({ category_id, feature }),
    ),
  report: (
    sid: string,
    format: "pdf" | "xlsx",
    sections: ReportSections,
    inline = false,
  ) => {
    const params = new URLSearchParams({
      format,
      inline: String(inline),
      ...Object.fromEntries(
        Object.entries(sections).map(([key, value]) => [key, String(value)]),
      ),
    });
    return request<Blob>(`/reports/${sid}/document?${params}`, {}, true);
  },
};

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
