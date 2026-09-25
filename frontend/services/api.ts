// Typed client for the FastAPI backend. All calls go to the same origin (/api is proxied),
// so no backend URL or credential ever lives in the browser bundle.

export type Platform = "kick" | "twitch";
export type JobStatus = "UPLOADED" | "QUEUED" | "PROCESSING" | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED";
export type Decision = "MATCH" | "REVIEW" | "NO_MATCH";

export interface VerificationCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface Job {
  id: string;
  filename: string;
  status: JobStatus;
  source_platform: Platform | null;
  detected_platform: Platform | null;
  detection_note: string | null;
  sheet_name: string | null;
  header_row: number | null;
  total_rows: number;
  processed_rows: number;
  match_count: number;
  no_match_count: number;
  review_count: number;
  not_found_count: number;
  error_count: number;
  skipped_count: number;
  error_message: string | null;
  warnings_json: string[] | null;
  verification_json: { ok: boolean; checks: VerificationCheck[]; mismatches: string[] } | null;
  export_stale: boolean;
  matching_engine_version: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  output_available: boolean;
  output_filename: string | null;
  review_filename: string | null;
  needs_platform_choice: boolean;
}

export interface Row {
  original_row: number;
  source_value: string | null;
  country: string | null;
  existing_destination: string | null;
  status: string;
  source_status: string | null;
  target_status: string | null;
  decision: Decision | null;
  confidence: number | null;
  matched_id: string | null;
  review_candidate: string | null;
  output_destination: string | null;
  output_remarks: string | null;
  write_destination: boolean;
  write_remarks: boolean;
  reason: string | null;
  error_message: string | null;
  manual_verdict: string | null;
  attempts: number;
}

export interface SignalOut {
  name: string;
  family: string;
  available: boolean;
  score: number | null;
  points: number;
  strength: string;
  detail: string;
}

export interface ProfileOut {
  platform: Platform;
  username: string;
  user_id?: string | null;
  display_name?: string | null;
  description?: string | null;
  profile_image_url?: string | null;
  profile_url?: string | null;
  category?: string | null;
  stream_title?: string | null;
  language?: string | null;
  tags?: string[];
}

export interface CandidateOut extends ProfileOut {
  discovered_via: string[];
  points: number;
  confidence: number;
  decision: Decision;
  reason: string;
  gates: Record<string, unknown>;
  evidence: Record<string, unknown>;
  signals: SignalOut[];
  manual_verdict?: string;
}

export interface Resolution {
  source_status: string;
  target_status: string | null;
  decision: Decision;
  source_profile: ProfileOut | null;
  source_socials: { kind: string; identity: string; url: string; unique: boolean }[];
  candidates: CandidateOut[];
  reason: string;
  engine_version: string;
  from_cache?: boolean;
  state_case?: string;
}

export interface RowDetail extends Row {
  evidence_json: Resolution | null;
}

export interface RowPage {
  total: number;
  offset: number;
  limit: number;
  items: Row[];
}

export interface ReviewItem {
  original_row: number;
  source_platform: Platform;
  source_value: string | null;
  country: string | null;
  status: string;
  source_status: string | null;
  confidence: number | null;
  reason: string | null;
  manual_verdict: string | null;
  source_profile: ProfileOut | null;
  source_socials: Resolution["source_socials"];
  candidate: CandidateOut | null;
  other_candidates: CandidateOut[];
}

export interface AppConfig {
  twitch_configured: boolean;
  kick_configured: boolean;
  search_engine_fallback: boolean;
  kick_public_profile_enrichment: boolean;
  match_threshold: number;
  review_threshold: number;
  max_candidates: number;
  existing_destination_policy: string;
  queue_backend: string;
  matching_engine_version: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type FetchLike = typeof fetch;
let fetchImpl: FetchLike = (...args) => fetch(...args);
export function setFetch(f: FetchLike): void {
  fetchImpl = f;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetchImpl(`/api${path}`, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  config: () => request<AppConfig>("/config"),
  listJobs: () => request<Job[]>("/jobs"),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Job>("/jobs", { method: "POST", body: form });
  },
  start: (id: string, sourcePlatform?: Platform | null) =>
    request<Job>(`/jobs/${id}/start`, json({ source_platform: sourcePlatform ?? null })),
  cancel: (id: string) => request<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  resume: (id: string) => request<Job>(`/jobs/${id}/resume`, { method: "POST" }),
  retryFailed: (id: string) => request<Job>(`/jobs/${id}/retry-failed`, { method: "POST" }),
  rows: (id: string, params: { offset?: number; limit?: number; decision?: string; status?: string } = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return request<RowPage>(`/jobs/${id}/rows?${q.toString()}`);
  },
  row: (id: string, originalRow: number) => request<RowDetail>(`/jobs/${id}/rows/${originalRow}`),
  reviews: (id: string, includeDecided = false) =>
    request<ReviewItem[]>(`/jobs/${id}/reviews?include_decided=${includeDecided}`),
  submitReview: (
    id: string,
    body: { original_row: number; verdict: "CONFIRM" | "REJECT" | "SKIP"; target_username?: string | null },
  ) => request<{ verdict: string; rows_updated?: number[] }>(`/jobs/${id}/reviews`, json(body)),
  downloadUrl: (id: string) => `/api/jobs/${id}/download`,
  reviewReportUrl: (id: string) => `/api/jobs/${id}/review-report`,
};
