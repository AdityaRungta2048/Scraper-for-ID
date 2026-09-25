import { DecisionBadge } from "@/components/ui";
import { displayDecision, formatConfidence, platformLabel } from "@/lib/format";
import type { Platform, Row } from "@/services/api";

export default function ResultsTable({
  rows,
  sourcePlatform,
  onSelect,
}: {
  rows: Row[];
  sourcePlatform: Platform | null;
  onSelect: (row: Row) => void;
}) {
  if (!rows.length) {
    return <div className="px-4 py-10 text-center text-sm text-zinc-500">No rows to show.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-zinc-200 text-sm">
        <thead className="bg-zinc-50 text-left text-xs font-semibold uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-4 py-2">Row</th>
            <th className="px-4 py-2">Source Platform</th>
            <th className="px-4 py-2">Country</th>
            <th className="px-4 py-2">Source ID</th>
            <th className="px-4 py-2">Matched ID</th>
            <th className="px-4 py-2">Decision</th>
            <th className="px-4 py-2 text-right">Confidence</th>
            <th className="px-4 py-2">Remarks written</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 bg-white">
          {rows.map((row) => {
            const decision = displayDecision(row);
            const matched = row.status === "MATCH" ? row.output_destination ?? row.matched_id : null;
            const candidate = decision === "REVIEW" ? row.review_candidate : null;
            return (
              <tr
                key={row.original_row}
                onClick={() => onSelect(row)}
                className="cursor-pointer hover:bg-brand-50/50"
                data-testid={`row-${row.original_row}`}
              >
                <td className="px-4 py-2 tabular-nums text-zinc-400">{row.original_row}</td>
                <td className="px-4 py-2">{platformLabel(sourcePlatform)}</td>
                <td className="px-4 py-2 text-zinc-600">{row.country ?? ""}</td>
                <td className="px-4 py-2 font-medium text-zinc-900">{row.source_value ?? <em className="text-zinc-400">empty</em>}</td>
                <td className="px-4 py-2">
                  {matched ? (
                    <span className="font-medium text-emerald-700">{matched}</span>
                  ) : candidate ? (
                    <span className="text-amber-700">candidate: {candidate}</span>
                  ) : (
                    ""
                  )}
                </td>
                <td className="px-4 py-2">
                  <DecisionBadge decision={decision} />
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {decision === "MATCH" || decision === "REVIEW" ? formatConfidence(row.confidence) : "–"}
                </td>
                <td className="px-4 py-2 text-zinc-600">{row.write_remarks ? row.output_remarks ?? "(cleared)" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
