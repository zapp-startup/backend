"""
Template-based text generation for reflection_text, conversation messages,
reasoning/explanation JSON, and user facts.
Uses OpenAI when --use-llm is enabled and LLM_API_KEY is set.
"""

from __future__ import annotations

import re

from numpy.random import Generator

from datagen.llm import _call_openai, is_llm_available

# Dataset-level assistant sentence repetition control (reset each pipeline run)
_ASSISTANT_SENTENCE_COUNTS: dict[str, int] = {}


def reset_conversation_dedup() -> None:
    """Clear cross-user assistant sentence counts (call at pipeline start / tests)."""
    _ASSISTANT_SENTENCE_COUNTS.clear()


def _register_assistant_sentence(content: str) -> str:
    n = _ASSISTANT_SENTENCE_COUNTS.get(content, 0)
    _ASSISTANT_SENTENCE_COUNTS[content] = n + 1
    if n >= 10:
        return content + " "
    return content


# ---------------------------------------------------------------------------
# Reflection text templates (Behavior Agent, Section 13)
# ---------------------------------------------------------------------------
_REFLECTION_TEMPLATES = {
    "high_impulse_high_regret": [
        "Bought this on a whim and I'm already regretting it.",
        "Didn't think this through. Probably didn't need it.",
        "Late night purchase I wish I could take back.",
        "Another impulse buy. Need to stop doing this.",
    ],
    "high_impulse_low_regret": [
        "Grabbed this spontaneously but honestly love it.",
        "Wasn't planned but turned out to be a great buy.",
        "Impulse purchase that actually makes me happy.",
    ],
    "low_impulse_high_regret": [
        "Spent a lot of time deciding but still not sure it was worth it.",
        "Researched this thoroughly but the quality disappointed me.",
        "Planned purchase but the price was higher than I expected.",
    ],
    "low_impulse_low_regret": [
        "Exactly what I needed, glad I took the time to decide.",
        "Good purchase, well within budget.",
        "Planned this for a while and happy with the result.",
        "Solid value for money.",
    ],
    "necessity": [
        "Regular expense, nothing special.",
        "Needed this, pretty standard.",
        "Essential purchase.",
    ],
}

_REFLECTION_CATEGORY_NOTES = {
    "groceries": ["for the house", "for the week", "for basics"],
    "transport": ["for getting around", "to get where I needed", "for the commute"],
    "dining": ["for takeout", "for a quick meal", "for going out"],
    "shopping": ["for something non-essential", "for a want, not a need", "for a random pickup"],
    "entertainment": ["for fun", "for a night out", "for downtime"],
    "health": ["for health stuff", "for a refill", "for something practical"],
    "travel": ["for a trip", "for travel plans", "for getting away"],
}

_REFLECTION_OPENERS = [
    "Honestly,",
    "Looking back,",
    "In hindsight,",
    "At the time,",
    "Now that I think about it,",
]

_REFLECTION_CLOSERS = [
    "Need to watch that pattern.",
    "Would probably handle it the same way.",
    "Not the end of the world, but worth noting.",
    "That one stands out more than usual.",
    "It fit the moment better than I expected.",
    "Still not totally sure about it.",
]

_CONVERSATION_USER_TEMPLATES = {
    "subscription_review": [
        "I'm paying ${price}/mo for {merchant}. Is it worth keeping?",
        "Should I cancel my {merchant} subscription?",
        "I barely use {merchant} anymore. Worth the ${price}?",
        "How does my {merchant} usage compare to what I'm paying?",
        "Too many subscriptions — is {merchant} one I should drop first?",
        "My {merchant} bill feels high vs what I use. Thoughts?",
        "Debt is tight; should I cut {merchant} or something else?",
    ],
    "budget_advice": [
        "I feel like I'm spending too much on {category}. Any tips?",
        "Can you help me figure out where my money is going?",
        "I want to cut my monthly expenses by 20%. Where should I start?",
        "My spending feels out of control this month. What should I do?",
        "I had a spending spike — where did the money go?",
        "Subscriptions add up to ${total_subscription_spend_monthly}/mo — help me trim.",
        "I'm oversubscribed and stressed about cash flow.",
    ],
    "item_valuation": [
        "I'm thinking about buying a {item}. Is it worth ${price}?",
        "Should I wait for a sale on {item} or buy now?",
        "Is {item} a good value at ${price} given my budget?",
        "Is {item} a bad idea if money is tight?",
        "Would you buy {item} at ${price} or skip?",
    ],
    "spending_regret": [
        "I keep buying things late at night and regretting it. How do I stop?",
        "I just realized I spent ${amount} on {category} this month. That's too much.",
        "I need help with impulse spending. It's getting worse.",
        "I regret last week's splurge — how do I recover?",
    ],
}

