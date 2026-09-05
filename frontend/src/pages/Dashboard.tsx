import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, listRuns } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { formatSampleSize, formatTimestamp, truncateRunId } from "../lib/format";
import type { RunListItem } from "../api/types";

export function Dashboard() {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    listRuns()
      .then((result) => setRuns([...result].reverse())) // most recent first
      .catch((err) => setError(err instanceof ApiError ? err.detail : "Could not reach the backend."));
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 5_000); // picks up in-flight runs finishing
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="max-w-6xl mx-auto px-6 py-10">
      <div className="flex items-end justify-between mb-8">
        <div>
          <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Dashboard</p>
          <h1 className="page-heading">Reconciliation runs</h1>
        </div>
        <Link
          to="/runs/new"
          className="btn-primary"
        >
          New run
        </Link>
      </div>

      {error ? (
        <ErrorState message={error} onRetry={load} />
      ) : runs === null ? (
        <LoadingState />
      ) : runs.length === 0 ? (
        <EmptyState />
      ) : (
        <RunsTable runs={runs} />
      )}
    </div>
  );
}

function RunsTable({ runs }: { runs: RunListItem[] }) {
  return (
    <div className="ledger-card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-text/10 text-left font-mono text-xs text-ink-text/50 uppercase tracking-wide">
            <th className="px-6 py-3 font-medium">Run</th>
            <th className="px-6 py-3 font-medium">Status</th>
            <th className="px-6 py-3 font-medium">Scope</th>
            <th className="px-6 py-3 font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.run_id}
              className="border-b border-ink-text/5 last:border-0 hover:bg-ink-text/[0.03] transition-colors"
            >
              <td className="px-6 py-4">
                <Link to={`/runs/${run.run_id}`} className="font-mono text-accent hover:underline underline-offset-2">
                  {truncateRunId(run.run_id)}
                </Link>
              </td>
              <td className="px-6 py-4">
                <StatusBadge status={run.status} />
              </td>
              <td className="px-6 py-4 font-mono text-ink-text/70">{formatSampleSize(run.sample_size)}</td>
              <td className="px-6 py-4 font-mono text-ink-text/50">{formatTimestamp(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="ledger-card px-6 py-16 text-center">
      <p className="font-display text-lg font-medium mb-2">No runs yet</p>
      <p className="text-ink-text/60 text-sm mb-6">Start one to see the reconciliation pipeline work, live.</p>
      <Link
        to="/runs/new"
        className="btn-primary inline-block"
      >
        New run
      </Link>
    </div>
  );
}

function LoadingState() {
  // A skeleton echoing the real table's row shape reads as "content is
  // arriving" rather than the plain "Loading…" text it replaces, which
  // gave no sense of what was about to appear.
  return (
    <div className="ledger-card overflow-hidden animate-pulse">
      <div className="border-b border-ink-text/10 px-6 py-3 flex gap-6">
        {["w-24", "w-16", "w-20", "w-28"].map((w, i) => (
          <div key={i} className={`h-3 rounded bg-ink-text/10 ${w}`} />
        ))}
      </div>
      {[...Array(4)].map((_, row) => (
        <div key={row} className="border-b border-ink-text/5 last:border-0 px-6 py-4 flex gap-6 items-center">
          <div className="h-4 w-32 rounded bg-ink-text/10" />
          <div className="h-5 w-20 rounded-full bg-ink-text/10" />
          <div className="h-4 w-12 rounded bg-ink-text/10" />
          <div className="h-4 w-24 rounded bg-ink-text/10" />
        </div>
      ))}
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="ledger-card px-6 py-10">
      <p className="text-flagged font-semibold mb-2 font-mono text-sm">Could not load runs</p>
      <p className="text-ink-text/70 text-sm mb-4">{message}</p>
      <button
        onClick={onRetry}
        className="text-accent font-mono text-sm hover:underline underline-offset-2"
      >
        Retry
      </button>
    </div>
  );
}
