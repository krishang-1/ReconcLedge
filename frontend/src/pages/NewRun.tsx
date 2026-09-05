import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, createRun } from "../api/client";

const RECENT_MERCHANTS_KEY = "reconciliation-ledger-recent-merchants";

function getRecentMerchants(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_MERCHANTS_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function rememberMerchant(id: string) {
  // Real bug found in a comprehensive audit (see docs/DECISIONS.md):
  // this had no error handling, unlike getRecentMerchants() right
  // above it - and crucially, it's called from inside the SAME try
  // block as the actual run-creation request in handleSubmit below.
  // If storage access throws (private browsing, quota exceeded,
  // storage disabled entirely - all real browser configurations, not
  // hypothetical), the exception would be caught by handleSubmit's own
  // catch block and shown as "Could not reach the backend" - even
  // though the run had ALREADY been created successfully by that
  // point. The user would see a failure message for a run that
  // actually succeeded and now sits on the dashboard, orphaned from
  // their view since navigate() never ran. Remembering a merchant ID
  // for autofill is a nice-to-have convenience, never something that
  // should be able to make a successful run look like a failure -
  // fails silently now, exactly like getRecentMerchants() already does.
  try {
    const recent = getRecentMerchants().filter((m) => m !== id);
    localStorage.setItem(RECENT_MERCHANTS_KEY, JSON.stringify([id, ...recent].slice(0, 8)));
  } catch {
    // storage unavailable - not remembering this merchant ID is a
    // minor UX loss, not a reason to make the actual run creation
    // (already succeeded by the time this is called) look like it failed
  }
}

export function NewRun() {
  const navigate = useNavigate();
  const [sampleSize, setSampleSize] = useState("");
  const [merchantId, setMerchantId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recentMerchants = getRecentMerchants();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Minor edge case found in a comprehensive audit (see
    // docs/DECISIONS.md): sampleSize is a STRING state value, so the
    // old `sampleSize ? Number(sampleSize) : undefined` check tested
    // truthiness of the STRING "0" (a non-empty string, always truthy)
    // rather than the numeric value - meaning a user typing "0"
    // sent sample_size: 0, which the backend correctly rejects
    // (Field(ge=1)) but only after a full round trip, surfacing as a
    // generic server error instead of an immediate, specific one.
    // Validated client-side now, before any request goes out.
    const parsedSampleSize = sampleSize.trim() === "" ? undefined : Number(sampleSize);
    if (parsedSampleSize !== undefined && (!Number.isInteger(parsedSampleSize) || parsedSampleSize < 1)) {
      setError("Sample size must be a whole number of 1 or more, or left blank for the full batch.");
      return;
    }

    setSubmitting(true);
    try {
      const trimmedMerchant = merchantId.trim();
      const response = await createRun({
        sample_size: parsedSampleSize,
        merchant_id: trimmedMerchant || undefined,
      });
      if (trimmedMerchant) rememberMerchant(trimmedMerchant);
      navigate(`/runs/${response.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-xl mx-auto px-6 py-10">
      <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">New run</p>
      <h1 className="page-heading mb-6">Start reconciling</h1>

      <form onSubmit={handleSubmit} className="ledger-card px-6 py-6">
        <label className="block font-mono text-xs text-ink-text/50 uppercase tracking-wide mb-2">
          Sample size
        </label>
        <input
          type="number"
          min={1}
          value={sampleSize}
          onChange={(e) => setSampleSize(e.target.value)}
          placeholder="Leave blank for the full batch"
          className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent mb-1"
        />
        <p className="text-xs text-ink-text/50 mb-5">
          A number runs a fast demo sample (deliberately biased to include live agent reasoning, not just
          instant matches). Leave blank for the full batch — the run that reports real eval metrics.
        </p>

        <label className="block font-mono text-xs text-ink-text/50 uppercase tracking-wide mb-2">
          Merchant ID <span className="normal-case text-ink-text/40">(optional)</span>
        </label>
        <input
          type="text"
          value={merchantId}
          onChange={(e) => setMerchantId(e.target.value)}
          placeholder="Applies that merchant's registered settlement window/threshold"
          list="recent-merchants"
          className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent mb-1"
        />
        <datalist id="recent-merchants">
          {recentMerchants.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <p className="text-xs text-ink-text/50 mb-5">
          No merchant registry to browse — the backend only supports looking up one merchant at a time, not
          listing them. Type an ID exactly as registered under <span className="font-mono">Settings</span>{" "}
          {"->"} merchant config, or leave blank for global defaults. Unregistered IDs behave identically to
          leaving this blank.
        </p>

        {error && <p className="text-flagged text-sm font-mono mb-4">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="btn-primary w-full"
        >
          {submitting ? "Starting…" : "Start run"}
        </button>
      </form>
    </div>
  );
}
