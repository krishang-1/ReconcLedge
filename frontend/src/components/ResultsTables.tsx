import { useState } from "react";
import { Link } from "react-router-dom";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { EXCEPTION_TYPE_DESCRIPTIONS, EXCEPTION_TYPE_LABELS } from "../lib/exceptionTypes";
import type { ExceptionRecord, ExceptionType, MatchedRecord } from "../api/types";

const CONFIDENCE_ORDER = { high: 0, medium: 1, low: 2 } as const;
type ConfidenceFilter = "all" | "high" | "medium" | "low";

export function MatchedRecordsTable({ records }: { records: MatchedRecord[] }) {
  const [filter, setFilter] = useState<ConfidenceFilter>("all");
  const filtered = filter === "all" ? records : records.filter((r) => r.confidence === filter);
  const sorted = [...filtered].sort((a, b) => CONFIDENCE_ORDER[a.confidence] - CONFIDENCE_ORDER[b.confidence]);

  return (
    <div className="ledger-card overflow-hidden">
      <div className="border-b border-ink-text/10 px-6 py-4 flex items-center justify-between">
        <p className="font-mono text-xs text-ink-text/50 uppercase tracking-wide">
          Matched ({filtered.length}{filtered.length !== records.length ? ` of ${records.length}` : ""})
        </p>
        <ConfidenceFilterButtons value={filter} onChange={setFilter} />
      </div>
      {sorted.length === 0 ? (
        <p className="text-ink-text/40 font-mono text-sm px-6 py-8 text-center">No records match this filter.</p>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-paper">
              <tr className="border-b border-ink-text/10 text-left font-mono text-[11px] text-ink-text/40 uppercase tracking-wide">
                <th className="px-6 py-2 font-medium">Transaction</th>
                <th className="px-6 py-2 font-medium">UTR</th>
                <th className="px-6 py-2 font-medium">Method</th>
                <th className="px-6 py-2 font-medium">Confidence</th>
                <th className="px-6 py-2 font-medium">Review</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.transaction_id} className="border-b border-ink-text/5 last:border-0">
                  <td className="px-6 py-2.5 font-mono text-xs">{r.transaction_id}</td>
                  <td className="px-6 py-2.5 font-mono text-xs text-ink-text/60">{r.utrs.join(", ")}</td>
                  <td className="px-6 py-2.5 font-mono text-xs text-ink-text/60">
                    {r.method === "deterministic" ? "Deterministic" : "Agent-verified"}
                  </td>
                  <td className="px-6 py-2.5">
                    <ConfidenceBadge confidence={r.confidence} />
                  </td>
                  <td className="px-6 py-2.5 font-mono text-xs">
                    {r.requires_human_review ? <span className="text-flagged">Flagged</span> : <span className="text-ink-text/30">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ExceptionsTable({ records }: { records: ExceptionRecord[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="ledger-card overflow-hidden">
      <div className="border-b border-ink-text/10 px-6 py-4">
        <p className="font-mono text-xs text-ink-text/50 uppercase tracking-wide">Exceptions ({records.length})</p>
      </div>
      {records.length === 0 ? (
        <p className="text-verified font-mono text-sm px-6 py-8 text-center">
          No exceptions — every record resolved cleanly.
        </p>
      ) : (
        <ul className="divide-y divide-ink-text/8">
          {records.map((r) => {
            const isOpen = expanded === r.transaction_id;
            // Real structural fix made while adding the cross-check
            // link below: the whole row used to be one <button>, with
            // the expanded detail nested INSIDE it. Nesting a <Link>
            // inside a <button> is invalid HTML (an interactive
            // element inside another interactive element) and would
            // have made the Link's click also toggle the row's
            // collapse state at the same time as navigating. Split
            // into a <button> for just the clickable header, with the
            // expanded detail as a sibling <div>, not a descendant of
            // the button - the Link inside it now behaves like any
            // normal link, with no conflicting click handler above it.
            return (
              <li key={r.transaction_id}>
                <button
                  onClick={() => setExpanded(isOpen ? null : r.transaction_id)}
                  className="w-full text-left px-6 py-3 hover:bg-ink-text/[0.02] transition-colors"
                >
                  <div className="flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="font-mono text-sm text-ink-text truncate">{r.transaction_id}</span>
                      <span className="text-[11px] font-mono font-medium uppercase tracking-wide text-flagged bg-flagged-soft rounded px-1.5 py-0.5 shrink-0">
                        {EXCEPTION_TYPE_LABELS[r.type as ExceptionType] ?? r.type}
                      </span>
                    </div>
                    <ConfidenceBadge confidence={r.confidence} />
                  </div>
                </button>
                {isOpen && (
                  <div className="px-6 pb-3 -mt-1 space-y-1.5">
                    <p className="text-xs text-ink-text/50 font-mono">
                      {EXCEPTION_TYPE_DESCRIPTIONS[r.type as ExceptionType] ?? "No description available for this exception type."}
                    </p>
                    <p className="text-xs text-ink-text/70 font-mono pl-3 border-l-2 border-flagged/30">{r.reason}</p>
                    <CrossCheckLink exceptionType={r.type as ExceptionType} transactionId={r.transaction_id} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ConfidenceFilterButtons({ value, onChange }: { value: ConfidenceFilter; onChange: (v: ConfidenceFilter) => void }) {
  const options: ConfidenceFilter[] = ["all", "high", "medium", "low"];
  return (
    <div className="flex items-center gap-1">
      {options.map((opt) => (
        <button
          key={opt}
          onClick={() => onChange(opt)}
          className={`text-[11px] font-mono uppercase tracking-wide px-2 py-1 rounded ${
            value === opt ? "bg-accent text-paper-text" : "text-ink-text/40 hover:text-ink-text/70"
          }`}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

/**
 * Presentation-layer link from an unresolved exception to the relevant
 * standalone tool. Touches no pipeline logic - it pre-fills a form a
 * human still reviews and submits, never an auto-reclassification.
 *
 * Only two exception types get a link, the two with a defensible
 * real-world connection: an unexplained amount gap looks exactly like
 * a partial refund, and no-candidate-found looks exactly like an
 * N-way-batched settlement. The other two get no link rather than a
 * contrived one.
 */
function CrossCheckLink({ exceptionType, transactionId }: { exceptionType: ExceptionType; transactionId: string }) {
  if (exceptionType === "AMOUNT_MISMATCH_UNEXPLAINED") {
    return (
      <Link
        to={`/tools?tab=refunds&transaction_id=${encodeURIComponent(transactionId)}`}
        className="inline-block text-xs font-mono text-accent hover:underline underline-offset-2 pt-1"
      >
        Check against Refunds →
      </Link>
    );
  }
  if (exceptionType === "NO_CANDIDATE_FOUND") {
    return (
      <Link
        to={`/tools?tab=batches&transaction_id=${encodeURIComponent(transactionId)}`}
        className="inline-block text-xs font-mono text-accent hover:underline underline-offset-2 pt-1"
      >
        Check if part of a settlement batch →
      </Link>
    );
  }
  return null;
}
