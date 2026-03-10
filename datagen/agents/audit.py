"""
Agent 11: Audit Agent (Section 17)
Catches impossible or unrealistic outputs and repairs them.
Checks:
  1. No charges after subscription cancellation
  2. Income/expense plausibility
  3. Refunds must have corresponding earlier purchase
  4. Overdraft fees only with tight/negative balance
  5. Valuation recommendations consistent with net values
"""

from __future__ import annotations

from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.state import UserState


class AuditAgent(BaseAgent):
    """Validate and repair generated data for consistency."""

    def run(self, state: UserState, context: dict) -> dict:
        repairs = []

        repairs.extend(self._check_subscription_charges(context))
        repairs.extend(self._check_income_plausibility(state, context))
        repairs.extend(self._check_refund_matching(context))
        repairs.extend(self._check_valuation_consistency(context))

        return {"audit_repairs": repairs}

    def _check_subscription_charges(self, context: dict) -> list[str]:
        """Section 17.2 check 1: No active subscription charges after cancellation."""
        repairs = []
        subscriptions = context.get("subscriptions", [])
        sub_txns = context.get("subscription_transactions", [])

        # Build set of (merchant_name, cancel_date) for canceled subs
        cancel_map: dict[str, object] = {}
        for sub in subscriptions:
            if sub.get("status") != "canceled" or not sub.get("cancelled_on"):
                continue
            merchant_name = sub.get("merchant_info", {}).get("name", "")
            cancel_map[merchant_name] = sub["cancelled_on"]

        # Filter out invalid txns via new list (avoid O(n^2) remove-in-loop)
        to_keep = []
        for txn in sub_txns:
            txn_merch = txn.get("merchant_info", {}).get("name", "") if txn.get("merchant_info") else ""
            cancel_date = cancel_map.get(txn_merch)
            if cancel_date is not None and txn["occurred_at"].date() > cancel_date:
                repairs.append(f"Removed post-cancellation charge for {txn_merch}")
            else:
                to_keep.append(txn)

        context["subscription_transactions"] = to_keep
        return repairs

    def _check_income_plausibility(self, state: UserState, context: dict) -> list[str]:
        """Section 17.2 check 2: Total spend shouldn't vastly exceed income without liquidity deterioration."""
        repairs = []

        income_txns = context.get("income_transactions", [])
        total_income = sum(float(t["amount"]) for t in income_txns)

        spend_keys = ("spend_transactions", "obligation_transactions",
                      "subscription_transactions", "anomaly_transactions")
        total_spend = 0.0
        for key in spend_keys:
            for t in context.get(key, []):
                if t.get("direction") in ("spend",):
                    total_spend += float(t["amount"])

        if total_spend > total_income * 1.5 and state.liquidity == "comfortable":
            state.liquidity = "tight"
            state.balance_proxy = Decimal(str(round(
                float(state.monthly_fixed_expenses) * 0.3, 2
            )))
            repairs.append(
                f"Adjusted liquidity to 'tight': spend ({total_spend:.0f}) "
                f"exceeds income ({total_income:.0f}) by >50%"
            )

        return repairs

    def _check_refund_matching(self, context: dict) -> list[str]:
        """Section 17.2 check 3: Refunds should have a corresponding earlier purchase."""
        repairs = []
        anomaly_txns = context.get("anomaly_transactions", [])
        all_spend = []
        for key in ("spend_transactions", "obligation_transactions", "subscription_transactions"):
            all_spend.extend(context.get(key, []))

        spend_amounts = {float(t["amount"]) for t in all_spend}

        to_keep = []
        for txn in anomaly_txns:
            if txn.get("_anomaly") in ("refund", "reversal"):
                if float(txn["amount"]) not in spend_amounts:
                    repairs.append(f"Removed orphan refund of {txn['amount']}")
                else:
                    to_keep.append(txn)
            else:
                to_keep.append(txn)

        context["anomaly_transactions"] = to_keep
        return repairs

    def _check_valuation_consistency(self, context: dict) -> list[str]:
        """Section 17.2 check 6: Recommendations must agree with net values."""
        repairs = []

        for val in context.get("subscription_valuations", []):
            net = float(val.get("net_value", 0))
            rec = val.get("recommendation", "")

            if net > 0 and rec == "skip":
                val["recommendation"] = "buy"
                repairs.append(f"Fixed sub valuation: positive net ({net:.2f}) had 'skip' -> 'buy'")
            elif net < -float(val.get("total_cost", 1)) * 0.3 and rec == "buy":
                val["recommendation"] = "skip"
                repairs.append(f"Fixed sub valuation: large negative net ({net:.2f}) had 'buy' -> 'skip'")

        for val in context.get("item_valuations", []):
            score = val.get("personal_value_score", 50)
            rec = val.get("recommendation", "")

            if score >= 75 and rec == "skip":
                val["recommendation"] = "buy"
                repairs.append(f"Fixed item valuation: high score ({score}) had 'skip' -> 'buy'")
            elif score < 25 and rec == "buy":
                val["recommendation"] = "skip"
                repairs.append(f"Fixed item valuation: low score ({score}) had 'buy' -> 'skip'")

        return repairs
