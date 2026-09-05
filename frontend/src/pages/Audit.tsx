import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, getAudit } from "../api/client";
import { ConfidenceBadge } from "../components/ConfidenceBadge";
import { formatTimestamp, truncateRunId } from "../lib/format";
import type { AuditRow } from "../api/types";

/**
 * Audit trail search - Stage 6 Phase 4 (see docs/STAGE_6_FRONTEND_PLAN.md).
 * Maps to GET /audit?transaction_id=...&run_id=... (api/app.py), which
 * queries the audit_log table directly - see api/jobs.py's
 * _record_audit_entries()'s own docstring for why this is the real
 * audit-review question ("what happened to transaction X, ever") as
 * opposed to /runs/{id}/results, which stops being answerable the
 * moment you no longer know which run_id to ask about.
 */
export function Audit() {
  const [transactionId, setTransactionId] = useState("");
  const [runId, setRunId] = useState("");
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);

  const search = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const results = await getAudit({
        transaction_id: transactionId.trim() || undefined,
        run_id: runId.trim() || undefined,
      });
      setRows(results);
      setHasSearched(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Audit trail</p>
      <h1 className="page-heading mb-2">Search decision history</h1>
      <p className="text-sm text-paper-text/50 mb-6 max-w-2xl">
        Every decision ever recorded for a transaction, across every run it's ever appeared in - this table has
        no update or delete path in the backend at all, by design; what's shown here is genuinely immutable.
      </p>

      <form onSubmit={search} className="ledger-card px-6 py-5 mb-6 flex items-end gap-4">
        <div className="flex-1">
          <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">Transaction ID</label>
          <input
            type="text"
            value={transactionId}
            onChange={(e) => setTransactionId(e.target.value)}
            placeholder="txn_..."
            className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <div className="flex-1">
          <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">Run ID <span className="normal-case text-ink-text/40">(optional)</span></label>
          <input
            type="text"
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            placeholder="Filter to one run"
            className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="btn-primary shrink-0"
        >
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {error && <p className="text-flagged font-mono text-sm mb-6">{error}</p>}

      {!hasSearched ? (
        <p className="text-paper-text/30 font-mono text-sm text-center py-8">
          Search by transaction ID, or leave it blank and set a run ID to see every decision from one run.
        </p>
      ) : rows && rows.length === 0 ? (
        <p className="text-paper-text/30 font-mono text-sm text-center py-8">
          No audit entries match this search.
        </p>
      ) : rows ? (
        <AuditTimeline rows={rows} />
      ) : null}
    </div>
  );
}

function AuditTimeline({ rows }: { rows: AuditRow[] }) {
  return (
    <div className="ledger-card overflow-hidden">
      <div className="border-b border-ink-text/10 px-6 py-4">
        <p className="font-mono text-xs text-ink-text/50 uppercase tracking-wide">{rows.length} entries</p>
      </div>
      <ul className="divide-y divide-ink-text/8">
        {rows.map((row, i) => (
          <li key={`${row.run_id}-${row.transaction_id}-${i}`} className="px-6 py-3">
            <div className="flex items-center justify-between gap-4 mb-1">
              <div className="flex items-center gap-2.5 min-w-0">
                <span
                  className={`text-[11px] font-mono font-medium uppercase tracking-wide rounded px-1.5 py-0.5 shrink-0 ${
                    row.decision_type === "matched" ? "bg-verified-soft text-verified" : "bg-flagged-soft text-flagged"
                  }`}
                >
                  {row.decision_type}
                </span>
                <span className="font-mono text-sm truncate">{row.transaction_id}</span>
                {typeof row.detail.confidence === "string" && (
                  <ConfidenceBadge confidence={row.detail.confidence as "high" | "medium" | "low"} />
                )}
                {row.detail.requires_human_review === true && (
                  <span className="text-[11px] font-mono text-flagged shrink-0">flagged for review</span>
                )}
              </div>
              <span className="font-mono text-xs text-ink-text/40 shrink-0">{formatTimestamp(row.recorded_at)}</span>
            </div>
            <div className="flex items-center gap-3 pl-0.5">
              <Link
                to={`/runs/${row.run_id}`}
                className="font-mono text-xs text-accent hover:underline underline-offset-2"
              >
                run {truncateRunId(row.run_id)}
              </Link>
              {row.method && <span className="font-mono text-xs text-ink-text/40">· {row.method}</span>}
              <span className="font-mono text-xs text-ink-text/30">· recorded by {row.actor ?? "system"}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
