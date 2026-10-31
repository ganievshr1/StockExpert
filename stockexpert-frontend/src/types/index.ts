export type NavTab =
  | "dashboard"
  | "overview"
  | "data"
  | "processing"
  | "taxonomy"
  | "shortlist"
  | "reports"
  | "audit"
  | "metrics"
  | "quality"
  | "developer";
export type Role = "admin" | "dmto_expert" | "buyer" | string;
export interface User {
  id: number;
  username: string;
  role: Role;
  organization_id: number;
}
export interface LLMConfig {
  profile_id: string;
  label: string;
  provider: "deepseek" | "ollama";
  model: string;
  thinking: boolean;
  reasoning_effort: string;
  max_tokens: number;
  num_ctx: number;
  concurrency: number;
  timeout: number;
  quantization: string | null;
}
export interface LLMModel extends LLMConfig {
  external_processing: boolean;
  availability: { status: string; available: boolean; message: string };
  pull_command: string | null;
}
export interface LLMModels {
  default_profile_id: string;
  ollama_version: string | null;
  models: LLMModel[];
}
export interface SystemConfig {
  api_contract_version: string;
  ui_contract_version?: string;
  llm_provider: "deepseek" | "ollama";
  llm_model: string;
  external_processing: boolean;
  api_key_configured: boolean | null;
  concurrency: number;
  embedding_model: string;
  reranker_enabled: boolean;
  default_llm_config: LLMConfig;
  default_normalization_method?: "llm" | "regex";
  model_selection_supported: boolean;
}
export interface Feature {
  key: string;
  label: string;
  kind: "string" | "number" | "integer";
  unit: string;
  comparison: "eq" | "gte" | "lte" | "informational";
  tolerance: number;
  aliases?: string[];
}
export interface Category {
  id: string;
  name: string;
  parent_id: string | null;
  feature_keys?: string[];
  aliases?: string[];
  item_count?: number;
  requirement_count?: number;
}
export interface Mapping {
  document_id: string;
  sheet: string;
  header_row: number;
  name_columns: string[];
  category_column: string | null;
}
export interface SchemaDraft {
  features: Feature[];
  categories: Category[];
  mappings: Mapping[];
  taxonomy_mode?: "sample" | "passports";
  mapping_approved?: boolean;
  taxonomy_status?: "pending" | "review" | "approved" | "skipped";
  analyzed_items?: number;
}
export interface ProcurementSession {
  id: string;
  title: string;
  status: string;
  progress: number;
  approved: boolean;
  schema_doc: Partial<SchemaDraft>;
  taxonomy_version: string | null;
  created_at: string;
  updated_at?: string;
  llm_config: LLMConfig;
  llm_locked: boolean;
  taxonomy_enabled?: boolean;
  normalization_method?: "llm" | "regex";
  normalization_locked?: boolean;
  llm_legacy: boolean;
  base_snapshot?: {
    source_session_id: string;
    source_title: string;
    normalized_rows: number;
    source_rows?: number;
    reused_passports?: boolean;
    source_llm_config?: LLMConfig;
    copied_at: string;
  };
}
export interface DocumentRecord {
  id: string;
  filename: string;
  kind: "residual" | "requirement";
  meta: Record<string, unknown>;
}
export interface JobStatus {
  id?: string;
  status: string;
  stage?: string;
  progress: number;
  session_status?: string;
  error?: string | null;
  kind?: string;
  params?: {
    llm_config?: LLMConfig;
    runtime?: Partial<LLMConfig>;
    normalization_config?: { method: "llm" | "regex"; rules_version?: string };
  };
}
export interface ProgressEventData {
  status: string;
  progress: number;
  job: JobStatus;
}
export interface Metrics {
  total_rows: number;
  normalized_items: number;
  shortlisted_pairs: number;
  contradicted_pairs: number;
  time_saved_hours: null;
  potential_savings_rub: null;
}
export interface Passport {
  name?: string;
  name_evidence?: string;
  material_id?: string | null;
  normalized_features?: Feature[];
  normalized_attributes?: Record<string, string | number | null>;
  attributes: Record<string, string | number | null>;
  evidence: Record<
    string,
    {
      quote: string;
      raw_value: string;
      source_unit: string;
      target_unit: string;
    }
  >;
  category_id: string | null;
  category_evidence?: string | null;
  provenance?: Record<string, unknown>;
}
export interface Item {
  sheet?: string;
  row_index?: number;
  document_id?: string;
  kind?: string;
  stock_id?: string | null;
  stock_state?: "AVAILABLE" | "USED" | "CLEARED";
  id: string;
  source_text: string;
  passport: Passport | null;
  issues: { code: string; key?: string }[];
}
export interface Diff {
  field: string;
  label: string;
  required: string | number;
  actual: string | number;
  operator: string;
  unit: string;
  tolerance: number;
}
export type MatchState = "NEEDS_REVIEW" | "CONTRADICTED" | "PASSES_KNOWN_RULES";
export type ExpertDecision = "RECOMMENDED" | "NEEDS_REVIEW" | "REJECTED_MANUAL";
export interface CandidateItem {
  id: string;
  item_id: string;
  requirement_id: string;
  state: MatchState;
  is_shortlisted: boolean;
  rank: number | null;
  rrf_score: number | null;
  cross_encoder_score: number | null;
  reason: string;
  missing_fields: string[];
  diff: Diff[];
  category_review?: boolean;
  decision: ExpertDecision | "PENDING";
  expert_comment: string | null;
  residual: Item;
  requirement: Item;
}
export interface Page<T> {
  total: number;
  items: T[];
}
export interface ReportSummary {
  title: string;
  document_status: string;
  pairs_evaluated: number;
  shortlisted_pairs: number;
  contradicted_pairs: number;
  needs_review_pairs: number;
  savings_rub: null;
}
export interface AuditEvent {
  id: number;
  user_id: number | null;
  event_type: string;
  event_details: Record<string, unknown>;
  created_at: string;
  prev_hash: string;
  sha256_hash: string;
}
export interface Snapshot {
  session: ProcurementSession;
  documents: DocumentRecord[];
  metrics: Metrics;
  job: JobStatus;
}

export interface NormalizedBase {
  session_id: string;
  title: string;
  normalized_rows: number;
  taxonomy_version: string | null;
  created_at: string;
  features: string[];
}
