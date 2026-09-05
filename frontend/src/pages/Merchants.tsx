import { useState } from "react";
import { ApiError, getMerchantConfig, setMerchantConfig } from "../api/client";

/**
 * Merchant config admin - Stage 6 Phase 6 (see docs/STAGE_6_FRONTEND_PLAN.md).
 * Maps to GET/POST /merchants/{id}/config.
 *
 * Real, consequential backend behavior this design is built around
 * (confirmed by reading api/app.py's set_merchant_config() directly,
 * not assumed): POST is a full REPLACE, not a patch - a field omitted
 * from the request resets to the global default, not "left unchanged."
 * A naive blank-form UI would let someone updating one field silently
 * wipe out the other back to its default. Sidestepped by design, not
 * by a warning label: this form always LOADS the merchant's real
 * current values first, then always submits both fields together on
 * save - there's never a partial request in the first place.
 */
export function Merchants() {
  const [merchantId, setMerchantId] = useState("");
  const [dateWindowDays, setDateWindowDays] = useState("");
  const [escalationThreshold, setEscalationThreshold] = useState("");
  const [knownMerchant, setKnownMerchant] = useState<boolean | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!merchantId.trim()) return;
    setLoading(true);
    setError(null);
    setSaved(false);
    try {
      // GET never 404s for an unknown merchant - it returns the plain
      // global defaults with known_merchant: false (see api/app.py).
      // "Not registered yet" is a normal state here, not an error.
      const config = await getMerchantConfig(merchantId.trim());
      setDateWindowDays(String(config.date_window_days));
      setEscalationThreshold(String(config.escalation_threshold));
      setKnownMerchant(config.known_merchant);
      setLoaded(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
      setLoaded(false);
    } finally {
      setLoading(false);
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      // Always sends BOTH fields together, whatever their current
      // values are (freshly loaded or just edited) - never a partial
      // request, which is what makes the full-replace backend
      // semantics safe here rather than a footgun.
      const response = await setMerchantConfig(merchantId.trim(), {
        date_window_days: Number(dateWindowDays),
        escalation_threshold: Number(escalationThreshold),
      });
      setDateWindowDays(String(response.date_window_days));
      setEscalationThreshold(String(response.escalation_threshold));
      setKnownMerchant(true);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto px-6 py-10">
      <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Merchant config</p>
      <h1 className="page-heading mb-2">Settlement windows &amp; thresholds</h1>
      <p className="text-sm text-paper-text/50 mb-6 max-w-lg">
        A merchant's own settlement cycle and escalation threshold, applied directly to the matcher and escalation
        logic for their runs — not a separate demo path. Saving always replaces both fields together, so a
        registered merchant's other setting is never silently reset.
      </p>

      <form onSubmit={load} className="ledger-card px-6 py-5 flex items-end gap-4 mb-4">
        <div className="flex-1">
          <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">Merchant ID</label>
          <input
            type="text"
            value={merchantId}
            onChange={(e) => {
              setMerchantId(e.target.value);
              setLoaded(false);
              setSaved(false);
            }}
            placeholder="e.g. big_marketplace_seller"
            className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !merchantId.trim()}
          className="btn-primary shrink-0"
        >
          {loading ? "Loading…" : "Look up"}
        </button>
      </form>

      {error && <p className="text-flagged font-mono text-sm mb-4">{error}</p>}

      {loaded && (
        <form onSubmit={save} className="ledger-card px-6 py-5">
          <div className="flex items-center justify-between mb-4">
            <span className="font-mono text-xs text-ink-text/50 uppercase tracking-wide">{merchantId}</span>
            <span
              className={`text-[11px] font-mono font-medium uppercase tracking-wide rounded px-1.5 py-0.5 ${
                knownMerchant ? "bg-verified-soft text-verified" : "bg-ink-text/8 text-ink-text/50"
              }`}
            >
              {knownMerchant ? "registered" : "not registered — showing global defaults"}
            </span>
          </div>

          <div className="flex items-end gap-4">
            <div className="flex-1">
              <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">
                Settlement window (days)
              </label>
              <input
                type="number"
                min={0}
                value={dateWindowDays}
                onChange={(e) => setDateWindowDays(e.target.value)}
                className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <div className="flex-1">
              <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">
                Escalation threshold
              </label>
              <input
                type="number"
                min={0}
                value={escalationThreshold}
                onChange={(e) => setEscalationThreshold(e.target.value)}
                className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <button
              type="submit"
              disabled={saving}
              className="btn-primary shrink-0"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>

          {saved && <p className="text-verified font-mono text-xs mt-3">Saved — apply this merchant ID on New Run to use it.</p>}
        </form>
      )}
    </div>
  );
}
