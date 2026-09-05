import { useState } from "react";
import { Field, SubmitButton } from "./RefundTool";
import { ApiError, reconcileChargeback } from "../../api/client";
import { ToolResultCard } from "../../components/ToolResultCard";
import type { ChargebackEvent } from "../../api/types";

// Real example from data/chargeback_generator.py - a fresh in-flight
// dispute. All six statuses are Razorpay's real documented dispute
// lifecycle states (see agent/chargeback_matcher.py), not invented.
const EXAMPLE = { txnId: "cb_txn_001", netAmount: "5000.00", disputed: "5000.00", fee: "250.00" };
const STATUSES: ChargebackEvent["status"][] = ["open", "under_review", "pre_arbitration", "arbitration", "won", "lost"];
const INITIATORS: ChargebackEvent["initiated_by"][] = ["issuing_bank", "customer"];

export function ChargebackTool() {
  const [txnId, setTxnId] = useState(EXAMPLE.txnId);
  const [netAmount, setNetAmount] = useState(EXAMPLE.netAmount);
  const [status, setStatus] = useState<ChargebackEvent["status"]>("open");
  const [disputed, setDisputed] = useState(EXAMPLE.disputed);
  const [fee, setFee] = useState(EXAMPLE.fee);
  const [initiatedBy, setInitiatedBy] = useState<ChargebackEvent["initiated_by"]>("issuing_bank");
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await reconcileChargeback({
        gateway_record: { transaction_id: txnId, net_amount: Number(netAmount) },
        chargeback_event: { status, disputed_amount: Number(disputed), chargeback_fee: Number(fee), initiated_by: initiatedBy },
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not reach the backend.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <p className="text-sm text-paper-text/50 mb-4">
        Dispute/chargeback reconciliation across Razorpay's real six-status lifecycle. Try "won" or "lost" to see the resolved-balance calculation change.
      </p>
      <form onSubmit={submit} className="ledger-card px-6 py-5 space-y-3">
        <div className="flex items-end gap-4">
          <Field label="Transaction ID" value={txnId} onChange={setTxnId} />
          <Field label="Net amount" value={netAmount} onChange={setNetAmount} type="number" />
        </div>
        <div className="flex items-end gap-4">
          <Field label="Disputed amount" value={disputed} onChange={setDisputed} type="number" />
          <Field label="Chargeback fee" value={fee} onChange={setFee} type="number" />
        </div>
        <div className="flex items-end gap-4 pt-2 border-t border-ink-text/10">
          <div className="flex-1">
            <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">Dispute status</label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as ChargebackEvent["status"])}
              className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex-1">
            <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">Initiated by</label>
            <select
              value={initiatedBy}
              onChange={(e) => setInitiatedBy(e.target.value as ChargebackEvent["initiated_by"])}
              className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {INITIATORS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
          <SubmitButton loading={loading} />
        </div>
      </form>
      {error && <p className="text-flagged font-mono text-sm mt-3">{error}</p>}
      <ToolResultCard result={result} />
    </div>
  );
}
