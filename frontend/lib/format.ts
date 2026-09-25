import type { Job, JobStatus, Platform, Row } from "@/services/api";

export const ACTIVE_STATUSES: JobStatus[] = ["QUEUED", "PROCESSING"];
export const FINISHED_STATUSES: JobStatus[] = ["COMPLETED", "PARTIAL"];

export function isActive(job: Pick<Job, "status">): boolean {
  return ACTIVE_STATUSES.includes(job.status);
}

export function platformLabel(p: Platform | null | undefined): string {
  if (p === "kick") return "Kick";
  if (p === "twitch") return "Twitch";
  return "—";
}

export function otherPlatform(p: Platform | null | undefined): Platform | null {
  if (p === "kick") return "twitch";
  if (p === "twitch") return "kick";
  return null;
}

export function percent(done: number, total: number): number {
  if (!total) return 0;
  return Math.min(100, Math.round((done / total) * 100));
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return `${Math.round(value)}%`;
}

export function formatScore(value: unknown): string {
  if (typeof value === "number") return `${Math.round(value * 100)}%`;
  if (typeof value === "boolean") return value ? "MATCH" : "no";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  return value === null || value === undefined ? "n/a" : String(value);
}

export type DisplayDecision = "MATCH" | "REVIEW" | "NO_MATCH" | "NOT_FOUND" | "ERROR" | "SKIPPED" | "PENDING";

/** Collapse the internal row status + decision into what the results table shows. */
export function displayDecision(row: Pick<Row, "status" | "decision" | "manual_verdict">): DisplayDecision {
  const s = row.status;
  if (s === "PENDING" || s === "SOURCE_EXISTS") return "PENDING";
  if (["API_ERROR", "RATE_LIMITED", "TEMPORARY_ERROR", "PROCESSING_ERROR"].includes(s)) return "ERROR";
  if (s === "SKIPPED_EMPTY" || s === "PRESERVED") return "SKIPPED";
  if (s === "MATCH") return "MATCH";
  if (row.decision === "REVIEW" && !row.manual_verdict) return "REVIEW";
  if (s === "SOURCE_NOT_FOUND") return "NOT_FOUND";
  return "NO_MATCH";
}

export function jobStatusTone(status: JobStatus): "neutral" | "info" | "success" | "warning" | "danger" {
  switch (status) {
    case "COMPLETED":
      return "success";
    case "PARTIAL":
      return "warning";
    case "FAILED":
      return "danger";
    case "QUEUED":
    case "PROCESSING":
      return "info";
    default:
      return "neutral";
  }
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
