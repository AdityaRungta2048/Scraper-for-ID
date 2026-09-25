import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";

import { EvidenceSummary, SignalTable } from "@/components/EvidencePanel";
import Layout from "@/components/Layout";
import ProfileCard from "@/components/ProfileCard";
import { Alert, Badge, Button, Card, LinkButton } from "@/components/ui";
import { formatConfidence, platformLabel } from "@/lib/format";
import { api, type Job, type ReviewItem } from "@/services/api";

export default function ReviewPage() {
  const router = useRouter();
  const id = typeof router.query.id === "string" ? router.query.id : null;
  const [job, setJob] = useState<Job | null>(null);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [skipped, setSkipped] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [decided, setDecided] = useState(0);

  const load = useCallback(async () => {
    if (!id) return;
    const [j, r] = await Promise.all([api.getJob(id), api.reviews(id)]);
    setJob(j);
    setItems(r);
  }, [id]);

  useEffect(() => {
    const t = setTimeout(() => load().catch((e: Error) => setError(e.message)), 0);
    return () => clearTimeout(t);
  }, [load]);

  const queue = items.filter((i) => !skipped.has(i.original_row));
  const current = queue[0];

  const decide = async (verdict: "CONFIRM" | "REJECT" | "SKIP") => {
    if (!id || !current) return;
    if (verdict === "SKIP") {
      setSkipped(new Set([...skipped, current.original_row]));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.submitReview(id, {
        original_row: current.original_row,
        verdict,
        target_username: current.candidate?.username,
      });
      setDecided((d) => d + 1);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Layout>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          {id && (
            <Link href={`/jobs/${id}`} className="text-sm text-brand-600 hover:underline">
              ← Back to job
            </Link>
          )}
          <h1 className="mt-1 text-xl font-semibold">Review uncertain matches</h1>
          <p className="text-sm text-zinc-500">
            {job?.filename} · {queue.length} remaining{skipped.size ? ` · ${skipped.size} skipped` : ""}
          </p>
        </div>
        {id && decided > 0 && (
          <LinkButton variant="primary" href={api.downloadUrl(id)} download={job?.output_filename ?? undefined}>
            ⬇ Download updated Excel
          </LinkButton>
        )}
      </div>

      <Alert tone="info">
        Confirming writes the candidate&apos;s ID into the processed workbook for this row (and any duplicate rows of the
        same source ID). Rejecting stores a negative decision. Decisions are stored as explicit overrides — they never
        change the scoring weights.
      </Alert>
      {error && <Alert tone="danger">{error}</Alert>}

      {!current ? (
        <Card className="p-8 text-center text-zinc-600">
          {items.length === 0 ? "Nothing left to review." : "All remaining items were skipped."}
        </Card>
      ) : (
        <Card className="space-y-5 p-6" data-testid="review-item">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>Excel row {current.original_row}</Badge>
            <Badge tone="warning">REVIEW</Badge>
            <Badge>confidence {formatConfidence(current.candidate?.confidence ?? current.confidence)}</Badge>
            {current.source_status === "NOT_FOUND" && (
              <Badge tone="danger">source {platformLabel(current.source_platform)} account not found</Badge>
            )}
          </div>
          <div className="flex flex-col gap-4 md:flex-row">
            <ProfileCard
              title={`Source · ${platformLabel(current.source_platform)}: ${current.source_value}`}
              profile={current.source_profile}
              country={current.country}
              socials={current.source_socials}
              missingText="The source account does not exist on this platform."
            />
            <ProfileCard title="Candidate" profile={current.candidate} missingText="No candidate" />
          </div>
          {current.candidate && (
            <>
              <div className="rounded-lg bg-zinc-50 p-4">
                <div className="mb-2 text-sm font-semibold">Why this needs review</div>
                <p className="text-sm text-zinc-700">{current.candidate.reason || current.reason}</p>
              </div>
              <div className="grid gap-6 md:grid-cols-2">
                <div>
                  <div className="mb-2 text-sm font-semibold">Evidence</div>
                  <EvidenceSummary evidence={current.candidate.evidence} />
                </div>
                <div>
                  <div className="mb-2 text-sm font-semibold">Signals</div>
                  <SignalTable signals={current.candidate.signals} />
                </div>
              </div>
              {current.other_candidates.length > 1 && (
                <p className="text-sm text-amber-700">
                  Other plausible candidates:{" "}
                  {current.other_candidates
                    .filter((c) => c.username !== current.candidate?.username)
                    .map((c) => `${c.username} (${formatConfidence(c.confidence)})`)
                    .join(", ")}
                </p>
              )}
            </>
          )}
          <div className="flex flex-wrap gap-3">
            <Button variant="primary" disabled={busy || !current.candidate} onClick={() => decide("CONFIRM")}>
              Confirm Match
            </Button>
            <Button variant="danger" disabled={busy || !current.candidate} onClick={() => decide("REJECT")}>
              Reject Match
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => decide("SKIP")}>
              Skip
            </Button>
          </div>
        </Card>
      )}
    </Layout>
  );
}
