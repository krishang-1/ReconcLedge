/** Shared result display for the Phase 5 reconciliation tools - a
 * classification badge (tone driven by whether the value reads as
 * "clean"/"verified" vs "needs a look"/"flagged") plus the raw JSON
 * response, so the real backend output is always visible verbatim,
 * never summarized into something that could drift from what the
 * endpoint actually said. */
const CLEAN_VALUES = new Set([
  "full_refund", "matched", true, false, // false = requires_human_review:false is clean
  "fully_reconciled", "matched_within_rate_band",
]);

export function ToolResultCard({ result }: { result: unknown }) {
  if (result === null || result === undefined) return null;
  const classification =
    typeof result === "object" && result !== null
      ? (result as Record<string, unknown>).classification ??
        (result as Record<string, unknown>).status ??
        null
      : null;
  const isClean = classification !== null && CLEAN_VALUES.has(classification as string);

  return (
    <div className="ledger-card overflow-hidden mt-4">
      <div className="border-b border-ink-text/10 px-6 py-3 flex items-center justify-between">
        <p className="font-mono text-xs text-ink-text/50 uppercase tracking-wide">Response</p>
        {classification !== null && (
          <span
            className={`text-[11px] font-mono font-medium uppercase tracking-wide rounded px-1.5 py-0.5 ${
              isClean ? "bg-verified-soft text-verified" : "bg-flagged-soft text-flagged"
            }`}
          >
            {String(classification)}
          </span>
        )}
      </div>
      <pre className="px-6 py-4 text-xs font-mono text-ink-text/70 overflow-x-auto whitespace-pre-wrap break-words">
        {JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}
