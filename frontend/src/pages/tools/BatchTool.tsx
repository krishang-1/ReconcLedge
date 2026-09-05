import { useState } from "react";
import { Field, SubmitButton } from "./RefundTool";
import { ApiError, reconcileBatches } from "../../api/client";
import { ToolResultCard } from "../../components/ToolResultCard";

// Real example from data/batch_generator.py's "clean" scenario - two
// gateway transactions settling together in one bank batch credit.
const EXAMPLE = {
  txn1: { transaction_id: "batch_txn_001", net_amount: "1000.00" },
  txn2: { transaction_id: "batch_txn_002", net_amount: "2500.50" },
  batchId: "BATCH_CLEAN_01",
  credited: "3500.50",
};

export function BatchTool({ initialTransactionId }: { initialTransactionId?: string } = {}) {
  const isCrossCheck = Boolean(initialTransactionId);
  // Only the first slot gets the real cross-referenced ID - we
  // genuinely don't know what OTHER transaction it might have been
  // batched with, that's exactly the second thing a reviewer would
  // need to go find out, not something safe to guess at here.
  const [txn1Id, setTxn1Id] = useState(initialTransactionId ?? EXAMPLE.txn1.transaction_id);
  const [txn1Amount, setTxn1Amount] = useState(isCrossCheck ? "" : EXAMPLE.txn1.net_amount);
  const [txn2Id, setTxn2Id] = useState(EXAMPLE.txn2.transaction_id);
  const [txn2Amount, setTxn2Amount] = useState(EXAMPLE.txn2.net_amount);
  const [batchId, setBatchId] = useState(EXAMPLE.batchId);
  const [credited, setCredited] = useState(EXAMPLE.credited);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await reconcileBatches(
        [
          { transaction_id: txn1Id, net_amount: Number(txn1Amount), settlement_batch_id: batchId },
          { transaction_id: txn2Id, net_amount: Number(txn2Amount), settlement_batch_id: batchId },
        ],
        [{ batch_id: batchId, credited_amount: Number(credited) }],
      );
      setResult(response.batch_id_reconciliation[0] ?? response);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <p className="text-sm text-paper-text/50 mb-4">
        N-way settlement matching - checks whether several gateway transactions sum to one bank batch credit.
      </p>
      {isCrossCheck && (
        <div className="bg-accent/10 border border-accent/30 rounded-lg px-4 py-2.5 mb-4 text-xs font-mono text-paper-text/70">
          Cross-checking transaction <span className="text-paper-text">{initialTransactionId}</span> from an
          unresolved exception - a human reviewer's own hypothesis to test, not an automatic reclassification.
          The second transaction and batch details below are still the illustrative example - fill in what you
          believe this one was actually batched with.
        </div>
      )}
      <form onSubmit={submit} className="ledger-card px-6 py-5 space-y-3">
        <div className="flex items-end gap-4">
          <Field label="Transaction 1 ID" value={txn1Id} onChange={setTxn1Id} />
          <Field label="Amount" value={txn1Amount} onChange={setTxn1Amount} type="number" placeholder={isCrossCheck ? "Amount you're checking" : undefined} />
        </div>
        <div className="flex items-end gap-4">
          <Field label="Transaction 2 ID" value={txn2Id} onChange={setTxn2Id} />
          <Field label="Amount" value={txn2Amount} onChange={setTxn2Amount} type="number" />
        </div>
        <div className="flex items-end gap-4 pt-2 border-t border-ink-text/10">
          <Field label="Bank batch ID" value={batchId} onChange={setBatchId} />
          <Field label="Credited amount" value={credited} onChange={setCredited} type="number" />
          <SubmitButton loading={loading} />
        </div>
      </form>
      {error && <p className="text-flagged font-mono text-sm mt-3">{error}</p>}
      <ToolResultCard result={result} />
    </div>
  );
}
