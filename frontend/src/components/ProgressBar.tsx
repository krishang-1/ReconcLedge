/** Uses the {stage, current, total} the backend already streams (see
 * api/jobs.py's on_progress) and the frontend previously ignored.
 * Stage-scoped rather than one merged percentage: the deterministic
 * and agent stages have unrelated totals, so a combined bar would jump
 * discontinuously and imply a false sense of their relative size. */
export function ProgressBar({ stage, current, total }: { stage: string; current: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-text/40">{stage} stage</span>
        <span className="font-mono text-xs text-ink-text/60">
          {current} / {total}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-ink-text/10 overflow-hidden">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
