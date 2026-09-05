import { useState } from "react";
import { Field, SubmitButton } from "./RefundTool";
import { ApiError, reconcileFx } from "../../api/client";
import { ToolResultCard } from "../../components/ToolResultCard";

// Real example from data/fx_generator.py's first scenario.
const EXAMPLE = { txnId: "fx_txn_001", amount: "100.00", currency: "USD", settled: "8300.00", settledCurrency: "INR", rateMin: "82.5", rateMax: "83.5", markup: "0" };

export function FxTool() {
  const [txnId, setTxnId] = useState(EXAMPLE.txnId);
  const [amount, setAmount] = useState(EXAMPLE.amount);
  const [currency, setCurrency] = useState(EXAMPLE.currency);
  const [settled, setSettled] = useState(EXAMPLE.settled);
  const [settledCurrency, setSettledCurrency] = useState(EXAMPLE.settledCurrency);
  const [rateMin, setRateMin] = useState(EXAMPLE.rateMin);
  const [rateMax, setRateMax] = useState(EXAMPLE.rateMax);
  const [markup, setMarkup] = useState(EXAMPLE.markup);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await reconcileFx({
        gateway_record: { transaction_id: txnId, amount: Number(amount), currency },
        bank_record: { settled_amount: Number(settled), currency: settledCurrency },
        rate_min: Number(rateMin),
        rate_max: Number(rateMax),
        markup_bps: Number(markup),
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
        Checks a cross-currency settlement against a real bank FX rate band - flags implausible rates rather than trusting an exact conversion.
      </p>
      <form onSubmit={submit} className="ledger-card px-6 py-5 space-y-3">
        <div className="flex items-end gap-4">
          <Field label="Transaction ID" value={txnId} onChange={setTxnId} />
          <Field label="Gateway amount" value={amount} onChange={setAmount} type="number" />
          <Field label="Currency" value={currency} onChange={setCurrency} />
        </div>
        <div className="flex items-end gap-4">
          <Field label="Settled amount" value={settled} onChange={setSettled} type="number" />
          <Field label="Settled currency" value={settledCurrency} onChange={setSettledCurrency} />
        </div>
        <div className="flex items-end gap-4 pt-2 border-t border-ink-text/10">
          <Field label="Rate min" value={rateMin} onChange={setRateMin} type="number" />
          <Field label="Rate max" value={rateMax} onChange={setRateMax} type="number" />
          <Field label="Markup (bps)" value={markup} onChange={setMarkup} type="number" />
          <SubmitButton loading={loading} />
        </div>
      </form>
      {error && <p className="text-flagged font-mono text-sm mt-3">{error}</p>}
      <ToolResultCard result={result} />
    </div>
  );
}
