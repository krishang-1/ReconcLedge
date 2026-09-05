// Deterministic, timing-independent verification of the real
// transition-detection rule computeTransitions() actually implements
// (src/lib/runTransitions.ts) - run directly with Node's native
// TypeScript execution, matching the same pattern already established
// for src/lib/format.ts (see docs/DECISIONS.md). Exists specifically
// because a real, live E2E test can't reliably PROVE this fires
// correctly (a run can resolve well under one 5s poll interval against
// the near-instant fake client used everywhere else in this project's
// own testing - see scripts/deployment_browser_test.py's step 13,
// which documents the same limitation for the LiveFeed "watching for
// the next record" indicator). This checks the actual rule directly,
// with no timing involved at all.
import { computeTransitions } from "../frontend/src/lib/runTransitions.ts";

let failures = 0;
function check(name, actual, expected) {
  const pass = JSON.stringify(actual) === JSON.stringify(expected);
  console.log(`${pass ? "OK" : "FAIL"} - ${name}`);
  if (!pass) {
    failures++;
    console.log(`  expected: ${JSON.stringify(expected)}`);
    console.log(`  actual:   ${JSON.stringify(actual)}`);
  }
}

// A run genuinely moving from running -> completed IS a real transition.
check(
  "running -> completed is a real transition",
  computeTransitions({ run_a: "running" }, [{ run_id: "run_a", status: "completed", sample_size: null, created_at: "" }]),
  [{ run_id: "run_a", status: "completed" }],
);

// running -> failed is also a real transition.
check(
  "running -> failed is a real transition",
  computeTransitions({ run_a: "running" }, [{ run_id: "run_a", status: "failed", sample_size: null, created_at: "" }]),
  [{ run_id: "run_a", status: "failed" }],
);

// The exact case the empty-baseline design specifically exists to
// prevent: a run seen for the FIRST TIME already completed (an empty
// {} baseline, simulating the very first poll after mount) must NOT
// be reported as a transition - there was nothing observed to
// transition FROM.
check(
  "a run already completed on first observation is NOT a transition",
  computeTransitions({}, [{ run_id: "run_a", status: "completed", sample_size: null, created_at: "" }]),
  [],
);

// pending -> running is a real status change but deliberately NOT a
// notification-worthy transition (see runTransitions.ts's own
// docstring - a run finishing is the actionable event, not starting).
check(
  "pending -> running is not reported (not a terminal-state transition)",
  computeTransitions({ run_a: "pending" }, [{ run_id: "run_a", status: "running", sample_size: null, created_at: "" }]),
  [],
);

// A run that was already completed on the LAST poll and is still
// completed now must not re-fire every single poll thereafter.
check(
  "an already-completed run staying completed does not re-fire",
  computeTransitions({ run_a: "completed" }, [{ run_id: "run_a", status: "completed", sample_size: null, created_at: "" }]),
  [],
);

// Multiple runs in one poll, only one of which actually transitioned -
// the others must not be swept up incorrectly.
check(
  "only the genuinely-transitioned run is reported among several",
  computeTransitions(
    { run_a: "running", run_b: "running", run_c: "completed" },
    [
      { run_id: "run_a", status: "completed", sample_size: null, created_at: "" },
      { run_id: "run_b", status: "running", sample_size: null, created_at: "" },
      { run_id: "run_c", status: "completed", sample_size: null, created_at: "" },
    ],
  ),
  [{ run_id: "run_a", status: "completed" }],
);

console.log(failures === 0 ? "\nALL PASS: transition-detection rule verified deterministically, no timing involved" : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
