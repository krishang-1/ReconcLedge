import { useState } from "react";
import { useAuth } from "../context/AuthContext";

export function Settings() {
  const { status, setApiKey, clearApiKey } = useAuth();
  const [input, setInput] = useState("");
  const [checking, setChecking] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setChecking(true);
    await setApiKey(input);
    setChecking(false);
  };

  return (
    <div className="max-w-xl mx-auto px-6 py-10">
      <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Settings</p>
      <h1 className="page-heading mb-6">API key</h1>

      <div className="ledger-card px-6 py-6">
        {status === "no-auth-required" && (
          <p className="font-mono text-sm text-ink-text/70">
            This backend has authentication disabled (no <code className="bg-ink-text/5 px-1 py-0.5 rounded">API_KEYS</code>{" "}
            configured) - no key is needed.
          </p>
        )}

        {status === "authenticated" && (
          <div>
            <p className="font-mono text-sm text-verified mb-4">Key set and working.</p>
            <button
              onClick={clearApiKey}
              className="font-mono text-sm text-flagged hover:underline underline-offset-2"
            >
              Clear key
            </button>
          </div>
        )}

        {(status === "needs-key" || status === "invalid-key" || status === "checking") && (
          <form onSubmit={handleSubmit}>
            {status === "invalid-key" && (
              <p className="font-mono text-sm text-flagged mb-3">
                That key was rejected - check it against your backend's <code className="bg-ink-text/5 px-1 py-0.5 rounded">API_KEYS</code>.
              </p>
            )}
            <label className="block font-mono text-xs text-ink-text/50 uppercase tracking-wide mb-2">
              X-API-Key
            </label>
            <input
              type="password"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="your-chosen-key"
            />
            <button
              type="submit"
              disabled={checking || !input}
              className="btn-primary mt-4"
            >
              {checking ? "Checking…" : "Save"}
            </button>
          </form>
        )}
      </div>

      <p className="text-xs font-mono text-paper-text/30 mt-6">
        Stored for this browser tab only (sessionStorage) - never persisted beyond the session.
      </p>
    </div>
  );
}