_CONVERSATION_ASSISTANT_TEMPLATES = {
    "subscription_review": [
        "Based on your usage of {merchant}, you're averaging {usage} sessions per week. "
        "At ${price}/mo, that's about ${per_use} per session. {recommendation}.",
        "Looking at your {merchant} subscription: {recommendation}. "
        "Your usage has been {usage_trend} over the past 3 months.",
        "From your data: {merchant} costs ${price}/mo and usage is {usage}/week. {recommendation}",
        "If cash is tight, {recommendation} for {merchant} given ${price}/mo.",
    ],
    "budget_advice": [
        "Looking at your spending breakdown: {top_categories}. "
        "The biggest opportunity to save is in {category} where you're spending "
        "{pct}% more than similar users.",
        "Here's what I'd suggest: {advice}. This could save you about ${savings}/month.",
        "Your top categories this period: {top_categories}. Start with {category}.",
        "You asked about cash flow; your recorded monthly spend in this window is ${amount}.",
    ],
    "item_valuation": [
        "For the {item} at ${price}: based on your preferences and budget, "
        "I'd give it a {score}/100 personal fit score. {recommendation}.",
        "The {item} is {price_assessment} compared to alternatives. "
        "Given your {budget_status} budget status, I'd recommend: {recommendation}.",
        "Fit score ~{score}/150 for {item} at ${price}. {recommendation}.",
    ],
    "spending_regret": [
        "I can see the pattern - {pct}% of your regretted purchases happen after 10pm. "
        "Consider setting up a cooling-off reminder for late-night browsing.",
        "Your impulse spending averages ${amount}/month. Here are three strategies: {strategies}.",
        "From your totals: {top_categories}. Try a 24h rule before non-essential buys.",
    ],
}

_CONVERSATION_TITLE_TEMPLATES = {
    "subscription_review": [
        "{merchant}: keep or cut?",
        "Reviewing {merchant}",
        "{merchant} value check",
        "Do I still use {merchant} enough?",
        "{merchant} subscription decision",
    ],
    "item_valuation": [
        "Worth buying: {item}?",
        "Decision on {item}",
        "{item} price check",
        "Should I wait on {item}?",
        "{item} fit review",
    ],
    "budget_advice": [
        "Monthly spending reset",
        "Where the budget is slipping",
        "Cash flow check-in",
        "Expense trim ideas",
        "Spending pattern review",
    ],
    "spending_regret": [
        "Impulse spend check-in",
        "Regret pattern review",
        "Late-night spending habit",
        "Recovering from overspending",
        "Cooling-off plan",
    ],
    "general": [
        "Money check-in",
        "Planning the next month",
        "Personal finance review",
        "Spending questions",
    ],
}

# ---------------------------------------------------------------------------
# User fact templates (Behavior Agent + Conversation Agent)
# ---------------------------------------------------------------------------
USER_FACT_TEMPLATES = [
    ("prefers_budget_purchases", "Consistently chooses lower-cost options"),
    ("frequent_late_night_shopper", "Makes {pct}% of purchases after 10pm"),
    ("impulse_buyer", "Impulse score averages {score:.2f} across purchases"),
    ("regrets_shopping", "Frequently regrets shopping category purchases"),
    ("over_subscribed", "Subscribes to {count} services but uses only {used} regularly"),
    ("dining_heavy_spender", "Spends {pct}% above average on dining out"),
    ("budget_adherent", "Consistently stays within monthly budget targets"),
    ("price_sensitive", "Often waits for sales before purchasing"),
    ("quality_focused", "Prioritizes quality over price in purchases"),
    ("subscription_churner", "Has cancelled and reactivated {count} subscriptions"),
]


