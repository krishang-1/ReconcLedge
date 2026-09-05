"""End-to-end batch runner. Requires GROQ_API_KEY in the environment for
the agent stage (deterministic stage needs no API key at all). Run from
the project root:

    python eval/run_batch.py

Writes results.json to eval/ with the full match/exception list and the
metrics computed strictly from the held-out eval split.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

from llm_client import GroqClient
from matcher import run_deterministic_stage
from metrics import compute_metrics
from react_loop import run_agent_stage

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_data():
    """Loads the three generated JSON datasets from data/."""
    with open(os.path.join(DATA_DIR, "gateway_transactions.json")) as f:
        gateway = json.load(f)
    with open(os.path.join(DATA_DIR, "bank_settlement.json")) as f:
        bank = json.load(f)
    with open(os.path.join(DATA_DIR, "ground_truth.json")) as f:
        ground_truth = json.load(f)
    return gateway, bank, ground_truth


def run():
    """Runs both stages end to end and writes results.json + prints a summary."""
    gateway, bank, ground_truth = load_data()

    det_matched, det_exceptions, needs_agent, unclaimed = run_deterministic_stage(gateway, bank)
    print(f"deterministic stage: {len(det_matched)} matched, {len(det_exceptions)} exceptions, {len(needs_agent)} routed to agent")

    client = GroqClient()
    agent_matched, agent_exceptions = run_agent_stage(needs_agent, unclaimed, client)
    print(f"agent stage: {len(agent_matched)} matched, {len(agent_exceptions)} exceptions")

    all_matched = det_matched + agent_matched
    all_exceptions = det_exceptions + agent_exceptions
    metrics = compute_metrics(all_matched, all_exceptions, ground_truth)

    output = {"matched": all_matched, "exceptions": all_exceptions, "metrics": metrics}
    with open(os.path.join(os.path.dirname(__file__), "results.json"), "w") as f:
        json.dump(output, f, indent=2)

    print()
    print(f"eval-split match rate: {metrics['match_rate']}")
    print(f"eval-split false positive rate: {metrics['false_positive_rate']}")
    print("results written to eval/results.json")


if __name__ == "__main__":
    run()
