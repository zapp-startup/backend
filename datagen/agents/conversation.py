"""
Agent 10: AI Conversation Agent (Section 16)
Generates realistic assistant conversation history and user facts.
Writes to: ai_conversation, ai_message, ai_userfact
"""

from __future__ import annotations

from datetime import datetime, timedelta

from datagen.agents.base import BaseAgent
from datagen.config import CONVERSATION_LAMBDA_BASE, CONVERSATION_LAMBDA_ENGAGED, MESSAGE_COUNT_NU
from datagen.distributions import make_aware_dt, sample_poisson
from datagen.state import UserState
from datagen.text import generate_conversation_messages, generate_conversation_title


CONTEXT_TYPES = ["subscription", "product", "budgeting", "general"]
CONTEXT_WEIGHTS = [0.30, 0.25, 0.30, 0.15]


def _monthly_sub_cost(s: dict) -> float:
    p = float(s.get("price", 0) or 0)
    bc = s.get("billing_cycle", "monthly")
    if bc == "yearly":
        return p / 12.0
    if bc == "weekly":
        return p * 52.0 / 12.0
    return p


def _dedupe_and_fix_roles(messages: list[dict]) -> list[dict]:
    """No duplicate content per role; alternates user/assistant starting with user."""
    seen_user: set[str] = set()
    seen_asst: set[str] = set()
    out: list[dict] = []
    expect_user = True
    for msg in messages:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user" and not expect_user:
            continue
        if role == "assistant" and expect_user:
            continue
        if role == "user":
            if content in seen_user:
                continue
            seen_user.add(content)
            expect_user = False
        else:
            if content in seen_asst:
                continue
            seen_asst.add(content)
            expect_user = True
        out.append({"role": role, "content": content})
    return out


