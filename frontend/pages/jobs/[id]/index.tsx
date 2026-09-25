import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";

import EvidencePanel from "@/components/EvidencePanel";
import Layout from "@/components/Layout";
import ResultsTable from "@/components/ResultsTable";
import { Alert, Badge, Button, Card, LinkButton, ProgressBar, Stat } from "@/components/ui";
import { FINISHED_STATUSES, isActive, jobStatusTone, otherPlatform, percent, platformLabel } from "@/lib/format";
import { api, type Job, type Row, type RowDetail } from "@/services/api";

const FILTERS: { key: string; label: string; params: { decision?: string; status?: string } }[] = [
  { key: "all", label: "All", params: {} },
  { key: "match", label: "Matches", params: { status: "MATCH" } },
  { key: "review", label: "Review", params: { decision: "REVIEW" } },
  { key: "nomatch", label: "No match", params: { status: "NO_MATCH" } },
  { key: "notfound", label: "Not found", params: { status: "SOURCE_NOT_FOUND" } },
  { key: "errors", label: "Errors", params: { status: "ERROR" } },
];
const PAGE = 100;

export default function JobPage() {
  const router = useRouter();
  const id = typeof router.query.id === "string" ? router.query.id : null;
  const [job, setJob] = useState<Job | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<RowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    const f = FILTERS.find((x) => x.key === filter)!;
    const [j, page] = await Promise.all([api.getJob(id), api.rows(id, { ...f.params, offset, limit: PAGE })]);
    setJob(j);
    setRows(page.items);
    setTotal(page.total);
  }, [id, filter, offset]);

  const refresh = useCallback(() => {
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  useEffect(() => {
    // subscribe: initial fetch; state is only set in the async callback
    const t = setTimeout(refresh, 0);
    return () => clearTimeout(t);
  }, [refresh]);

  useEffect(() => {
    if (!job || !isActive(job)) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [job, refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openRow = async (row: Row) => {
    if (!id) return;
    try {
      setDetail(await api.row(id, row.original_row));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!job) {
    return (
      <Layout>
        {error ? <Alert tone="danger" title="Could not load job">{error}</Alert> : <p className="text-zinc-500">Loading…</p>}
      </Layout>
    );
  }

  const pct = percent(job.processed_rows, job.total_rows);
  const finished = FINISHED_STATUSES.includes(job.status);
  const successful = job.processed_rows - job.error_count;

  return (
    <Layout>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/" className="text-sm text-brand-600 hover:underline">
            ← All jobs
          </Link>
          <h1 className="mt-1 text-xl font-semibold">{job.filename}</h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-zinc-600">
            <Badge tone={jobStatusTone(job.status)}>{job.status}</Badge>
            <span>
              {platformLabel(job.source_platform)} → {platformLabel(otherPlatform(job.source_platform))}
            </span>
            <span>· engine v{job.matching_engine_version}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {isActive(job) && (
            <Button variant="danger" disabled={busy} onClick={() => act(() => api.cancel(job.id))}>
              Cancel
            </Button>
          )}
          {(job.status === "FAILED" || job.status === "CANCELLED") && (
            <Button disabled={busy} onClick={() => act(() => api.resume(job.id))}>
              Resume
            </Button>
          )}
        </div>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}
      {job.status === "FAILED" && job.error_message && (
        <Alert tone="danger" title="Job failed">
          {job.error_message}
        </Alert>
      )}

      <Card className="space-y-4 p-6">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Progress</span>
          <span className="tabular-nums text-zinc-600">{pct}%</span>
        </div>
        <ProgressBar value={pct} />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Stat label="Processed" value={`${job.processed_rows} / ${job.total_rows}`} />
          <Stat label="Confirmed matches" value={job.match_count} tone="success" />
          <Stat label="No match" value={job.no_match_count} />
          <Stat label="Review" value={job.review_count} tone={job.review_count ? "warning" : undefined} />
          <Stat label="Errors" value={job.error_count} tone={job.error_count ? "danger" : undefined} />
        </div>
      </Card>

      {finished && (
        <Card className="space-y-4 p-6" data-testid="completion">
          <div>
            <h2 className="text-lg font-semibold">
              {job.status === "COMPLETED" ? "Processing Complete" : "Processing finished with temporary errors"}
            </h2>
            <p className="mt-1 text-sm text-zinc-600">
              Rows processed: {job.processed_rows} · Successful: {successful} · Matches: {job.match_count} · No match:{" "}
              {job.no_match_count} · Review: {job.review_count} · Errors: {job.error_count}
            </p>
          </div>
          {job.status === "PARTIAL" && (
            <Alert tone="warning" title={`${job.error_count} row(s) could not be checked (API/network errors)`}>
              These rows were left unchanged in the workbook — they were <strong>not</strong> marked as “no match”.
              Retry them once the platform is reachable.
            </Alert>
          )}
          {job.export_stale && (
            <Alert tone="info">Review decisions changed some rows — the workbook will be regenerated and re-verified on download.</Alert>
          )}
          <div className="flex flex-wrap gap-3">
            <LinkButton variant="primary" href={api.downloadUrl(job.id)} download={job.output_filename ?? undefined}>
              ⬇ DOWNLOAD PROCESSED EXCEL
            </LinkButton>
            <LinkButton href={api.reviewReportUrl(job.id)} download={job.review_filename ?? undefined}>
              Download Review Report
            </LinkButton>
            {job.review_count > 0 && (
              <Link
                href={`/jobs/${job.id}/review`}
                className="inline-flex items-center rounded-lg bg-amber-100 px-4 py-2 text-sm font-semibold text-amber-900 hover:bg-amber-200"
              >
                Review {job.review_count} uncertain row(s)
              </Link>
            )}
            {job.error_count > 0 && (
              <Button disabled={busy} onClick={() => act(() => api.retryFailed(job.id))}>
                Retry Failed Rows
              </Button>
            )}
          </div>
          <div className="text-xs text-zinc-500">Output file: {job.output_filename}</div>
          {job.verification_json && (
            <details className="text-sm">
              <summary className="cursor-pointer text-zinc-600">
                Output verification: {job.verification_json.ok ? "✔ passed" : "✖ failed"} (
                {job.verification_json.checks.length} checks)
              </summary>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">
                {job.verification_json.checks.map((c) => (
                  <li key={c.name} className={c.ok ? "text-emerald-700" : "text-rose-700"}>
                    {c.ok ? "✔" : "✖"} {c.name}
                    {c.detail ? <span className="text-zinc-500"> — {c.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </Card>
      )}

      <Card>
        <div className="flex flex-wrap items-center gap-1 border-b border-zinc-200 px-4 py-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => {
                setFilter(f.key);
                setOffset(0);
              }}
              className={`rounded-md px-3 py-1.5 text-sm ${filter === f.key ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100"}`}
            >
              {f.label}
            </button>
          ))}
          <span className="ml-auto text-xs text-zinc-500">Rows are shown in original Excel order · click a row for evidence</span>
        </div>
        <ResultsTable rows={rows} sourcePlatform={job.source_platform} onSelect={openRow} />
        {total > PAGE && (
          <div className="flex items-center justify-between border-t border-zinc-200 px-4 py-2 text-sm">
            <span className="text-zinc-500">
              {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
            </span>
            <span className="flex gap-2">
              <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </Button>
              <Button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </Button>
            </span>
          </div>
        )}
      </Card>

      {detail && <EvidencePanel row={detail} onClose={() => setDetail(null)} />}
    </Layout>
  );
}
