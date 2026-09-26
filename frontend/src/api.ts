/** Fetch client. Capabilities from the server are displayed, never used as authorisation (U-3). */

const CSRF_COOKIE = "csrftoken";

export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;

  constructor(message: string, status: number, body: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function csrfToken(): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const token = csrfToken();
  if (token) headers.set("X-CSRFToken", token);
  const response = await fetch(path, { ...init, headers, credentials: "include" });
  if (response.status === 204) return undefined as T;
  const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    throw new ApiError(String(body.detail || body.title || response.statusText), response.status, body);
  }
  return body as T;
}

export type SessionUser = {
  public_id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  role: string;
  mfa_enforced: boolean;
  is_active: boolean;
};

export type Session = {
  user: SessionUser;
  capabilities: string[];
  mfa_required?: boolean;
  enrol_required?: boolean;
};

export type LoginResult = {
  user: SessionUser | null;
  mfa_required: boolean;
  enrol_required?: boolean;
  capabilities?: string[];
  methods?: string[];
};

export type CaseRow = {
  public_id: string;
  name: string;
  reference: string;
  description: string;
  status: string;
  opened_at: string;
  closed_at: string | null;
};

export type CorpusRow = {
  id: number;
  code: string;
  name: string;
  description: string;
  data_classification: string;
  is_active: boolean;
  evidence_count?: number;
  indexed_count?: number;
};

export type MountRow = {
  id: number;
  corpus: number;
  corpus_code: string;
  path: string;
  label: string;
  is_enabled: boolean;
  created_at?: string;
  last_scan_at: string | null;
  last_scan_status: string | null;
  last_scan_files_seen: number;
  last_scan_error_count: number;
};

export type EncoderRow = {
  id: number;
  name: string;
  family: string;
  version: string;
  preprocess_version: string;
  dimensions: number;
  is_active: boolean;
};

export type EvidenceRow = {
  public_id: string;
  corpus: number;
  corpus_code?: string;
  case: number | null;
  original_filename: string;
  source_path?: string;
  state: string;
  registered_at: string;
  thumbnail_access?: string;
};

export type AccessGrant = {
  url: string;
  expires_at: string;
  variant: string;
};

export type RecallRow = {
  encoder_id: number;
  encoder_name: string;
  family: string;
  measured_recall: string | null;
  recall_k: number | null;
  measured_at: string | null;
  m: number | null;
  ef_construction: number | null;
  ef_search: number | null;
};

export type RunRow = {
  public_id: string;
  label: string;
  status: string;
  top_k: number;
  model_weight: string;
  metadata_weight: string;
  created_at?: string;
  query_count?: number;
  saved?: boolean;
};

export type QueryRow = {
  public_id: string;
  sequence: number;
  query_type: string;
  status: string;
  result_count: number;
  routed_spectrum: string | null;
  query_text?: string | null;
  probe_filename?: string | null;
  probe_evidence_id?: string | null;
};

export type ResultEvidence = {
  public_id: string;
  original_filename: string;
  source_path: string;
  corpus_code: string;
  state: string;
  thumbnail_access: string;
};

export type ResultRow = {
  public_id: string;
  rank: number;
  score_fused: string | number | null;
  score_model: string | number | null;
  score_clip: string | number | null;
  score_dinov2: string | number | null;
  score_metadata?: string | number | null;
  evidence_file: number;
  evidence?: ResultEvidence;
  approval_state?: string | null;
};

export type TaskRow = {
  public_id: string;
  task_name: string;
  status: string;
  phase: string | null;
  progress_current: number;
  progress_total: number | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type AuditRow = {
  public_id: string;
  occurred_at: string;
  action: string;
  actor_username: string;
  actor_role: string;
  target_type: string;
  target_public_id: string | null;
  outcome: string;
};

export type ReportRow = {
  public_id: string;
  format: string;
  status: string;
  available_at: string | null;
  expires_at: string | null;
  download_count: number;
};

export type SettingRow = {
  id: number;
  key: string;
  value: unknown;
  description: string;
  updated_at: string;
};

export type Page<T> = { results: T[]; next: string | null; previous: string | null };

export function rowsOf<T>(data: T[] | { results?: T[] } | undefined | null): T[] {
  if (!data) return [];
  return Array.isArray(data) ? data : data.results ?? [];
}
