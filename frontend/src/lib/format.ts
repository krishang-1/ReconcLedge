/**
 * Pure formatting/logic helpers, deliberately kept free of any React
 * import - lets these be verified directly (see the accompanying
 * format.test-manual.mjs run during development) independent of
 * whether the rendered DOM can be visually inspected in this
 * environment. See docs/DECISIONS.md for why that distinction matters
 * here.
 */
import type { RunStatus } from "../api/types";

export function truncateRunId(runId: string, length = 8): string {
  return runId.length <= length ? runId : `${runId.slice(0, length)}…`;
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatSampleSize(sampleSize: number | null): string {
  return sampleSize === null ? "Full run" : `Sample of ${sampleSize}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

/** Maps a run status to the design system's semantic colors -
 * "verified"/"flagged" reused from the resolved-record vocabulary
 * (see src/index.css) rather than inventing a third color language for
 * run-level status, since "completed" and "failed" are conceptually
 * the same verified/flagged distinction one level up. */
export function statusTone(status: RunStatus): "verified" | "flagged" | "pending" {
  if (status === "completed") return "verified";
  if (status === "failed") return "flagged";
  return "pending"; // pending or running
}

export function statusLabel(status: RunStatus): string {
  switch (status) {
    case "pending":
      return "Queued";
    case "running":
      return "Running";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
  }
}
