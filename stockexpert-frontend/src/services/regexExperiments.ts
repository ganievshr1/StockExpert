import { request } from "./api";

export interface RegexExperiment {
  id: string;
  source_session_id: string | null;
  source_type?: "session" | "xlsx";
  source_title: string;
  dataset_id: string | null;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  rules_version: string;
  rules_sha256: string;
  input_sha256: string;
  row_count: number;
  processed_rows: number;
  created_at: string;
  error: string | null;
  baseline: {
    metrics?: Record<string, number | null>;
    models?: { model: string }[];
    note?: string;
  };
  result: {
    evaluation_status?: "NO_REFERENCE" | "EVALUATED";
    is_development_dataset?: boolean;
    extraction?: {
      rows: number;
      rows_with_attributes: number;
      rows_with_parameters: number;
      rows_with_designation: number;
      model_only_rows: number;
      review_rows: number;
      note: string;
    };
    metrics?: Record<string, number | null>;
    coverage?: Record<string, number | string[]>;
    timing?: {
      normalization_seconds: number;
      p50_row_ms: number;
      p95_row_ms: number;
      scope: string;
    };
    runtime?: { llm_calls: number; device: string };
    notes?: string[];
  };
}
export interface RegexPassport {
  source_item_id: string;
  sheet: string;
  row: number;
  source_text: string;
  company?: string;
  source_group?: string;
  passport: {
    normalized_features?: { key: string; label: string; unit: string }[];
    type_hints?: string[];
    attributes: Record<string, unknown>;
    evidence: Record<
      string,
      {
        quote: string;
        rule: string;
        source_unit?: string;
        target_unit?: string;
      }
    >;
  };
  issues: { code: string; key?: string; message?: string; quote?: string }[];
  evaluation?: {
    errors: { key: string; expected: unknown; actual: unknown[] }[];
  };
}
export const regexApi = {
  list: (datasetId = "", offset = 0) =>
    request<{ total: number; items: RegexExperiment[] }>(
      `/developer/regex-experiments?limit=20&offset=${offset}${datasetId ? `&dataset_id=${encodeURIComponent(datasetId)}` : ""}`,
    ),
  start: (source_session_id: string, dataset_id: string, request_id: string) =>
    request<RegexExperiment>("/developer/regex-experiments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_session_id, dataset_id, request_id }),
    }),
  upload: (file: File, requestId: string, datasetId?: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("request_id", requestId);
    if (datasetId) body.append("dataset_id", datasetId);
    return request<RegexExperiment>("/developer/regex-experiments/xlsx", {
      method: "POST",
      body,
    });
  },
  passports: (id: string, offset = 0, reviewOnly = false) =>
    request<{ total: number; items: RegexPassport[] }>(
      `/developer/regex-experiments/${id}/passports?limit=20&offset=${offset}${reviewOnly ? "&review_only=true" : ""}`,
    ),
};