def generate_reflection(rng: Generator, impulse_score: float,
                        regret_score: float, category: str,
                        use_llm: bool = False,
                        merchant_name: str | None = None,
                        amount: float | None = None,
                        satisfaction: int | None = None) -> str | None:
    """Generate reflection text based on impulse/regret scores."""
    if rng.random() < 0.6:
        return None

    if category in ("groceries", "utilities", "rent", "transport"):
        if rng.random() < 0.7:
            return rng.choice(_REFLECTION_TEMPLATES["necessity"])

    if impulse_score > 0.6 and regret_score > 0.5:
        key = "high_impulse_high_regret"
    elif impulse_score > 0.6:
        key = "high_impulse_low_regret"
    elif regret_score > 0.5:
        key = "low_impulse_high_regret"
    else:
        key = "low_impulse_low_regret"

    if use_llm and is_llm_available():
        prompt = (
            f"Write exactly one short sentence (under 15 words) a person might "
            f"think after a purchase. Impulse: {impulse_score:.2f}, regret: {regret_score:.2f}, "
            f"category: {category}. Tone: {key.replace('_', ' ')}. No quotes, just the thought."
        )
        result = _call_openai(prompt, max_tokens=40)
        if result and len(result) < 200:
            return result

    base = str(rng.choice(_REFLECTION_TEMPLATES[key]))
    category_notes = _REFLECTION_CATEGORY_NOTES.get(category, ["for this purchase", "for that spend"])
    merchant_fragment = merchant_name if merchant_name and rng.random() < 0.45 else None
    amount_fragment = None
    if amount is not None and rng.random() < 0.40:
        if amount < 15:
            amount_fragment = "for a small amount"
        elif amount < 75:
            amount_fragment = "for a moderate amount"
        else:
            amount_fragment = "for more than I usually spend"

    note = rng.choice(category_notes)
    parts = []
    if rng.random() < 0.35:
        parts.append(str(rng.choice(_REFLECTION_OPENERS)))
    parts.append(base.rstrip("."))
    if merchant_fragment and rng.random() < 0.5:
        parts.append(f"Especially at {merchant_fragment},")
    else:
        parts.append(note)
    if amount_fragment:
        parts.append(amount_fragment)
    if satisfaction is not None and rng.random() < 0.35:
        if satisfaction >= 8:
            parts.append("and it mostly paid off")
        elif satisfaction <= 4:
            parts.append("and the result was underwhelming")
    if rng.random() < 0.55:
        parts.append(str(rng.choice(_REFLECTION_CLOSERS)))

    text = " ".join(str(part).strip() for part in parts if part).replace(" ,", ",")
    return re.sub(r"\s+", " ", text).strip()


def generate_conversation_messages(
    rng: Generator,
    context_type: str,
    num_messages: int,
    template_vars: dict,
    use_llm: bool = False,
) -> list[dict[str, str]]:
    """
    Generate a list of {role, content} messages for a conversation.
    Alternates user/assistant roles.
    """
    if use_llm and is_llm_available():
        context_desc = _format_context_for_llm(context_type, template_vars)
        prompt = (
            f"Generate a realistic chat between a user and a personal finance assistant. "
            f"Context: {context_desc}. "
            f"Format: alternate lines starting with 'User: ' and 'Assistant: '. "
            f"Write exactly {num_messages} exchanges. Keep each message 1-3 sentences."
        )
        result = _call_openai(prompt, max_tokens=400)
        if result:
            parsed = _parse_conversation_from_llm(result, num_messages)
            if parsed:
                return parsed

    messages = []
    user_templates = _CONVERSATION_USER_TEMPLATES.get(context_type, _CONVERSATION_USER_TEMPLATES["budget_advice"])
    asst_templates = _CONVERSATION_ASSISTANT_TEMPLATES.get(context_type, _CONVERSATION_ASSISTANT_TEMPLATES["budget_advice"])

    for i in range(num_messages):
        if i % 2 == 0:
            tpl = rng.choice(user_templates)
            role = "user"
        else:
            tpl = rng.choice(asst_templates)
            role = "assistant"

        try:
            content = tpl.format(**{k: v for k, v in template_vars.items()})
        except (KeyError, IndexError):
            content = tpl.split("{")[0].strip() or tpl

        content = _embellish_conversation_message(
            rng,
            role,
            context_type,
            content,
            template_vars,
        )

        if role == "assistant":
            content = _register_assistant_sentence(content)

        messages.append({"role": role, "content": content})

    return messages


def generate_conversation_title(rng: Generator, context_type: str, template_vars: dict) -> str:
    templates = _CONVERSATION_TITLE_TEMPLATES.get(
        context_type,
        _CONVERSATION_TITLE_TEMPLATES["general"],
    )
    tpl = str(rng.choice(templates))
    try:
        title = tpl.format(**template_vars)
    except (KeyError, IndexError):
        title = tpl
    return re.sub(r"\s+", " ", title).strip()


