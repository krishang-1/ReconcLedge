const TONE: Record<string, string> = {
  high: "bg-verified-soft text-verified",
  medium: "bg-flagged-soft text-flagged",
  low: "bg-ink-text/10 text-ink-text/60",
};

export function ConfidenceBadge({ confidence }: { confidence: "high" | "medium" | "low" }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-mono font-medium uppercase tracking-wide ${TONE[confidence]}`}>
      {confidence}
    </span>
  );
}
