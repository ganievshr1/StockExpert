import { request } from "./api";
import type { LLMConfig } from "../types";
import type { Hypothesis, H3Population } from "../components/HypothesisMetrics";

export interface ExpertReference {
  id: string;
  title: string;
  version: string;
  checksum: string;
  passport_count: number;
  pair_count: number;
  notes: string[];
}
export interface Evaluation {
  id: string;
  session_id: string;
  session_title: string;
  status: string;
  error: string | null;
  updated_at: string;
  result: {
    hypotheses?: Hypothesis[];
    h3_population?: H3Population;
    h3_queries?: {
      requirement_id: string;
      catalog_rows: number;
      pairs_reviewed: number;
      relevant: number;
      hits_150: number;
      missed_relevant: number;
      top150: number;
      recall_at_150: number | null;
    }[];
    metrics?: Record<string, number | null>;
    timing?: Record<string, number | null>;
    models?: { model: string; passports: number }[];
    selected_model?: Partial<LLMConfig>;
    coverage?: {
      expected_stock: number;
      normalized_stock: number;
      reused_passports: number;
      missing_ids: string[];
      changed_ids: string[];
      extra_stock_ids?: string[];
      tp: number;
      fp: number;
      fn: number;
      l3_available: number;
    };
    notes?: string[];
    note?: string;
    fields?: {
      key: string;
      tp?: number;
      fp?: number;
      fn?: number;
      f1: number | null;
    }[];
    rows?: {
      id: string;
      row: number;
      sheet: string;
      name: string;
      missing: boolean;
      errors: { key: string; expected: unknown; actual: unknown[] }[];
      expected_category: string[];
      actual_category_ids: string[];
    }[];
  };
}
export interface ComparableSession {
  id: string;
  title: string;
  status: string;
  llm_config: Partial<LLMConfig>;
  normalization_method?: "llm" | "regex";
  selected: boolean;
  base_snapshot?: { reused_passports?: boolean };
}

export async function saveDownload(path: string, filename: string) {
  const blob = await request<Blob>(path, {}, true);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const developerApi = {
  references: () => request<ExpertReference[]>("/developer/references"),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<ExpertReference>("/developer/references", {
      method: "POST",
      body,
    });
  },
  sessions: (id: string, offset = 0) =>
    request<{ total: number; items: ComparableSession[] }>(
      `/developer/references/${id}/sessions?limit=100&offset=${offset}`,
    ),
  evaluate: (id: string, session_ids: string[]) =>
    request(`/developer/references/${id}/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_ids }),
    }),
  results: (id: string) =>
    request<Evaluation[]>(`/developer/references/${id}/results`),
  detail: (id: string) => request<Evaluation>(`/developer/evaluations/${id}`),
};
