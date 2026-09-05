import { useState } from "react";
import { Field, SubmitButton } from "./RefundTool";
import { ApiError, reconcileMarketplace } from "../../api/client";
import { ToolResultCard } from "../../components/ToolResultCard";
import type { MarketplaceTransfer } from "../../api/types";

// Real example from data/marketplace_generator.py's first scenario -
// a Route-style split payment across two linked accounts.
const EXAMPLE = { txnId: "route_txn_001", netAmount: "10350.00", commission: "375.00", sellerA: "9500.00", sellerB: "475.00" };

const STATUSES: MarketplaceTransfer["status"][] = ["settled", "on_hold", "reversed"];

export function MarketplaceTool() {
  const [txnId, setTxnId] = useState(EXAMPLE.txnId);
  const [netAmount, setNetAmount] = useState(EXAMPLE.netAmount);
  const [commission, setCommission] = useState(EXAMPLE.commission);
  const [sellerAAmount, setSellerAAmount] = useState(EXAMPLE.sellerA);
  const [sellerAStatus, setSellerAStatus] = useState<MarketplaceTransfer["status"]>("settled");
  const [sellerBAmount, setSellerBAmount] = useState(EXAMPLE.sellerB);
  const [sellerBStatus, setSellerBStatus] = useState<MarketplaceTransfer["status"]>("settled");
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await reconcileMarketplace({
        gateway_record: { transaction_id: txnId, net_amount: Number(netAmount) },
        transfers: [
          { linked_account_id: "seller_a", amount: Number(sellerAAmount), status: sellerAStatus },
          { linked_account_id: "seller_b", amount: Number(sellerBAmount), status: sellerBStatus },
        ],
        platform_commission: Number(commission),
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
        Route-style split-payment reconciliation - one payment splitting into transfers to multiple linked accounts after platform commission. Try setting a transfer to "on_hold" or "reversed".
      </p>
      <form onSubmit={submit} className="ledger-card px-6 py-5 space-y-3">
        <div className="flex items-end gap-4">
          <Field label="Transaction ID" value={txnId} onChange={setTxnId} />
          <Field label="Net amount" value={netAmount} onChange={setNetAmount} type="number" />
          <Field label="Platform commission" value={commission} onChange={setCommission} type="number" />
        </div>
        <div className="flex items-end gap-4">
          <Field label="Seller A amount" value={sellerAAmount} onChange={setSellerAAmount} type="number" />
          <StatusSelect label="Seller A status" value={sellerAStatus} onChange={setSellerAStatus} />
        </div>
        <div className="flex items-end gap-4 pt-2 border-t border-ink-text/10">
          <Field label="Seller B amount" value={sellerBAmount} onChange={setSellerBAmount} type="number" />
          <StatusSelect label="Seller B status" value={sellerBStatus} onChange={setSellerBStatus} />
          <SubmitButton loading={loading} />
        </div>
      </form>
      {error && <p className="text-flagged font-mono text-sm mt-3">{error}</p>}
      <ToolResultCard result={result} />
    </div>
  );
}

function StatusSelect({ label, value, onChange }: { label: string; value: MarketplaceTransfer["status"]; onChange: (v: MarketplaceTransfer["status"]) => void }) {
  return (
    <div className="flex-1">
      <label className="block font-mono text-[11px] text-ink-text/50 uppercase tracking-wide mb-1.5">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as MarketplaceTransfer["status"])}
        className="w-full font-mono text-sm border border-ink-text/15 rounded px-3 py-2 bg-white/50 focus:outline-none focus:ring-2 focus:ring-accent"
      >
        {STATUSES.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>
    </div>
  );
}
