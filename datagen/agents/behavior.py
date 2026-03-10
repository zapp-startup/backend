"""
Agent 7: Behavior Agent (Section 13)
Labels transactions with impulse/regret scores, satisfaction, repurchase
likelihood, reflection text, and generates user facts.
Writes to: transactions_transaction behavioral columns, ai_userfact
"""

from __future__ import annotations

from datagen.agents.base import BaseAgent
from datagen.config import IMPULSE_COEFFICIENTS, REGRET_COEFFICIENTS, RESEARCHED_COEFFICIENTS
from datagen.distributions import clip01, logistic
from datagen.state import UserState
from datagen.text import USER_FACT_TEMPLATES, generate_reflection


NEED_CATEGORIES = {"groceries", "utilities", "rent", "transport", "health", "bills"}


class BehaviorAgent(BaseAgent):
    """Annotate transactions with behavioral scores and generate user facts."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng

        all_txns = []
        for key in ("spend_transactions", "obligation_transactions",
                     "subscription_transactions", "income_transactions"):
            all_txns.extend(context.get(key, []))

        spend_txns = [t for t in all_txns if t.get("direction") == "spend"]

        max_llm_reflections = context.get("max_llm_reflections", 20)
        llm_reflection_count = 0

        annotated_facts = []
        impulse_scores = []
        regret_scores = []
        late_night_count = 0
        total_spend_count = len(spend_txns)

        for txn in spend_txns:
            cat = txn.get("spend_category") or txn.get("category", "other")
            is_need = cat in NEED_CATEGORIES
            occurred = txn["occurred_at"]
            is_late_night = occurred.hour >= 22 or occurred.hour < 5
            if is_late_night:
                late_night_count += 1

            days_since = state.days_since_payday(occurred.date())
            is_payday_window = 1.0 if days_since <= 3 else 0.0

            # Section 13.5: Impulse score
            ic = IMPULSE_COEFFICIENTS
            impulse_raw = (
                ic["a0"]
                + ic["a_impulse"] * state.impulse
                + ic["a_novelty"] * state.novelty_seeking
                + ic["a_late_night"] * (1.0 if is_late_night else 0.0)
                + ic["a_payday"] * is_payday_window
                - ic["a_budget"] * state.budget_adherence
                + float(rng.normal(0, ic["sigma"]))
            )
            impulse_score = clip01(impulse_raw)

            # Satisfaction: inversely related to impulse for non-necessities
            if is_need:
                satisfaction = int(max(1, min(10, 6 + rng.normal(0, 1.5))))
            else:
                base_sat = 7 - impulse_score * 3 + state.quality_preference * 2
                satisfaction = int(max(1, min(10, base_sat + rng.normal(0, 1.2))))

            # Section 13.5: Regret score
            rc = REGRET_COEFFICIENTS
            amount_ratio = float(txn["amount"]) / max(1.0, float(state.monthly_income))
            regret_raw = (
                rc["t0"]
                + rc["t_impulse"] * impulse_score
                + rc["t_amount_ratio"] * amount_ratio
                - rc["t_need"] * (1.0 if is_need else 0.0)
                - rc["t_satisfaction"] * (satisfaction / 10.0)
                + float(rng.normal(0, rc["sigma"]))
            )
            regret_score = clip01(regret_raw)

            # Regret rating (0-100)
            regret_rating = int(max(0, min(100, regret_score * 100 + rng.normal(0, 5))))

            # Repurchase likelihood
            if is_need:
                repurchase = int(max(0, min(100, 70 + rng.normal(0, 10))))
            else:
                repurchase = int(max(0, min(100,
                    (1 - regret_score) * 60 + satisfaction * 5 + rng.normal(0, 8))))

            # Usage frequency (times per week)
            if cat in ("groceries", "transport"):
                usage_freq = int(max(1, min(14, rng.poisson(4))))
            elif cat in ("entertainment", "dining"):
                usage_freq = int(max(0, min(7, rng.poisson(2))))
            else:
                usage_freq = int(max(0, min(5, rng.poisson(1)))) if rng.random() < 0.5 else None

            # Section 13.5: Researched flag
            rcoeffs = RESEARCHED_COEFFICIENTS
            researched_z = (
                rcoeffs["d0"]
                + rcoeffs["d_budget"] * state.budget_adherence
                + rcoeffs["d_quality"] * state.quality_preference
                - rcoeffs["d_impulse"] * state.impulse
            )
            researched = rng.random() < logistic(researched_z) if not is_need else None

            # Considered at (for researched purchases)
            considered_at = None
            if researched:
                hours_before = int(rng.integers(1, 72))
                considered_at = txn["occurred_at"] - __import__("datetime").timedelta(hours=hours_before)

            # Used buy advisor
            used_advisor = bool(rng.random() < 0.15) if not is_need else False

            # Reflection text (cap LLM calls per user when --use-llm)
            use_llm_this = self.use_llm and (llm_reflection_count < max_llm_reflections)
            reflection = generate_reflection(
                rng, impulse_score, regret_score, cat, use_llm_this
            )
            if use_llm_this:
                llm_reflection_count += 1

            # Store annotations on the transaction dict
            txn["impulse_score"] = round(impulse_score, 4)
            txn["regret_score"] = round(regret_score, 4)
            txn["satisfaction_rating"] = satisfaction
            txn["regret_rating"] = regret_rating
            txn["repurchase_likelihood"] = repurchase
            txn["usage_frequency"] = usage_freq
            txn["reflection_text"] = reflection
            txn["considered_at"] = considered_at
            txn["used_buy_advisor"] = used_advisor
            txn["self_report_researched"] = researched

            impulse_scores.append(impulse_score)
            regret_scores.append(regret_score)

        # Generate user facts when patterns are observed (Section 13.6)
        if total_spend_count > 0:
            avg_impulse = sum(impulse_scores) / max(1, len(impulse_scores))
            avg_regret = sum(regret_scores) / max(1, len(regret_scores))
            late_night_frac = late_night_count / total_spend_count

            if avg_impulse > 0.55:
                annotated_facts.append({
                    "fact_key": "impulse_buyer",
                    "fact_value_json": {"avg_impulse": round(avg_impulse, 3)},
                    "source": "inferred",
                    "confidence": round(min(0.95, 0.5 + avg_impulse * 0.4), 2),
                })
            if late_night_frac > 0.15:
                annotated_facts.append({
                    "fact_key": "frequent_late_night_shopper",
                    "fact_value_json": {"pct": round(late_night_frac * 100, 1)},
                    "source": "inferred",
                    "confidence": round(min(0.95, 0.5 + late_night_frac), 2),
                })
            if avg_regret > 0.4:
                annotated_facts.append({
                    "fact_key": "regrets_shopping",
                    "fact_value_json": {"avg_regret": round(avg_regret, 3)},
                    "source": "inferred",
                    "confidence": round(min(0.95, 0.5 + avg_regret * 0.3), 2),
                })
            if state.budget_adherence > 0.65:
                annotated_facts.append({
                    "fact_key": "budget_adherent",
                    "fact_value_json": {"score": round(state.budget_adherence, 3)},
                    "source": "inferred",
                    "confidence": round(min(0.95, 0.5 + state.budget_adherence * 0.3), 2),
                })
            if state.quality_preference > 0.65:
                annotated_facts.append({
                    "fact_key": "quality_focused",
                    "fact_value_json": {"preference": round(state.quality_preference, 3)},
                    "source": "inferred",
                    "confidence": round(min(0.95, 0.5 + state.quality_preference * 0.3), 2),
                })

        return {"behavior_facts": annotated_facts}
