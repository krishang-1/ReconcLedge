import { statusLabel, statusTone } from "../lib/format";
import type { RunStatus } from "../api/types";

const TONE_CLASSES: Record<ReturnType<typeof statusTone>, string> = {
  verified: "bg-verified-soft text-verified",
  flagged: "bg-flagged-soft text-flagged",
  pending: "bg-ink-text/8 text-ink-text/60",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  const tone = statusTone(status);
  const isLive = status === "running";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-mono font-medium ${TONE_CLASSES[tone]}`}
    >
      {isLive && <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />}
      {statusLabel(status)}
    </span>
  );
}
