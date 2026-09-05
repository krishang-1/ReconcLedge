import type { StreamEvent } from "../api/types";

/** Each row mounts once when its transaction_id first appears, so the
 * stamp-settle animation (src/index.css) fires on mount and never
 * re-fires - "new items animate in, old ones stay put" for free.
 *
 * `isRunning` exists because this only re-renders on a NEW event, and
 * real Groq rate-limiting produced 30+ second gaps where the screen
 * sat identical, indistinguishable from a dead run. Deliberately not a
 * fake progress bar - there's no honest way to estimate a rate-limit
 * wait - just a signal the stream is still open. */
export function LiveFeed({ events, isRunning }: { events: StreamEvent[]; isRunning: boolean }) {
  const resolvedEvents = events.filter((e) => e.stage !== "done");

  if (resolvedEvents.length === 0) {
    return <p className="text-ink-text/40 font-mono text-sm px-6 py-8 text-center">Waiting for the first record…</p>;
  }

  return (
    <ul className="divide-y divide-ink-text/8">
      {isRunning && (
        <li className="px-6 py-3 flex items-center gap-2.5">
          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
          <span className="font-mono text-xs text-ink-text/40">watching for the next record…</span>
        </li>
      )}
      {[...resolvedEvents].reverse().map((event, idx) => (
        <li key={`${event.transaction_id}-${event.stage}`} className="stamp-settle px-6 py-3">
          <FeedRow event={event} isLatest={idx === 0} />
        </li>
      ))}
    </ul>
  );
}

function FeedRow({ event, isLatest }: { event: StreamEvent; isLatest: boolean }) {
  const isMatched = event.status === "matched";
  const tone = isMatched ? "text-verified" : "text-flagged";
  const dot = isMatched ? "bg-verified" : "bg-flagged";

  return (
    <div className={isLatest ? "ring-1 ring-accent/30 rounded-md -mx-3 px-3 py-1" : ""}>
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
          <span className="font-mono text-sm text-ink-text truncate">{event.transaction_id}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="font-mono text-[11px] uppercase tracking-wide text-ink-text/40">{event.stage}</span>
          <span className={`font-mono text-xs font-medium ${tone}`}>{isMatched ? "matched" : "exception"}</span>
        </div>
      </div>
      {event.agent_reasoning && (
        <p className="mt-1.5 text-xs text-ink-text/60 font-mono pl-4 border-l-2 border-accent/30">
          {event.agent_reasoning}
        </p>
      )}
      {event.reason && (
        <p className="mt-1.5 text-xs text-ink-text/60 font-mono pl-4 border-l-2 border-flagged/30">{event.reason}</p>
      )}
    </div>
  );
}
