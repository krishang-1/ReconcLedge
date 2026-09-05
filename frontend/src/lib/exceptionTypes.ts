/**
 * Descriptions grounded in the real backend behavior (agent/matcher.py,
 * agent/react_loop.py, agent/verifier.py) - general and accurate for
 * the TYPE, not narrowly assuming one specific cause. The real,
 * specific reason for any individual exception is always shown
 * verbatim from the backend alongside this - this caption explains the
 * category, the reason string explains the instance.
 */
import type { ExceptionType } from "../api/types";

export const EXCEPTION_TYPE_LABELS: Record<ExceptionType, string> = {
  NO_CANDIDATE_FOUND: "No candidate found",
  AMBIGUOUS_MULTIPLE_CANDIDATES: "Ambiguous — multiple candidates",
  AMOUNT_MISMATCH_UNEXPLAINED: "Amount mismatch, unexplained",
  VERIFIER_REJECTED: "Verifier rejected",
};

export const EXCEPTION_TYPE_DESCRIPTIONS: Record<ExceptionType, string> = {
  NO_CANDIDATE_FOUND: "No bank settlement row matched this transaction within the search window - genuinely unresolved, not guessed at.",
  AMBIGUOUS_MULTIPLE_CANDIDATES: "More than one settlement row fits equally well - most often two rows settling the exact same amount with no distinguishing reference (a real duplicate-settlement scenario). Deferred to a human rather than picked at random.",
  AMOUNT_MISMATCH_UNEXPLAINED: "A candidate exists but the amount gap doesn't fit any known fee/FX/refund pattern the agent could verify.",
  VERIFIER_REJECTED: "The agent proposed a match, but the independent verifier rejected it - a second opinion catching what the first pass got wrong, not agreeing by default.",
};
