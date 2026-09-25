import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

import Layout from "@/components/Layout";
import UploadDropzone from "@/components/UploadDropzone";
import { Alert, Badge, Button, Card } from "@/components/ui";
import { jobStatusTone, percent, platformLabel } from "@/lib/format";
import { api, type Job, type Platform } from "@/services/api";

export default function Home() {
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [choice, setChoice] = useState<Platform | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);

  useEffect(() => {
    api.listJobs().then(setRecent).catch(() => setRecent([]));
  }, []);

  const onFile = async (file: File) => {
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      const created = await api.upload(file);
      setJob(created);
      setChoice(created.detected_platform);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      await api.start(job.id, choice);
      await router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <Layout>
      <Card className="p-6">
        <h1 className="text-lg font-semibold">Upload a streamer workbook</h1>
        <p className="mb-4 mt-1 text-sm text-zinc-500">
          The processed workbook keeps your exact rows, columns and formatting — only the destination ID and remarks
          cells are filled in.
        </p>
        <UploadDropzone onFile={onFile} disabled={busy} />
        {busy && !job && <p className="mt-3 text-sm text-zinc-500">Validating workbook…</p>}
        {error && (
          <div className="mt-4">
            <Alert tone="danger" title="Upload problem">
              {error}
            </Alert>
          </div>
        )}

        {job && (
          <div className="mt-6 space-y-4 rounded-lg border border-zinc-200 p-4" data-testid="upload-summary">
            <div className="grid gap-4 sm:grid-cols-4">
              <div>
                <div className="text-xs uppercase text-zinc-500">File</div>
                <div className="font-medium">{job.filename}</div>
              </div>
              <div>
                <div className="text-xs uppercase text-zinc-500">Detected platform</div>
                <div className="font-semibold">
                  {job.detected_platform ? platformLabel(job.detected_platform).toUpperCase() : "AMBIGUOUS"}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase text-zinc-500">Rows</div>
                <div className="font-semibold tabular-nums">{job.total_rows}</div>
              </div>
              <div>
                <div className="text-xs uppercase text-zinc-500">Sheet</div>
                <div className="font-medium">
                  {job.sheet_name} (header row {job.header_row})
                </div>
              </div>
            </div>
            {job.detection_note && <p className="text-sm text-zinc-600">{job.detection_note}</p>}
            {job.warnings_json && job.warnings_json.length > 0 && (
              <Alert tone="warning" title="Warnings">
                <ul className="list-disc pl-5">
                  {job.warnings_json.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {job.needs_platform_choice && (
              <div>
                <div className="mb-2 text-sm font-medium">Which platform are the source IDs from?</div>
                <div className="flex gap-2">
                  {(["kick", "twitch"] as Platform[]).map((p) => (
                    <Button key={p} variant={choice === p ? "primary" : "secondary"} onClick={() => setChoice(p)}>
                      {platformLabel(p)} source
                    </Button>
                  ))}
                </div>
              </div>
            )}
            <Button variant="primary" onClick={start} disabled={busy || !choice}>
              Start Processing
            </Button>
          </div>
        )}
      </Card>

      <Card>
        <div className="border-b border-zinc-200 px-6 py-3 text-sm font-semibold">Recent jobs</div>
        {recent.length === 0 ? (
          <p className="px-6 py-6 text-sm text-zinc-500">No jobs yet.</p>
        ) : (
          <ul className="divide-y divide-zinc-100">
            {recent.map((j) => (
              <li key={j.id}>
                <Link href={`/jobs/${j.id}`} className="flex items-center justify-between px-6 py-3 hover:bg-zinc-50">
                  <span>
                    <span className="font-medium">{j.filename}</span>{" "}
                    <span className="text-sm text-zinc-500">
                      · {platformLabel(j.source_platform ?? j.detected_platform)} · {j.total_rows} rows
                    </span>
                  </span>
                  <span className="flex items-center gap-3 text-sm">
                    <span className="tabular-nums text-zinc-500">{percent(j.processed_rows, j.total_rows)}%</span>
                    <Badge tone={jobStatusTone(j.status)}>{j.status}</Badge>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Layout>
  );
}
