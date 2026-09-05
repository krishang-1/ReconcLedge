import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { listRuns } from "../api/client";
import { computeTransitions } from "../lib/runTransitions";
import { useNotifications } from "./NotificationContext";
import type { RunListItem } from "../api/types";

interface RunsContextValue {
  runs: RunListItem[];
  loading: boolean;
}

const RunsContext = createContext<RunsContextValue>({ runs: [], loading: true });

/** Polls GET /v1/runs globally on a 5s cadence - one shared poll, so
 * the sidebar and the completion notifications read from a single
 * source rather than each running their own timer.
 *
 * A run already finished when the page loads must never notify:
 * computeTransitions() only reports runs previously seen as "running",
 * so an empty initial baseline handles this with no extra flag. */
export function RunsProvider({ children }: { children: ReactNode }) {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const previousStatuses = useRef<Record<string, string>>({});
  const { notify } = useNotifications();

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const result = await listRuns();
        if (cancelled) return;

        for (const transition of computeTransitions(previousStatuses.current, result)) {
          notify(
            `Run ${transition.run_id.slice(0, 8)} ${transition.status}`,
            transition.status === "completed" ? "verified" : "flagged",
          );
        }

        const nextStatuses: Record<string, string> = {};
        for (const run of result) nextStatuses[run.run_id] = run.status;
        previousStatuses.current = nextStatuses;

        setRuns(result);
        setLoading(false);
      } catch {
        // Same silent-continue pattern as AppShell's own health poll -
        // a transient failure here shouldn't disrupt the rest of the
        // app or spam an error notification for a routine background
        // poll; it just tries again on the next interval.
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <RunsContext.Provider value={{ runs, loading }}>{children}</RunsContext.Provider>;
}

export function useRuns() {
  return useContext(RunsContext);
}
