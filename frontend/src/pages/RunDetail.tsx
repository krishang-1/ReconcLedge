import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getRunResults, getRunStatus, streamRun } from "../api/client";
import { HonestyCallout } from "../components/HonestyCallout";
import { DonutChart } from "../components/DonutChart";
import { LiveFeed } from "../components/LiveFeed";
import { ProgressBar } from "../components/ProgressBar";
import { ExceptionsTable, MatchedRecordsTable } from "../components/ResultsTables";
import { StatusBadge } from "../components/StatusBadge";
import { formatPercent } from "../lib/format";
import type { RunResults, RunStatus, StreamEvent } from "../api/types";

/**
 * Live-run + full results view - Stage 6 Phases 2 (live streaming) and
 * 3 (full matched/exception tables, confidence breakdown) combined,
 * see docs/STAGE_6_FRONTEND_PLAN.md.
 */
export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [results, setResults] = useState<RunResults | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    // Real bug found in a comprehensive audit (see docs/DECISIONS.md):
    // none of this component's state was reset when `runId` changed -
    // React re-runs this effect (it's in the dependency array below),
    // but a route param changing does NOT unmount/remount the
    // component if the surrounding tree shape stays the same, so every
    // piece of state below would otherwise persist across a navigation
    // from one run to a different one. Concretely: navigating from a
    // completed run straight to a newly-started one would show the
    // OLD run's results summary while the new run was actually
    // streaming in the background, and `events` would literally
    // APPEND the new run's events onto the old run's leftover array
    // (setEvents uses a functional updater reading `prev`), corrupting
    // the feed into a mix of two different runs' transactions. Fixed
    // by resetting every piece of state synchronously before any async
    // work starts for the new runId.
    setStatus(null);
    setError(null);
    setRunError(null);
    setEvents([]);
    setResults(null);

    const start = async () => {
      try {
        const initial = await getRunStatus(runId);
        if (cancelled) return;
        setStatus(initial.status);

        if (initial.status === "completed" || initial.status === "failed") {
          if (initial.status === "failed") setRunError(initial.error);
          else setResults(await getRunResults(runId));
          return;
        }

        const controller = new AbortController();
        abortRef.current = controller;
        for await (const event of streamRun(runId, controller.signal)) {
          if (cancelled) break;
          setEvents((prev) => [...prev, event]);
          if (event.stage === "done") {
            setStatus(event.status as RunStatus);
            if (event.status === "failed") {
              setRunError(event.error ?? "Run failed.");
            } else {
              setResults(await getRunResults(runId));
            }
          }
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
      }
    };

    start();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [runId]);

  // Real UX oversight found in a comprehensive audit (see
  // docs/DECISIONS.md): an early version of this fix left the OLD
  // unconditional `if (error) return <full-page error>` block in place
  // above this refined one - since it's unconditional and runs first,
  // it always short-circuited before this more precise check could
  // ever be reached, meaning the fix below was dead code until this
  // was caught (during Phase 3 work, re-reading this file closely) and
  // the stale duplicate removed. Only show the full-page error state
  // when there's genuinely nothing else to show yet (no events
  // captured, no results); once there's real partial progress on
  // screen, a connection problem surfaces as a small inline banner
  // instead, preserving what was already captured rather than
  // discarding it.
  if (error && events.length === 0 && !results) {
    return (
      <div className="max-w-3xl mx-auto px-6 py-10 text-center">
        <p className="text-flagged font-mono text-sm">{error}</p>
        <Link to="/" className="font-mono text-sm text-accent hover:underline underline-offset-2 mt-4 inline-block">
          ← Back to dashboard
        </Link>
      </div>
    );
  }

  // The most recent event's own progress field - events append in
  // chronological order (see the setEvents call above), so the last
  // element is always the latest. Real data the backend has always
  // streamed (see api/jobs.py); this just reads it for the first time.
  const latestProgress = events.length > 0 ? events[events.length - 1].progress : undefined;

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <div className="flex items-center justify-between mb-6">
        <div>
          <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Run</p>
          <h1 className="font-display text-lg font-semibold text-paper-text font-mono">{runId}</h1>
        </div>
        {status && <StatusBadge status={status} />}
      </div>

      {error && (events.length > 0 || results) && (
        <div className="bg-flagged-soft text-ink-text rounded-lg px-4 py-3 mb-6 border border-flagged/30 text-sm font-mono">
          <span className="text-flagged font-semibold">Connection issue: </span>
          {error} — showing what was captured before the connection dropped.
        </div>
      )}

      {status === "failed" && runError && (
        <div className="bg-flagged-soft text-ink-text rounded-lg px-6 py-5 mb-6 border border-flagged/30">
          <p className="font-mono text-sm font-semibold text-flagged mb-1">This run failed</p>
          <p className="font-mono text-sm text-ink-text/70">{runError}</p>
        </div>
      )}

      {results ? (
        <ResultsSummary results={results} runId={runId!} />
      ) : (
        <div className="ledger-card overflow-hidden">
          <div className="border-b border-ink-text/10 px-6 py-4">
            <p className="font-mono text-xs text-ink-text/50 uppercase tracking-wide mb-3">Live progress</p>
            {latestProgress && <ProgressBar stage={latestProgress.stage} current={latestProgress.current} total={latestProgress.total} />}
          </div>
          <LiveFeed events={events} isRunning={status === "running"} />
        </div>
      )}
    </div>
  );
}

