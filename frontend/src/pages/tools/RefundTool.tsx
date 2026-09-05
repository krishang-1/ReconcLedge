import { useState } from "react";
import { ApiError, reconcileRefunds } from "../../api/client";
import { ToolResultCard } from "../../components/ToolResultCard";

const EXAMPLE = { transaction_id: "txn_631d7aa90c3291", refund_amount: 6341.34 };

export function RefundTool({ initialTransactionId }: { initialTransactionId?: string } = {}) {
  const isCrossCheck = Boolean(initialTransactionId);
  const [transactionId, setTransactionId] = useState(initialTransactionId ?? EXAMPLE.transaction_id);
  // When cross-referencing a real exception (see ResultsTables.tsx's
  // "Check against Refunds" link), the example's unrelated dummy amount
  // would be actively misleading here - the whole point is the user
  // doesn't yet know the real refund amount, that's what they're
  // investigating. Starts empty in that case, prompting them to enter
  // the real figure, rather than showing a number that has nothing to
  // do with the transaction they actually care about.
  const [amount, setAmount] = useState(isCrossCheck ? "" : String(EXAMPLE.refund_amount));
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await reconcileRefunds([{ transaction_id: transactionId, refund_amount: Number(amount) }]);
      setResult(response.reconciliation[0] ?? response);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <p className="text-sm text-paper-text/50 mb-4">
        Classifies a refund event against a real gateway transaction's original amount - full, partial, or over-refunded.
      </p>
      {isCrossCheck && (
        <div className="bg-accent/10 border border-accent/30 rounded-lg px-4 py-2.5 mb-4 text-xs font-mono text-paper-text/70">
          Cross-checking transaction <span className="text-paper-text">{initialTransactionId}</span> from an
          unresolved exception - a human reviewer's own hypothesis to test, not an automatic reclassification.
        </div>
      )}
      <form onSubmit={submit} className="ledger-card px-6 py-5 flex items-end gap-4">
        <Field label="Transaction ID" value={transactionId} onChange={setTransactionId} />
        <Field label="Refund amount" value={amount} onChange={setAmount} type="number" placeholder={isCrossCheck ? "Amount you're checking" : undefined} />
        <SubmitButton loading={loading} />
      </form>
      {error && <p className="text-flagged font-mono text-sm mt-3">{error}</p>}
      <ToolResultCard result={result} />
    </div>
  );
}

export function Field({ label, value, onChange, type = "text", placeholder }: { label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string }) {
  return (
    <div className="flex-1">
      <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
      />
    </div>
  );
}

export function SubmitButton({ loading }: { loading: boolean }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="btn-primary shrink-0"
    >
      {loading ? "Checking…" : "Reconcile"}
    </button>
  );
}