class ConversationAgent(BaseAgent):
    """Generate AI conversation threads and extract user facts."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        subscriptions = context.get("subscriptions", [])
        item_valuations = context.get("item_valuations", [])
        sub_valuations = context.get("subscription_valuations", [])
        start_date = context["start_date"]
        end_date = context["end_date"]
        generated_at = context.get("generated_at")

        lam = CONVERSATION_LAMBDA_ENGAGED if state.subscription_engagement in (
            "heavy", "churn_prone",
        ) else CONVERSATION_LAMBDA_BASE

        if state.subscription_burden_state in ("stretched", "overloaded"):
            lam += 1.5
        if any(v.get("recommendation") == "skip" for v in sub_valuations):
            lam += 1.0
        spend_txns = context.get("spend_transactions", [])
        months = max(1.0, (end_date - start_date).days / 30.0)
        monthly_spend = sum(float(t.get("amount", 0)) for t in spend_txns) / months
        if monthly_spend > 1.2 * float(state.monthly_income):
            lam += 1.0
        if state.budget_adherence < 0.35:
            lam += 0.5

        n_convos = max(1, sample_poisson(rng, lam))

        conversations = []

        for _ in range(n_convos):
            ctx_type = str(rng.choice(CONTEXT_TYPES, p=CONTEXT_WEIGHTS))

            n_msgs = 2 + sample_poisson(rng, MESSAGE_COUNT_NU)
            n_msgs = min(n_msgs, 12)
            if n_msgs % 2 == 1:
                n_msgs += 1

            days_range = max(1, (end_date - start_date).days)
            convo_start = make_aware_dt(
                datetime(
                    start_date.year,
                    start_date.month,
                    start_date.day,
                    int(rng.integers(8, 22)),
                    int(rng.integers(0, 60)),
                ) + timedelta(days=int(rng.integers(0, days_range)))
            )
            if generated_at is not None and convo_start > generated_at:
                convo_start = generated_at

            linked_sub_idx = None
            linked_item_idx = None
            template_vars = self._build_template_vars(state, context)

            if ctx_type == "subscription" and subscriptions:
                linked_sub_idx = int(rng.integers(0, len(subscriptions)))
                sub = subscriptions[linked_sub_idx]
                template_vars["merchant"] = sub.get("merchant_info", {}).get("name", "Unknown")
                template_vars["price"] = str(sub.get("price", "9.99"))
                uf = float(sub.get("usage_frequency", 3.0 / 7.0) or 0)
                template_vars["usage"] = str(round(uf, 3))
                template_vars["per_use"] = str(round(
                    float(sub.get("price", 10)) / max(0.05, uf * 28), 2
                ))
                template_vars["recommendation"] = (
                    "I'd suggest keeping it" if uf > (3.0 / 7.0)
                    else "You might want to reconsider"
                )
                template_vars["usage_trend"] = rng.choice(["increasing", "steady", "declining"])
                msg_context = "subscription_review"
            elif ctx_type == "product" and item_valuations:
                linked_item_idx = int(rng.integers(0, len(item_valuations)))
                item = item_valuations[linked_item_idx]
                template_vars["item"] = item.get("item_name", "product")
                template_vars["price"] = str(item.get("observed_price", "50"))
                template_vars["score"] = str(item.get("personal_value_score", 50))
                template_vars["recommendation"] = item.get("recommendation", "wait")
                template_vars["price_assessment"] = (
                    "fairly priced" if item.get("recommendation") == "buy" else "overpriced"
                )
                template_vars["budget_status"] = state.liquidity
                msg_context = "item_valuation"
            elif ctx_type == "budgeting":
                msg_context = "budget_advice"
            else:
                msg_context = rng.choice(["budget_advice", "spending_regret"])

            messages = generate_conversation_messages(
                rng, msg_context, n_msgs, template_vars, self.use_llm
            )
            messages = _dedupe_and_fix_roles(messages)

            msg_time = convo_start
            for i, msg in enumerate(messages):
                if i > 0:
                    msg_time += timedelta(minutes=int(rng.integers(1, 5)))
                    if generated_at is not None and msg_time > generated_at:
                        msg_time = generated_at
                msg["created_at"] = msg_time

            title = generate_conversation_title(rng, msg_context, template_vars)

            conversations.append({
                "title": title,
                "context_type": ctx_type,
                "linked_sub_idx": linked_sub_idx,
                "linked_item_idx": linked_item_idx,
                "created_at": convo_start,
                "messages": messages,
            })

        subs = context.get("subscriptions", [])
        cancelled_count = sum(1 for s in subs if s.get("status") == "canceled")
        reactivated_count = sum(s.get("reactivation_count", 0) for s in subs)
        active_count = sum(1 for s in subs if s.get("status") == "active")
        churn_basis = max(1, len(subs))
        churn_rate = cancelled_count / churn_basis

        conversation_facts = []
        if cancelled_count >= 3 and (reactivated_count >= 1 or churn_rate >= 0.45):
            conversation_facts.append({
                "fact_key": "subscription_churner",
                "fact_value_json": {
                    "cancelled": cancelled_count,
                    "reactivated": reactivated_count,
                    "active": active_count,
                    "churn_rate": round(churn_rate, 3),
                },
                "source": "inferred",
                "confidence": round(min(0.95, 0.45 + cancelled_count * 0.08 + reactivated_count * 0.05), 2),
            })

        active_subs = [s for s in subs if s.get("status") == "active"]
        mi = max(1.0, float(state.monthly_income))
        monthlyized = sum(_monthly_sub_cost(s) for s in active_subs)
        if len(active_subs) > 6 and monthlyized / mi > 0.12:
            low_usage = sum(1 for s in active_subs if float(s.get("usage_frequency", 0) or 0) < (2.0 / 7.0))
            conversation_facts.append({
                "fact_key": "over_subscribed",
                "fact_value_json": {"total": len(active_subs), "low_usage": low_usage},
                "source": "inferred",
                "confidence": 0.85,
            })

        return {
            "conversations": conversations,
            "conversation_facts": conversation_facts,
        }

    def _build_template_vars(self, state: UserState, context: dict) -> dict:
        """Build template variables from real generated aggregates only."""
        spend_txns = context.get("spend_transactions", [])
        category_totals: dict[str, float] = {}
        for t in spend_txns:
            cat = t.get("spend_category", t.get("category", "other"))
            category_totals[cat] = category_totals.get(cat, 0) + float(t.get("amount", 0))

        top_cats = sorted(category_totals.items(), key=lambda x: -x[1])[:3]
        top_categories_str = ", ".join(f"{c}: ${a:,.0f}" for c, a in top_cats) or "varied"

        monthly_spend = sum(float(t.get("amount", 0)) for t in spend_txns)
        months = max(1, (context["end_date"] - context["start_date"]).days / 30)

        active_subs = [s for s in context.get("subscriptions", []) if s.get("status") == "active"]
        total_sub_cost = sum(_monthly_sub_cost(s) for s in active_subs)

        return {
            "amount": str(round(monthly_spend / months, 2)),
            "category": top_cats[0][0] if top_cats else "spending",
            "top_categories": top_categories_str,
            "pct": str(round(min(100, max(0, 100 * monthly_spend / months / max(1, float(state.monthly_income)))), 0)),
            "savings": str(round(max(0, float(state.monthly_income) * 0.1), 0)),
            "advice": "reduce dining out and review unused subscriptions",
            "strategies": "1) Set a 24hr waiting period, 2) Unsubscribe from marketing emails, 3) Use a wishlist",
            "total_subscription_spend_monthly": str(round(total_sub_cost, 2)),
        }
