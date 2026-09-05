import type { ExceptionRecord } from "../api/types";

/**
 * Surfaces the honest-deferral result up front rather than buried in
 * the exceptions table. Framed broadly - every exception type is the
 * agent declining to force a match, not a failure - then names the
 * duplicate-settlement case specifically when present, since that's
 * the crispest example of guessing being easy and wrong.
 *
 * Says nothing the passed-in exception records don't support.
 */
export function HonestyCallout({ exceptions }: { exceptions: ExceptionRecord[] }) {
  if (exceptions.length === 0) {
    return (
      <div className="bg-verified-soft text-ink-text rounded-lg px-5 py-3 border border-verified/30 text-sm font-mono">
        <span className="text-verified font-semibold">Every record resolved cleanly</span> — nothing needed to be
        deferred this run.
      </div>
    );
  }

  const duplicateLikeCount = exceptions.filter((e) => e.type === "AMBIGUOUS_MULTIPLE_CANDIDATES").length;

  return (
    <div className="bg-flagged-soft text-ink-text rounded-lg px-5 py-3 border border-flagged/30 text-sm font-mono">
      <span className="text-flagged font-semibold">
        {exceptions.length} record{exceptions.length === 1 ? "" : "s"} honestly deferred to human review
      </span>{" "}
      rather than guessed at.
      {duplicateLikeCount > 0 && (
        <>
          {" "}
          {duplicateLikeCount} of those {duplicateLikeCount === 1 ? "is" : "are"} a genuinely ambiguous
          case — multiple settlements that fit equally well, with no reliable way to tell them apart — where
          a less careful system would have picked one anyway and risked being wrong.
        </>
      )}
    </div>
  );
}
