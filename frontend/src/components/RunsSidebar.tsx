import { Link, useLocation } from "react-router-dom";
import { useRuns } from "../context/RunsContext";
import { StatusBadge } from "./StatusBadge";
import { truncateRunId } from "../lib/format";

/** Persistent sidebar showing recent/active reconciliation runs,
 * visible from any page (not just Dashboard) - reuses RunsContext's
 * shared polling rather than fetching independently. Hidden below the
 * `lg` breakpoint rather than squeezed into a mobile layout - a cramped
 * sidebar would be worse than no sidebar on a small screen. Below `lg`,
 * AppShell's mobile drawer (see components/AppShell.tsx) covers the
 * same recent-runs list on demand instead.
 *
 * Also carries the backend health indicator now, moved out of the
 * header - `mt-auto` pins it to the bottom of the column regardless
 * of how many (or few) runs are listed above it. */
export function RunsSidebar({ healthy }: { healthy: boolean | null }) {
  const { runs, loading } = useRuns();
  const location = useLocation();
  const recent = runs.slice(0, 10);

  return (
    <aside className="hidden lg:flex lg:flex-col w-64 shrink-0 border-r border-rule px-4 py-6">
      <p className="font-mono text-[11px] uppercase tracking-wide text-paper-text/40 mb-3 px-2">Processes</p>
      {loading && recent.length === 0 ? (
        <p className="text-paper-text/25 font-mono text-xs px-2">Loading…</p>
      ) : recent.length === 0 ? (
        <p className="text-paper-text/25 font-mono text-xs px-2">No runs yet.</p>
      ) : (
        <ul className="space-y-1">
          {recent.map((run) => {
            const isActive = location.pathname === `/runs/${run.run_id}`;
            return (
              <li key={run.run_id}>
                <Link
                  to={`/runs/${run.run_id}`}
                  className={`block px-2 py-2 rounded-md transition-colors ${
                    isActive ? "bg-paper-text/10" : "hover:bg-paper-text/5"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="font-mono text-xs text-paper-text/70 truncate">{truncateRunId(run.run_id)}</span>
                  </div>
                  <StatusBadge status={run.status} />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-auto pt-4 px-2 flex items-center gap-2 font-mono text-xs text-paper-text/40">
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            healthy === null ? "bg-paper-text/20" : healthy ? "bg-verified" : "bg-flagged"
          } ${healthy ? "animate-pulse" : ""}`}
        />
        backend {healthy === null ? "checking" : healthy ? "live" : "unreachable"}
      </div>
    </aside>
  );
}
