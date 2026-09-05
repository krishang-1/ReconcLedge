import type { RunListItem } from "../api/types";

/** Pure and React-free (same pattern as lib/format.ts) so it can run
 * directly under `node --experimental-strip-types` - a .tsx file with
 * JSX can't, since that mode strips types without transforming JSX.
 * Living here lets the transition rule be verified deterministically
 * (scripts/verify_run_transitions.mjs), independent of the timing
 * difficulty of catching it fire in a live E2E test.
 *
 * Given the previous poll's statuses and the current run list, returns
 * only runs that genuinely moved from "running" to a terminal state -
 * never one seen for the first time already finished (no observed
 * transition), never one still running. */
export function computeTransitions(previousStatuses: Record<string, string>, currentRuns: RunListItem[]) {
  const transitions: { run_id: string; status: "completed" | "failed" }[] = [];
  for (const run of currentRuns) {
    const prev = previousStatuses[run.run_id];
    if (prev === "running" && (run.status === "completed" || run.status === "failed")) {
      transitions.push({ run_id: run.run_id, status: run.status });
    }
  }
  return transitions;
}
