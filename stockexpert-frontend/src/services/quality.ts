import { request } from "./api";
import type { Item } from "../types";
export interface Quality {
  job_id: string | null;
  taxonomy_enabled?: boolean;
  recall_stage?: string;
  finalized: boolean;
  finalized_at: string | null;
  f1: number | null;
  text_f1?: number | null;
  text_precision?: number | null;
  text_recall?: number | null;
  accuracy: number | null;
  precision_shortlist: number | null;
  recall_at_150: number | null;
  coverage: Record<string, number>;
  fields: {
    key: string;
    f1: number | null;
    checked: number;
    tp: number;
    fp: number;
    fn: number;
  }[];
  note?: string;
}
export interface Answer {
  attributes?: Record<string, string | number | null>;
  custom_features?: {key:string;label:string;unit:string;aliases:string[]}[];
  category_reviewed?: boolean;
  category_id?: string | null;
  expected_category?: string | null;
  suitable?: boolean;
  comment?: string;
}
export interface QualityTarget {
  target: Item & {
    raw?: Record<string, unknown>;
    kind?: string;
    sheet?: string;
    row_index?: number;
    result?: { rank: number | null; is_shortlisted: boolean };
    residual?: Item;
    requirement?: Item;
  };
  review: { answer: Answer; updated_at: string; reviewer_id: number } | null;
}
export interface Targets {
  job_id: string;
  total: number;
  items: QualityTarget[];
  schema?: import("../types").SchemaDraft;
}
export const qualityApi = {
  summary: (sid: string, job?: string) => request<Quality>(`/quality/${sid}${job ? `?job_id=${job}&historical=true` : ""}`),
  targets: (
    sid: string,
    kind: string,
    offset: number,
    shortlisted: boolean,
    extra: Record<string, string> = {},
  ) =>
    request<Targets>(
      `/quality/${sid}/targets?${new URLSearchParams({ kind, offset: String(offset), limit: "10", shortlisted: String(shortlisted), ...extra })}`,
    ),
  save: (sid: string, job: string, kind: string, id: string, answer: Answer) =>
    request(`/quality/${sid}/${kind}/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job, historical: true, ...answer }),
    }),
  remove: (sid: string, job: string, kind: string, id: string) =>
    request(`/quality/${sid}/${kind}/${id}?job_id=${job}&historical=true`, {
      method: "DELETE",
    }),
  finalize: (sid: string, job: string) =>
    request<Quality>(`/quality/${sid}/finalize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: job, historical: true }),
    }),
};
export const percent = (n: number | null | undefined) =>
  n == null
    ? "Не измерено"
    : `${(n * 100).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