def _embellish_conversation_message(
    rng: Generator,
    role: str,
    context_type: str,
    content: str,
    template_vars: dict,
) -> str:
    if role == "user":
        addons = {
            "subscription_review": [
                "I want the honest version.",
                "I'm trying to cut waste.",
                "This one has been on my mind for a while.",
            ],
            "budget_advice": [
                "I need something practical, not generic.",
                "I'm trying to get ahead before next month.",
                "I don't want to keep repeating this pattern.",
            ],
            "item_valuation": [
                "I can wait if the math says wait.",
                "I'm trying not to rationalize this one.",
                "I only want it if it actually fits my budget.",
            ],
            "spending_regret": [
                "The pattern is getting predictable.",
                "I notice it most when I'm tired.",
                "I want a fix I can stick to.",
            ],
        }.get(context_type, [])
    else:
        category = template_vars.get("category", "spending")
        addons = {
            "subscription_review": [
                "The main driver here is usage versus price.",
                "Status and recent activity matter more than the label.",
                "If you keep it, it should earn its place each month.",
            ],
            "budget_advice": [
                f"I'd start with {category} before cutting essentials.",
                "Small recurring leaks usually matter more than one-off wins.",
                "The goal is a plan you can repeat next month.",
            ],
            "item_valuation": [
                "I'm weighting fit and budget more than hype here.",
                "The recommendation is about value in your context, not just sticker price.",
                "If this is optional, patience is part of the decision.",
            ],
            "spending_regret": [
                "The useful fix is to interrupt the pattern before checkout.",
                "A short cooling-off rule usually beats willpower alone.",
                "You do not need a perfect month to improve the pattern.",
            ],
        }.get(context_type, [])

    if addons and rng.random() < 0.55:
        addon = str(rng.choice(addons))
        if addon not in content:
            content = f"{content} {addon}"
    return content


def _format_context_for_llm(context_type: str, template_vars: dict) -> str:
    """Format template_vars for the LLM prompt."""
    parts = [f"topic={context_type}"]
    for k, v in list(template_vars.items())[:6]:
        if v is not None:
            parts.append(f"{k}={v}")
    return ", ".join(str(p) for p in parts)


def _parse_conversation_from_llm(text: str, target_count: int) -> list[dict[str, str]] | None:
    """Parse 'User: ...' and 'Assistant: ...' lines into message dicts."""
    messages = []
    pattern = re.compile(r"^(User|Assistant):\s*(.+)$", re.IGNORECASE | re.MULTILINE)
    for m in pattern.finditer(text):
        role = "user" if m.group(1).lower() == "user" else "assistant"
        content = m.group(2).strip()
        if content:
            messages.append({"role": role, "content": content})

    if len(messages) >= 2 and len(messages) <= target_count + 2:
        return messages[:target_count]
    return None


def generate_explanation_json(
    rng: Generator,
    valuation_type: str,
    scores: dict,
) -> dict:
    """Generate structured explanation JSON for valuations."""
    if valuation_type == "subscription":
        drivers = []
        if scores.get("usage", 0) < 0.3:
            drivers.append("Low usage frequency reduces perceived value")
        if scores.get("cost_ratio", 0) > 0.1:
            drivers.append("Cost represents significant portion of budget")
        if scores.get("alternatives", 0) > 0.5:
            drivers.append("Cheaper alternatives available in this category")
        if scores.get("habit", 0) > 0.7:
            drivers.append("Strong usage habit suggests continued value")
        if not drivers:
            drivers.append("Moderate value alignment with spending patterns")

        return {
            "summary": drivers[0],
            "drivers": drivers,
            "metrics": {k: round(v, 3) for k, v in scores.items()},
        }

    return {
        "summary": f"Personal fit score: {scores.get('score', 50)}/100",
        "pros": _sample_pros(rng, scores),
        "cons": _sample_cons(rng, scores),
        "metrics": {k: round(v, 3) if isinstance(v, float) else v for k, v in scores.items()},
    }


def _sample_pros(rng: Generator, scores: dict) -> list[str]:
    pool = [
        "Aligns with your stated quality preferences",
        "Good price relative to market average",
        "Matches a recurring need in your spending pattern",
        "High satisfaction predicted based on similar purchases",
        "Within your comfortable budget range",
    ]
    return list(rng.choice(pool, size=min(3, len(pool)), replace=False))


def _sample_cons(rng: Generator, scores: dict) -> list[str]:
    pool = [
        "Price is above your typical range for this category",
        "Similar item already owned or subscribed to",
        "Low predicted usage based on your patterns",
        "Better alternatives available at lower price",
        "May strain this month's discretionary budget",
    ]
    return list(rng.choice(pool, size=min(2, len(pool)), replace=False))
