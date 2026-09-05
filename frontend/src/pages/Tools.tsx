import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BatchTool } from "./tools/BatchTool";
import { ChargebackTool } from "./tools/ChargebackTool";
import { FxTool } from "./tools/FxTool";
import { MarketplaceTool } from "./tools/MarketplaceTool";
import { RefundTool } from "./tools/RefundTool";

/**
 * Five standalone forms hitting the five reconciliation endpoints, no
 * full run required. Pre-filled with real example data from the
 * backend's own data/*_generator.py scenarios, editable, and genuinely
 * submitted to the real backend.
 *
 * Reads ?tab=&transaction_id= for the cross-reference links from
 * ResultsTables.tsx - this only pre-fills a form, never changes any
 * pipeline logic or decision counts.
 */
const TABS = [
  { id: "refunds", label: "Refunds" },
  { id: "batches", label: "Batches" },
  { id: "fx", label: "FX" },
  { id: "marketplace", label: "Marketplace" },
  { id: "chargebacks", label: "Chargebacks" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function Tools() {
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const crossCheckTransactionId = searchParams.get("transaction_id") ?? undefined;
  const validInitialTab = TABS.some((t) => t.id === requestedTab) ? (requestedTab as TabId) : "refunds";

  const [active, setActive] = useState<TabId>(validInitialTab);

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <p className="font-mono text-xs tracking-[0.2em] text-paper-text/50 uppercase mb-2">Reconciliation tools</p>
      <h1 className="page-heading mb-6">Standalone checks</h1>

      <div className="flex items-center gap-1 mb-6 border-b border-rule">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActive(tab.id)}
            className={`px-4 py-2 font-mono text-sm transition-colors border-b-2 -mb-px ${
              active === tab.id
                ? "border-accent text-paper-text"
                : "border-transparent text-paper-text/40 hover:text-paper-text/70"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {active === "refunds" && <RefundTool initialTransactionId={active === validInitialTab ? crossCheckTransactionId : undefined} />}
      {active === "batches" && <BatchTool initialTransactionId={active === validInitialTab ? crossCheckTransactionId : undefined} />}
      {active === "fx" && <FxTool />}
      {active === "marketplace" && <MarketplaceTool />}
      {active === "chargebacks" && <ChargebackTool />}
    </div>
  );
}