function ResultsSummary({ results, runId }: { results: RunResults; runId: string }) {
  const total = results.matched.length + results.exceptions.length;
  // Real bug found via an actual rendered-browser test, not caught by
  // any prior compile/curl-level check: requires_human_review is only
  // at the top level for full runs - a demo run only has it nested
  // inside `summary`. Reading `results.requires_human_review` directly
  // rendered the literal string "undefined" on screen for every demo
  // run. See src/api/types.ts's RunResults docstring and
  // docs/DECISIONS.md.
  const requiresHumanReview = results.requires_human_review ?? results.summary?.requires_human_review ?? 0;

  const confidenceCounts = { high: 0, medium: 0, low: 0 };
  for (const r of [...results.matched, ...results.exceptions]) confidenceCounts[r.confidence]++;

  return (
    <div className="space-y-6">
      <HonestyCallout exceptions={results.exceptions} />

      <div className="ledger-card px-6 py-6">
        <div className="grid grid-cols-2 gap-4 mb-6">
          {results.metrics ? (
            <>
              <Stat label="Match rate" value={formatPercent(results.metrics.match_rate)} tone="verified" />
              <Stat label="False positive rate" value={formatPercent(results.metrics.false_positive_rate)} tone="verified" />
            </>
          ) : (
            <Stat label="Mode" value="Demo sample" tone="pending" />
          )}
          <Stat label="Matched" value={String(results.matched.length)} tone="verified" />
          <Stat label="Exceptions" value={String(results.exceptions.length)} tone={results.exceptions.length ? "flagged" : "verified"} />
          <Stat label="Needs human review" value={String(requiresHumanReview)} tone={requiresHumanReview ? "flagged" : "verified"} />
          <Stat label="Total records" value={String(total)} tone="pending" />
        </div>
        <div className="border-t border-ink-text/10 pt-4">
          <p className="font-mono text-[11px] uppercase tracking-wide text-ink-text/40 mb-3">Confidence</p>
          <DonutChart
            segments={[
              { label: "High", value: confidenceCounts.high, color: "#3f7d5c" },
              { label: "Medium", value: confidenceCounts.medium, color: "#b8863b" },
              { label: "Low", value: confidenceCounts.low, color: "#6b6d75" },
            ]}
          />
        </div>
        {results.llm_usage && (
          <div className="border-t border-ink-text/10 pt-4 flex items-center gap-6 mt-4">
            <p className="font-mono text-[11px] uppercase tracking-wide text-ink-text/40">Agent-stage LLM usage</p>
            <span className="font-mono text-xs text-ink-text/70">
              <span className="text-ink-text">{results.llm_usage.calls}</span> call{results.llm_usage.calls === 1 ? "" : "s"}
            </span>
            <span className="font-mono text-xs text-ink-text/70">
              <span className="text-ink-text">{(results.llm_usage.prompt_tokens + results.llm_usage.completion_tokens).toLocaleString()}</span> tokens
            </span>
            <span className="font-mono text-xs text-ink-text/70">
              <span className="text-ink-text">{results.llm_usage.latency_seconds.toFixed(1)}s</span> total
            </span>
          </div>
        )}
      </div>

      <MatchedRecordsTable records={results.matched} />
      <ExceptionsTable records={results.exceptions} />

      <p className="text-xs font-mono text-paper-text/30 text-center">
        Per-transaction audit history for this run ({runId}) arrives with{" "}
        <Link to="/audit" className="text-accent hover:underline underline-offset-2">/audit</Link> in Phase 4.
      </p>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone: "verified" | "flagged" | "pending" }) {
  const toneClass = tone === "verified" ? "text-verified" : tone === "flagged" ? "text-flagged" : "text-ink-text/70";
  return (
    <div>
      <p className="font-mono text-[11px] uppercase tracking-wide text-ink-text/40 mb-1">{label}</p>
      <p className={`font-display text-2xl font-semibold ${toneClass}`}>{value}</p>
    </div>
  );
}
