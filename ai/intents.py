import re

INTENT_ASK = "ask"
INTENT_EDIT = "edit"
INTENT_RECOMMEND = "recommend"
INTENT_SUMMARIZE = "summarize"


_INTENT_PATTERNS = {
    INTENT_SUMMARIZE: [
        r"\bsummar(?:y|ize|ise|ized|ised|izing|ising)\b",
        r"\btl;dr\b",
        r"\brecap\b",
        r"\bkey points?\b",
    ],
    INTENT_EDIT: [
        r"\b(edit|rewrite|rephrase|fix|improve|update|change|shorten|expand|polish)\b",
        r"\bmake this\b",
    ],
    INTENT_RECOMMEND: [
        r"\b(recommend|suggest|advice|best option|what should i|which should i|should i)\b",
    ],
}


_PRIORITY = [INTENT_SUMMARIZE, INTENT_EDIT, INTENT_RECOMMEND]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def classify_intent(content: str) -> dict:
    normalized = _normalize(content)
    matches = {intent: [] for intent in _PRIORITY}

    for intent in _PRIORITY:
        for pattern in _INTENT_PATTERNS[intent]:
            if re.search(pattern, normalized):
                matches[intent].append(pattern)

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
        },
        "supported_intents": [INTENT_ASK, INTENT_EDIT, INTENT_RECOMMEND, INTENT_SUMMARIZE],
    }
