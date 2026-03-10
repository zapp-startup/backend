"""
Agent 10: AI Conversation Agent (Section 16)
Generates realistic assistant conversation history and user facts
around spending and subscriptions.
Writes to: ai_conversation, ai_message, ai_userfact
"""

from __future__ import annotations

from datetime import datetime, timedelta

from datagen.agents.base import BaseAgent
from datagen.config import CONVERSATION_LAMBDA_BASE, CONVERSATION_LAMBDA_ENGAGED, MESSAGE_COUNT_NU
from datagen.distributions import sample_poisson
from datagen.state import UserState
from datagen.text import generate_conversation_messages


CONTEXT_TYPES = ["subscription", "product", "budgeting", "general"]
CONTEXT_WEIGHTS = [0.30, 0.25, 0.30, 0.15]


class ConversationAgent(BaseAgent):
    """Generate AI conversation threads and extract user facts."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        subscriptions = context.get("subscriptions", [])
        item_valuations = context.get("item_valuations", [])
        start_date = context["start_date"]
        end_date = context["end_date"]

        # Number of conversations (Section 16.4)
        if state.subscription_engagement in ("heavy", "churn_prone"):
            lam = CONVERSATION_LAMBDA_ENGAGED
        else:
            lam = CONVERSATION_LAMBDA_BASE
        n_convos = max(1, sample_poisson(rng, lam))

        conversations = []
        all_messages = []
        conversation_facts = []

        for _ in range(n_convos):
            # Pick context type
            ctx_type = str(rng.choice(CONTEXT_TYPES, p=CONTEXT_WEIGHTS))

            # Number of messages: 2 + Poisson(nu) (Section 16.4)
            n_msgs = 2 + sample_poisson(rng, MESSAGE_COUNT_NU)
            n_msgs = min(n_msgs, 12)

            # Conversation timestamp
            days_range = max(1, (end_date - start_date).days)
            convo_start = datetime(
                start_date.year, start_date.month, start_date.day,
                int(rng.integers(8, 22)), int(rng.integers(0, 60))
            ) + timedelta(days=int(rng.integers(0, days_range)))

            # Linked entities
            linked_sub_idx = None
            linked_item_idx = None
            template_vars = self._build_template_vars(state, context)

            if ctx_type == "subscription" and subscriptions:
                linked_sub_idx = int(rng.integers(0, len(subscriptions)))
                sub = subscriptions[linked_sub_idx]
                template_vars["merchant"] = sub.get("merchant_info", {}).get("name", "Unknown")
                template_vars["price"] = str(sub.get("price", "9.99"))
                template_vars["usage"] = str(sub.get("usage_frequency", 3))
                template_vars["per_use"] = str(round(
                    float(sub.get("price", 10)) / max(1, sub.get("usage_frequency", 3) * 4), 2
                ))
                template_vars["recommendation"] = "I'd suggest keeping it" if sub.get("usage_frequency", 0) > 3 else "You might want to reconsider"
                template_vars["usage_trend"] = rng.choice(["increasing", "steady", "declining"])
                msg_context = "subscription_review"
            elif ctx_type == "product" and item_valuations:
                linked_item_idx = int(rng.integers(0, len(item_valuations)))
                item = item_valuations[linked_item_idx]
                template_vars["item"] = item.get("item_name", "product")
                template_vars["price"] = str(item.get("observed_price", "50"))
                template_vars["score"] = str(item.get("personal_value_score", 50))
                template_vars["recommendation"] = item.get("recommendation", "wait")
                template_vars["price_assessment"] = "fairly priced" if item.get("recommendation") == "buy" else "overpriced"
                template_vars["budget_status"] = state.liquidity
                msg_context = "item_valuation"
            elif ctx_type == "budgeting":
                msg_context = "budget_advice"
            else:
                msg_context = rng.choice(["budget_advice", "spending_regret"])

            messages = generate_conversation_messages(
                rng, msg_context, n_msgs, template_vars, self.use_llm
            )

            # Add timestamps to messages
            for i, msg in enumerate(messages):
                msg["created_at"] = convo_start + timedelta(minutes=i * int(rng.integers(1, 5)))

            # Title
            title_map = {
                "subscription_review": f"Review: {template_vars.get('merchant', 'subscription')}",
                "item_valuation": f"Should I buy {template_vars.get('item', 'this')}?",
                "budget_advice": "Budget review",
                "spending_regret": "Spending concerns",
            }
            title = title_map.get(msg_context, "Chat with Zapp")

            conversations.append({
                "title": title,
                "context_type": ctx_type,
                "linked_sub_idx": linked_sub_idx,
                "linked_item_idx": linked_item_idx,
                "created_at": convo_start,
                "messages": messages,
            })

        # Section 16.5: Generate facts from repeated behavior
        subs = context.get("subscriptions", [])
        cancelled_count = sum(1 for s in subs if s.get("status") == "canceled")
        reactivated_count = sum(s.get("reactivation_count", 0) for s in subs)

        if cancelled_count >= 2:
            conversation_facts.append({
                "fact_key": "subscription_churner",
                "fact_value_json": {"cancelled": cancelled_count, "reactivated": reactivated_count},
                "source": "inferred",
                "confidence": round(min(0.95, 0.5 + cancelled_count * 0.1), 2),
            })

        active_subs = [s for s in subs if s.get("status") == "active"]
        if len(active_subs) > 6:
            low_usage = sum(1 for s in active_subs if s.get("usage_frequency", 0) < 2)
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
        """Build default template variables for conversation generation."""
        total_sub_cost = sum(
            float(s.get("price", 0)) for s in context.get("subscriptions", [])
            if s.get("status") == "active"
        )

        spend_txns = context.get("spend_transactions", [])
        category_totals: dict[str, float] = {}
        for t in spend_txns:
            cat = t.get("spend_category", t.get("category", "other"))
            category_totals[cat] = category_totals.get(cat, 0) + float(t.get("amount", 0))

        top_cats = sorted(category_totals.items(), key=lambda x: -x[1])[:3]
        top_categories_str = ", ".join(f"{c}: ${a:,.0f}" for c, a in top_cats) or "varied"

        monthly_spend = sum(float(t.get("amount", 0)) for t in spend_txns)
        months = max(1, (context["end_date"] - context["start_date"]).days / 30)

        return {
            "amount": str(round(monthly_spend / months, 2)),
            "category": top_cats[0][0] if top_cats else "spending",
            "top_categories": top_categories_str,
            "pct": str(round(30 + float(self.rng.normal(0, 10)), 0)),
            "savings": str(round(float(self.rng.uniform(50, 300)), 0)),
            "advice": "reduce dining out and review unused subscriptions",
            "strategies": "1) Set a 24hr waiting period, 2) Unsubscribe from marketing emails, 3) Use a wishlist",
        }
