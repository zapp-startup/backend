import re

from transactions.models import TransactionCategory

ADVISOR_CATEGORY_ALIASES = {
    "streaming": {"streaming", "netflix", "spotify", "hulu", "disney", "youtube premium", "audible", "prime video"},
    "grocery": {"grocery", "groceries", "costco", "whole foods", "trader joe", "supermarket", "hellofresh"},
    "fitness": {"fitness", "gym", "headspace", "wellness", "workout"},
    "software": {"software", "adobe", "github", "notion", "dropbox", "microsoft 365", "app", "apps"},
    "utilities": {"utilities", "utility", "electric", "water", "internet", "phone", "rent", "bill", "bills"},
    "food": {"food", "restaurant", "restaurants", "dining", "coffee", "takeout", "meal", "meals"},
    "education": {"education", "course", "courses", "tuition", "duolingo", "class", "classes"},
    "other": {"other", "misc", "miscellaneous"},
}

LOCAL_TO_ADVISOR_CATEGORY = {
    TransactionCategory.SUBSCRIPTIONS: "streaming",
    TransactionCategory.GROCERIES: "grocery",
    TransactionCategory.EATING_OUT: "food",
    TransactionCategory.TRANSPORT: "other",
    TransactionCategory.SHOPPING: "other",
    TransactionCategory.BILLS: "utilities",
    TransactionCategory.ENTERTAINMENT: "streaming",
    TransactionCategory.HEALTH: "fitness",
    TransactionCategory.EDUCATION: "education",
    TransactionCategory.OTHER: "other",
}

ADVISOR_CATEGORIES = tuple(ADVISOR_CATEGORY_ALIASES.keys())

_ALIAS_BOUNDARY = r"(?<!\w){alias}(?!\w)"
ADVISOR_CATEGORY_ALIAS_PATTERNS = {
    category: tuple(
        re.compile(_ALIAS_BOUNDARY.format(alias=re.escape(alias)), re.IGNORECASE)
        for alias in sorted(aliases, key=len, reverse=True)
    )
    for category, aliases in ADVISOR_CATEGORY_ALIASES.items()
}


def extract_requested_category(content: str):
    normalized = (content or "").strip()

    for category, patterns in ADVISOR_CATEGORY_ALIAS_PATTERNS.items():
        if any(pattern.search(normalized) for pattern in patterns):
            return category
    return None
