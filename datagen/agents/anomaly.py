"""
Agent 8: Anomaly Agent (Section 14)
Injects feed noise and unusual financial events:
duplicate transactions, refunds, reversals, overdraft fees, fraud bursts,
category mislabels.
Target: 1-3% of transactions touched by anomaly logic.
Writes to: transactions_transaction (altered/additional rows)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    ANOMALY_CATEGORY_MISLABEL_P,
    ANOMALY_DUPLICATE_P,
    ANOMALY_FRAUD_YEARLY_P,
    ANOMALY_OVERDRAFT_AMOUNT,
    ANOMALY_OVERDRAFT_MONTHLY_P,
    ANOMALY_REFUND_P,
    ANOMALY_REVERSAL_P,
)
from datagen.state import UserState


class AnomalyAgent(BaseAgent):
    """Inject anomalies into the transaction set."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng

        all_spend_txns = []
        for key in ("spend_transactions", "obligation_transactions", "subscription_transactions"):
            all_spend_txns.extend(context.get(key, []))

        spend_txns = [t for t in all_spend_txns if t.get("direction") == "spend"]

        if not spend_txns:
            return {"anomaly_transactions": []}

        anomaly_txns: list[dict] = []
        total = len(spend_txns)

        # Section 14.5.1: Duplicates
        for txn in spend_txns:
            if rng.random() < ANOMALY_DUPLICATE_P:
                dup = dict(txn)
                delta_secs = int(rng.integers(0, 300))
                dup["occurred_at"] = txn["occurred_at"] + timedelta(seconds=delta_secs)
                dup["_anomaly"] = "duplicate"
                anomaly_txns.append(dup)

        # Section 14.5.2: Refunds
        refund_p = rng.uniform(*ANOMALY_REFUND_P)
        eligible = [t for t in spend_txns if t.get("category") not in ("bills", "subscriptions")]
        for txn in eligible:
            if rng.random() < refund_p:
                lag_days = int(rng.integers(1, 15))
                refund_date = txn["occurred_at"] + timedelta(days=lag_days)
                refund = {
                    "direction": "refund",
                    "amount": txn["amount"],
                    "occurred_at": refund_date,
                    "category": txn.get("category", "other"),
                    "payment_channel": txn.get("payment_channel", "card"),
                    "description_raw": "REFUND " + (txn.get("description_raw", "")[:40]),
                    "merchant_info": txn.get("merchant_info"),
                    "subscription_obj": None,
                    "_anomaly": "refund",
                }
                anomaly_txns.append(refund)

        # Section 14.5.3: Reversals
        reversal_p = rng.uniform(*ANOMALY_REVERSAL_P)
        for txn in spend_txns:
            if rng.random() < reversal_p:
                rev = {
                    "direction": "refund",
                    "amount": txn["amount"],
                    "occurred_at": txn["occurred_at"] + timedelta(hours=int(rng.integers(1, 48))),
                    "category": txn.get("category", "other"),
                    "payment_channel": txn.get("payment_channel", "card"),
                    "description_raw": "REVERSAL " + (txn.get("description_raw", "")[:35]),
                    "merchant_info": txn.get("merchant_info"),
                    "subscription_obj": None,
                    "_anomaly": "reversal",
                }
                anomaly_txns.append(rev)

        # Section 14.5.4: Overdraft fees
        if state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            od_p = rng.uniform(*ANOMALY_OVERDRAFT_MONTHLY_P)
            months_span = max(1, len(spend_txns) // 30)
            for _ in range(months_span):
                if rng.random() < od_p:
                    base_txn = rng.choice(spend_txns)
                    fee_date = base_txn["occurred_at"] + timedelta(days=int(rng.integers(0, 3)))
                    fee_amount = Decimal(str(round(float(rng.uniform(*ANOMALY_OVERDRAFT_AMOUNT)), 2)))
                    anomaly_txns.append({
                        "direction": "spend",
                        "amount": fee_amount,
                        "occurred_at": fee_date,
                        "category": "other",
                        "payment_channel": "bank",
                        "description_raw": "OVERDRAFT FEE",
                        "merchant_obj": None,
                        "subscription_obj": None,
                        "_anomaly": "overdraft_fee",
                    })

        # Section 14.5.5: Fraud burst
        days_in_window = max(1, (context["end_date"] - context["start_date"]).days)
        years = days_in_window / 365.0
        fraud_p = rng.uniform(*ANOMALY_FRAUD_YEARLY_P) * years
        if rng.random() < fraud_p:
            fraud_base_date = spend_txns[int(rng.integers(0, len(spend_txns)))]["occurred_at"]
            n_test = int(rng.integers(3, 7))
            for i in range(n_test):
                test_amount = Decimal(str(round(float(rng.uniform(0.50, 5.00)), 2)))
                anomaly_txns.append({
                    "direction": "spend",
                    "amount": test_amount,
                    "occurred_at": fraud_base_date + timedelta(minutes=int(rng.integers(0, 120)) * i),
                    "category": "other",
                    "payment_channel": "online",
                    "description_raw": f"SUSPICIOUS CHARGE #{i+1}",
                    "merchant_obj": None,
                    "subscription_obj": None,
                    "_anomaly": "fraud_test",
                })
            # Large fraudulent charge
            big_amount = Decimal(str(round(float(rng.uniform(200, 2000)), 2)))
            anomaly_txns.append({
                "direction": "spend",
                "amount": big_amount,
                "occurred_at": fraud_base_date + timedelta(hours=int(rng.integers(4, 24))),
                "category": "other",
                "payment_channel": "online",
                "description_raw": "UNAUTHORIZED CHARGE",
                "merchant_obj": None,
                "subscription_obj": None,
                "_anomaly": "fraud_large",
            })

        # Section 14.5.7: Category mislabels
        mislabel_p = rng.uniform(*ANOMALY_CATEGORY_MISLABEL_P)
        all_categories = ["groceries", "eating_out", "transport", "shopping",
                          "entertainment", "health", "education", "other", "bills"]
        for txn in spend_txns:
            if rng.random() < mislabel_p:
                original = txn.get("category", "other")
                wrong = str(rng.choice([c for c in all_categories if c != original]))
                txn["category"] = wrong
                txn["_anomaly"] = "mislabel"

        # Update balance for anomaly costs
        anomaly_cost = sum(
            t["amount"] for t in anomaly_txns
            if t.get("direction") == "spend"
        )
        anomaly_refunds = sum(
            t["amount"] for t in anomaly_txns
            if t.get("direction") == "refund"
        )
        state.balance_proxy -= anomaly_cost
        state.balance_proxy += anomaly_refunds
        state.update_liquidity()

        return {"anomaly_transactions": anomaly_txns}
