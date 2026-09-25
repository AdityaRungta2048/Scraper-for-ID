import ProfileCard from "@/components/ProfileCard";
import { Badge, DecisionBadge } from "@/components/ui";
import { displayDecision, formatConfidence, formatScore, humanize } from "@/lib/format";
import type { CandidateOut, RowDetail, SignalOut } from "@/services/api";

const EVIDENCE_KEYS = [
  "username_similarity",
  "display_name_similarity",
  "profile_image_similarity",
  "explicit_cross_link",
  "social_link_match",
  "shared_social_accounts",
  "instagram_match",
  "youtube_match",
  "bio_similarity",
  "content_similarity",
  "country_match",
];

function strengthTone(s: string) {
  if (s === "strong") return "success" as const;
  if (s === "supporting") return "info" as const;
  if (s === "negative" || s === "conflict") return "danger" as const;
  return "neutral" as const;
}

export function SignalTable({ signals }: { signals: SignalOut[] }) {
  return (
    <table className="w-full text-xs">
      <thead className="text-left text-zinc-500">
        <tr>
          <th className="py-1 pr-2">Signal</th>
          <th className="py-1 pr-2">Strength</th>
          <th className="py-1 pr-2 text-right">Points</th>
          <th className="py-1">Detail</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-zinc-100">
        {signals.map((s) => (
          <tr key={s.name} className={s.available ? "" : "text-zinc-400"}>
            <td className="py-1 pr-2 font-medium">{humanize(s.name)}</td>
            <td className="py-1 pr-2">{s.available ? <Badge tone={strengthTone(s.strength)}>{s.strength}</Badge> : "n/a"}</td>
            <td className="py-1 pr-2 text-right tabular-nums">{s.points ? s.points.toFixed(1) : "0"}</td>
            <td className="py-1">{s.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EvidenceSummary({ evidence }: { evidence: Record<string, unknown> }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
      {EVIDENCE_KEYS.filter((k) => k in evidence).map((k) => (
        <div key={k} className="contents">
          <dt className="text-zinc-500">{humanize(k)}</dt>
          <dd className="font-medium tabular-nums">{formatScore(evidence[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

function CandidateBlock({ c, highlight }: { c: CandidateOut; highlight: boolean }) {
  return (
    <details open={highlight} className="rounded-lg border border-zinc-200 bg-white">
      <summary className="flex cursor-pointer items-center justify-between gap-2 px-3 py-2">
        <span className="flex items-center gap-2">
          <span className="font-semibold">{c.username}</span>
          <DecisionBadge decision={c.decision} />
          {c.manual_verdict && <Badge tone="info">manual: {c.manual_verdict}</Badge>}
        </span>
        <span className="text-sm tabular-nums text-zinc-600">{formatConfidence(c.confidence)}</span>
      </summary>
      <div className="space-y-3 border-t border-zinc-100 px-3 py-3">
        <p className="text-sm text-zinc-700">{c.reason}</p>
        <div className="text-xs text-zinc-500">Discovered via: {c.discovered_via.join(", ") || "—"}</div>
        <EvidenceSummary evidence={c.evidence} />
        <SignalTable signals={c.signals} />
      </div>
    </details>
  );
}

export default function EvidencePanel({ row, onClose }: { row: RowDetail; onClose: () => void }) {
  const res = row.evidence_json;
  const decision = displayDecision(row);
  const best = res?.candidates.find((c) => c.username === (row.matched_id ?? "").toLowerCase() || c.username === row.review_candidate);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-zinc-900/30" onClick={onClose}>
      <aside
        className="h-full w-full max-w-3xl overflow-y-auto bg-zinc-50 p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
        aria-label="Row evidence"
      >
        <div className="flex items-start justify-between">
          <div>
            <div className="text-xs uppercase tracking-wide text-zinc-500">Excel row {row.original_row}</div>
            <h2 className="text-xl font-semibold">{row.source_value ?? "(empty source)"}</h2>
          </div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-zinc-500 hover:bg-zinc-200" aria-label="Close">
            ✕
          </button>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <DecisionBadge decision={decision} />
          <Badge>status: {row.status}</Badge>
          {row.confidence !== null && <Badge>confidence {formatConfidence(row.confidence)}</Badge>}
          {res?.state_case && <Badge>case {res.state_case}</Badge>}
          {res?.from_cache && <Badge tone="info">cached result</Badge>}
        </div>

        <div className="mt-4 rounded-lg bg-white p-4 text-sm ring-1 ring-zinc-200">
          <div className="font-semibold">Reason</div>
          <p className="mt-1 text-zinc-700">{row.reason ?? row.error_message ?? "—"}</p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-zinc-600">
            <div>
              Destination cell:{" "}
              <strong>{row.write_destination ? row.output_destination ?? "(cleared)" : "unchanged"}</strong>
            </div>
            <div>
              Remarks cell: <strong>{row.write_remarks ? row.output_remarks ?? "(cleared)" : "unchanged"}</strong>
            </div>
          </div>
        </div>

        {res && (
          <div className="mt-4 flex flex-col gap-3 md:flex-row">
            <ProfileCard title="Source profile" profile={res.source_profile} country={row.country} socials={res.source_socials} />
            <ProfileCard
              title={decision === "MATCH" ? "Matched account" : "Best candidate"}
              profile={best ?? null}
              missingText="No candidate selected"
            />
          </div>
        )}

        {res && res.candidates.length > 0 && (
          <div className="mt-6 space-y-2">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Candidates evaluated ({res.candidates.length})
            </h3>
            {res.candidates.map((c) => (
              <CandidateBlock key={c.username} c={c} highlight={c === best} />
            ))}
          </div>
        )}
        {res && (
          <p className="mt-6 text-xs text-zinc-400">Matching engine v{res.engine_version}</p>
        )}
      </aside>
    </div>
  );
}
