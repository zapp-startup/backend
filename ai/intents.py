import re

INTENT_ASK = "ask"
INTENT_UPDATE_SATISFACTION = "update_satisfaction"
INTENT_SMALLTALK = "smalltalk"
INTENT_META_HELP = "meta_help"
INTENT_EDIT = "edit"
INTENT_RECOMMEND = "recommend"
INTENT_SUMMARIZE = "summarize"
INTENT_RECORD_TRANSACTION = "record_transaction"


_SMALLTALK_VARIANT_PATTERNS = {
    "greeting": [
        r"^(hi|hello|hey|yo|hiya|howdy)\b[!.?]*$",
        r"^(good morning|good afternoon|good evening)\b[!.?]*$",
    ],
    "gratitude": [
        r"^(thanks|thank you|thx|ty)\b[!.?]*$",
        r"^(thanks a lot|thank you so much)\b[!.?]*$",
    ],
    "closing": [
        r"^(bye|goodbye|see you|see ya|cya|later)\b[!.?]*$",
        r"^(talk soon|catch you later)\b[!.?]*$",
    ],
}

_SPEND_QUERY_PATTERNS = [
    r"\bhow much (?:have )?i spent\b",
    r"\bhow much did i spend\b",
    r"\bhow much i spent\b",
    r"\bhow much am i spending\b",
    r"\bwhat(?:'s| is) my spending\b",
    r"\bshow me (?:my )?spending\b",
    r"\btell me (?:my )?spending\b",
    r"\bwant to know how much i spent\b",
]
_SPEND_WINDOW_PATTERNS = [
    r"\bin the last \d+ days?\b",
    r"\bover the last \d+ days?\b",
    r"\bpast \d+ days?\b",
    r"\blast \d+ days?\b",
    r"\bpast \d+ weeks?\b",
    r"\blast \d+ weeks?\b",
    r"\blast week\b",
    r"\bpast week\b",
    r"\blast month\b",
    r"\bpast month\b",
]


_INTENT_PATTERNS = {
    INTENT_UPDATE_SATISFACTION: [
        r"\b(set|update|change)\b.{0,80}\bsatisfaction(?:\s+(?:score|rating))?\b",
        r"\bsatisfaction(?:\s+(?:score|rating))?\b.{0,80}\b(set|update|change)\b",
    ],
    INTENT_RECORD_TRANSACTION: [
        r"\b(i\s+)?(bought|purchased|ordered)\b",
        r"\bi\s+got\b(?:(?:.{0,40}\bfor\b)|(?:.{0,40}\bat\b)|(?:.{0,40}\bfrom\b))",
        r"\bi\s+spent\b(?:(?:\s+\$[\d,]+(?:\.\d{1,2})?)|(?:.{0,40}\bon\b)|(?:.{0,40}\bat\b))",
        r"\bi\s+paid\b(?:(?:\s+\$[\d,]+(?:\.\d{1,2})?)|(?:.{0,40}\bfor\b)|(?:.{0,40}\bat\b))",
        r"\b(add|log|record|track)\b.{0,20}\b(transaction|purchase|expense|spend)\b",
    ],
    INTENT_SMALLTALK: [
        pattern
        for patterns in _SMALLTALK_VARIANT_PATTERNS.values()
        for pattern in patterns
    ],
    INTENT_META_HELP: [
        r"^what can you do\??$",
        r"^what do you do\??$",
        r"^how can you help(?: me)?\??$",
        r"^how does this work\??$",
        r"^what can i ask(?: you)?\??$",
        r"^who are you\??$",
        r"^help\??$",
        r"^help me\??$",
    ],
    INTENT_SUMMARIZE: [
        r"\bsummar(?:y|ize|ise|ized|ised|izing|ising)\b",
        r"\btl;dr\b",
        r"\brecap\b",
        r"\bkey points?\b",
    ],
    INTENT_EDIT: [
        r"\b(edit|rewrite|rephrase|shorten|expand|polish)\b",
        r"\b(?:fix|improve|update|change)\b.{0,20}\b(?:this|it|the (?:text|message|response|draft|paragraph|sentence|reply))\b",
        r"\bmake this\b",
    ],
    INTENT_RECOMMEND: [
        r"\b(recommend|suggest|advice|best option|what should i|which should i|should i)\b",
    ],
}


_PRIORITY = [
    INTENT_UPDATE_SATISFACTION,
    INTENT_RECORD_TRANSACTION,
    INTENT_SMALLTALK,
    INTENT_META_HELP,
    INTENT_SUMMARIZE,
    INTENT_EDIT,
    INTENT_RECOMMEND,
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _classify_smalltalk_variant(normalized: str) -> str | None:
    for variant, patterns in _SMALLTALK_VARIANT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, normalized):
                return variant
    return None


def _is_spend_summary_query(normalized: str) -> bool:
    if any(re.search(pattern, normalized) for pattern in _SPEND_QUERY_PATTERNS):
        return True

    return (
        "spent" in normalized
        and any(re.search(pattern, normalized) for pattern in _SPEND_WINDOW_PATTERNS)
        and any(
            phrase in normalized
            for phrase in ("how much", "want to know", "show me", "tell me", "what did")
        )
    )


def classify_intent(content: str) -> dict:
    normalized = _normalize(content)
    matches = {intent: [] for intent in _PRIORITY}
    smalltalk_variant = _classify_smalltalk_variant(normalized)
    spend_summary_query = _is_spend_summary_query(normalized)

    for intent in _PRIORITY:
        for pattern in _INTENT_PATTERNS[intent]:
            if re.search(pattern, normalized):
                matches[intent].append(pattern)

    if spend_summary_query:
        matches[INTENT_RECORD_TRANSACTION] = []

    intent = INTENT_ASK
    for candidate in _PRIORITY:
        if matches[candidate]:
            intent = candidate
            break

    question_like = "?" in normalized or normalized.startswith(
        ("can you", "could you", "would you", "what", "how", "why", "when", "where", "who")
    )

    return {
        "intent": intent,
        "signals": {
            "matched_patterns": matches[intent] if intent != INTENT_ASK else [],
            "question_like": question_like,
            "smalltalk_variant": smalltalk_variant if intent == INTENT_SMALLTALK else None,
            "spend_summary_query": spend_summary_query,
        },
        "supported_intents": [
            INTENT_ASK,
            INTENT_UPDATE_SATISFACTION,
            INTENT_SMALLTALK,
            INTENT_META_HELP,
            INTENT_EDIT,
            INTENT_RECOMMEND,
            INTENT_SUMMARIZE,
            INTENT_RECORD_TRANSACTION,
        ],
    }

