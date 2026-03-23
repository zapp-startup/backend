"""
Agent 11: Audit Agent (Section 17)
Validates and repairs generated data; cascades subscription removals by _sub_key.
"""

from __future__ import annotations

from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import MERCHANT_CATEGORY_COMPAT
from datagen.state import UserState


class AuditAgent(BaseAgent):
    """Validate and repair generated data for consistency."""

    def run(self, state: UserState, context: dict) -> dict:
        repairs = []

        repairs.extend(self._check_profile_coherence(state, context))
        repairs.extend(self._check_sub_eligibility_cascade(state, context))
        repairs.extend(self._check_subscription_metric_consistency(state, context))
        repairs.extend(self._check_subscription_charges(context))
        repairs.extend(self._check_income_plausibility(state, context))
        repairs.extend(self._check_refund_matching(context))
        repairs.extend(self._check_merchant_category_compat(context))
        repairs.extend(self._check_valuation_realism(context))
        repairs.extend(self._check_conversation_dedup(context))
        repairs.extend(self._check_fact_evidence(context))
        repairs.extend(self._check_user_completeness(context))

        return {"audit_repairs": repairs}

    def _check_profile_coherence(self, state: UserState, context: dict) -> list[str]:
        repairs = []
        if state.household_size < state.dependents_count + 1:
            repairs.append("profile: household_size < dependents + 1")
        re_data = context.get("raw_explicit_data") or {}
        life_stage = str(re_data.get("life_stage", "") or "")
        if life_stage == "family" and state.household_size < 2:
            repairs.append("profile: family life_stage with household_size < 2")
        return repairs

    def _check_sub_eligibility_cascade(self, state: UserState, context: dict) -> list[str]:
        repairs: list[str] = []
        subs = context.get("subscriptions", [])
        to_remove: list[dict] = []
        kept: list[dict] = []
        for sub in subs:
            merch = sub.get("merchant_info") or {}
            if merch.get("eligibility", "not_subscribable") == "not_subscribable":
                to_remove.append(sub)
                repairs.append(f"Removed ineligible subscription {merch.get('name')}")
            else:
                kept.append(sub)

        if not to_remove:
            return repairs

        remove_keys = {s.get("_sub_key") for s in to_remove if s.get("_sub_key")}
        context["subscriptions"] = kept

        sub_txns = context.get("subscription_transactions", [])
        kept_tx = []
        for txn in sub_txns:
            k = txn.get("_sub_key")
            if k and k in remove_keys:
                continue
            if not k:
                m = (txn.get("merchant_info") or {}).get("name", "")
                if any(
                    (r.get("merchant_info") or {}).get("name") == m for r in to_remove
                ):
                    continue
            kept_tx.append(txn)
        context["subscription_transactions"] = kept_tx

        sub_vals = context.get("subscription_valuations", [])
        kept_v = []
        for val in sub_vals:
            ref = val.get("_sub_ref")
            sk = val.get("_sub_key")
            if sk and sk in remove_keys:
                continue
            if ref is not None and ref in to_remove:
                continue
            kept_v.append(val)
        context["subscription_valuations"] = kept_v

        active_cost = 0.0
        for s in kept:
            if s.get("status") == "active":
                p = float(s.get("price", 0) or 0)
                bc = s.get("billing_cycle", "monthly")
                if bc == "yearly":
                    active_cost += p / 12.0
                elif bc == "weekly":
                    active_cost += p * 52.0 / 12.0
                else:
                    active_cost += p
        state.update_subscription_burden(active_cost)

        return repairs

    def _check_subscription_charges(self, context: dict) -> list[str]:
        repairs = []
        subscriptions = context.get("subscriptions", [])
        sub_txns = context.get("subscription_transactions", [])

        cancel_map: dict[str, object] = {}
        for sub in subscriptions:
            if sub.get("status") != "canceled" or not sub.get("cancelled_on"):
                continue
            merchant_name = sub.get("merchant_info", {}).get("name", "")
            cancel_map[merchant_name] = sub["cancelled_on"]

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
        repairs = []

        income_txns = context.get("income_transactions", [])
        total_income = sum(float(t["amount"]) for t in income_txns)

        spend_keys = (
            "spend_transactions", "obligation_transactions",
            "subscription_transactions", "anomaly_transactions",
        )
        total_spend = 0.0
        for key in spend_keys:
            for t in context.get(key, []):
                if t.get("direction") in ("spend",):
                    total_spend += float(t["amount"])

        if total_income > 0 and total_spend > total_income * 1.5 and state.liquidity == "comfortable":
            state.liquidity = "tight"
            state.balance_proxy = Decimal(str(round(
                float(state.monthly_fixed_expenses) * 0.3, 2
            )))
            # Cascade: force a debt transition for the implied monthly deficit
            surplus = total_income - total_spend  # negative value
            state.apply_debt_transition(surplus)
            repairs.append(
                f"Adjusted liquidity to 'tight' and applied debt transition: spend ({total_spend:.0f}) "
                f"exceeds income ({total_income:.0f}) by >50%"
            )

        return repairs

    def _check_subscription_metric_consistency(self, state: UserState, context: dict) -> list[str]:
        """Verify burden_state matches recomputed monthlyized active subscription cost."""
        repairs = []
        active_cost = 0.0
        for s in context.get("subscriptions", []):
            if s.get("status") != "active":
                continue
            p = float(s.get("price", 0) or 0)
            bc = s.get("billing_cycle", "monthly")
            if bc == "yearly":
                active_cost += p / 12.0
            elif bc == "weekly":
                active_cost += p * 52.0 / 12.0
            else:
                active_cost += p

        mi = float(state.monthly_income or 1)
        expected_ratio = active_cost / mi if mi > 0 else 0.0
        from datagen.config import SUBSCRIPTION_BURDEN_THRESHOLDS as SBT
        expected_burden: str
        if expected_ratio < SBT["light"]:
            expected_burden = "light"
        elif expected_ratio < SBT["normal"]:
            expected_burden = "normal"
        elif expected_ratio < SBT["stretched"]:
            expected_burden = "stretched"
        else:
            expected_burden = "overloaded"

        if state.subscription_burden_state != expected_burden:
            state.update_subscription_burden(active_cost)
            repairs.append(
                f"Recomputed subscription_burden_state: "
                f"{state.subscription_burden_state} (was inconsistent)"
            )
        return repairs

    def _check_valuation_realism(self, context: dict) -> list[str]:
        """Warn if all item valuation recommendations collapsed to a single value."""
        repairs = []
        item_vals = context.get("item_valuations", [])
        if len(item_vals) < 5:
            return repairs
        recs = [v.get("recommendation") for v in item_vals if v.get("recommendation")]
        if len(set(recs)) == 1:
            repairs.append(
                f"valuation: all {len(recs)} item recommendations collapsed to '{recs[0]}'"
            )
        return repairs

    def _check_fact_evidence(self, context: dict) -> list[str]:
        """Facts that require repeated transaction evidence must have it."""
        repairs = []
        spend_txns = context.get("spend_transactions", [])
        total_spend = len(spend_txns)

        all_facts = (
            list(context.get("behavior_facts", []))
            + list(context.get("conversation_facts", []))
        )
        for fact in all_facts:
            key = fact.get("fact_key", "")
            if key == "frequent_late_night_shopper":
                late_count = sum(
                    1 for t in spend_txns
                    if (t.get("occurred_at") and
                        (t["occurred_at"].hour >= 22 or t["occurred_at"].hour < 5))
                )
                if total_spend > 0 and late_count / total_spend < 0.10:
                    repairs.append(
                        "fact_evidence: frequent_late_night_shopper lacks threshold support"
                    )
            elif key == "budget_adherent":
                if total_spend == 0:
                    repairs.append("fact_evidence: budget_adherent with no spend transactions")
        return repairs

    def _check_refund_matching(self, context: dict) -> list[str]:
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

    def _check_merchant_category_compat(self, context: dict) -> list[str]:
        repairs = []
        for txn in context.get("spend_transactions", []):
            merch = txn.get("merchant_info") or {}
            mc = merch.get("category", "other")
            sc = txn.get("spend_category", "")
            status = MERCHANT_CATEGORY_COMPAT.get((mc, sc), "allowed")
            if status == "disallowed":
                repairs.append(f"compat: disallowed mapping {mc}->{sc}")
        return repairs

    def _check_conversation_dedup(self, context: dict) -> list[str]:
        repairs = []
        for convo in context.get("conversations", []):
            seen_u: set[str] = set()
            seen_a: set[str] = set()
            for msg in convo.get("messages", []):
                c = (msg.get("content") or "").strip()
                if msg.get("role") == "user":
                    if c in seen_u:
                        repairs.append("conversation: duplicate user content")
                    seen_u.add(c)
                else:
                    if c in seen_a:
                        repairs.append("conversation: duplicate assistant content")
                    seen_a.add(c)
        return repairs

    def _check_user_completeness(self, context: dict) -> list[str]:
        repairs = []
        facts = list(context.get("behavior_facts", [])) + list(
            context.get("conversation_facts", [])
        )
        if any(f.get("fact_key") == "intentionally_sparse" for f in facts):
            return repairs
        income_txns = context.get("income_transactions", [])
        if not income_txns:
            repairs.append("completeness: no income transactions")
        return repairs
