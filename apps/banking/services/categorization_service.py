"""
Zapp transaction categorization service.
Maps Plaid categories to Zapp categories, applies merchant overrides,
and provides effective category resolution.

Pipeline order:
1. user_override_category (if present)
2. merchant override rule (if matched)
3. Plaid personal_finance_category mapping (primary + detailed)
4. legacy Plaid category fallback
5. ML fallback (placeholder for future)
6. Miscellaneous / Unclassified
"""
import logging
import re
from typing import Any

from django.db.models.functions import Length

from apps.banking.categories import ZappPrimaryCategory, ZappSubcategory
from apps.banking.models import BankTransaction, MerchantCategoryRule

logger = logging.getLogger(__name__)

# --- Plaid Personal Finance Category (PFC) -> Zapp mapping ---
# Plaid PFC: {"primary": "TRAVEL", "detailed": "AIRLINES"}. Keys normalized (lowercase, underscores).
# Format: (pfc_primary, pfc_detailed) -> (zapp_primary, zapp_subcategory)
PLAID_PFC_TO_ZAPP: dict[tuple[str, str], tuple[str, str]] = {
    # INCOME
    ("income", ""): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    ("income", "payroll"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    ("income", "wage"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    ("income", "salary"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    ("income", "interest"): (ZappPrimaryCategory.INCOME, ""),
    ("income", "dividend"): (ZappPrimaryCategory.INCOME, ""),
    ("income", "gig"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    ("income", "gig_economy"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    # TRAVEL
    ("travel", ""): (ZappPrimaryCategory.TRAVEL, ""),
    ("travel", "flights"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.FLIGHTS),
    ("travel", "airlines"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.FLIGHTS),
    ("travel", "lodging"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.HOTELS),
    ("travel", "hotels"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.HOTELS),
    ("travel", "vacation_rentals"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.AIRBNB),
    ("travel", "car_rental"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.CAR_RENTAL),
    ("travel", "air_travel"): (ZappPrimaryCategory.TRAVEL, ZappSubcategory.FLIGHTS),
    # TRANSPORTATION
    ("transportation", ""): (ZappPrimaryCategory.TRANSPORTATION, ""),
    ("transportation", "gas"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.GAS),
    ("transportation", "gas_stations"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.GAS),
    ("transportation", "parking"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.PARKING),
    ("transportation", "public_transit"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.PUBLIC_TRANSIT),
    ("transportation", "tolls"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.TOLLS),
    ("transportation", "rideshare"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.RIDE_SHARE),
    ("transportation", "taxi"): (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.RIDE_SHARE),
    # FOOD_AND_DRINK
    ("food_and_drink", ""): (ZappPrimaryCategory.DINING_CAFES, ""),
    ("food_and_drink", "restaurants"): (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.RESTAURANTS),
    ("food_and_drink", "fast_food"): (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.FAST_FOOD),
    ("food_and_drink", "coffee_shop"): (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.COFFEE),
    ("food_and_drink", "groceries"): (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.GROCERIES),
    ("food_and_drink", "alcohol_and_bars"): (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.NIGHTLIFE),
    ("food_and_drink", "supermarkets_and_groceries"): (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.GROCERIES),
    # RENT_AND_UTILITIES
    ("rent_and_utilities", ""): (ZappPrimaryCategory.UTILITIES_BILLS, ""),
    ("rent_and_utilities", "rent"): (ZappPrimaryCategory.HOUSING_LIVING, ZappSubcategory.RENT),
    ("rent_and_utilities", "utilities"): (ZappPrimaryCategory.UTILITIES_BILLS, ""),
    ("rent_and_utilities", "internet_and_cable"): (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.INTERNET),
    ("rent_and_utilities", "phone"): (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.PHONE),
    ("rent_and_utilities", "electricity"): (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.ELECTRICITY),
    # LOAN_PAYMENTS
    ("loan_payments", ""): (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    # BANK_FEES
    ("bank_fees", ""): (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    # ENTERTAINMENT
    ("entertainment", ""): (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ""),
    ("entertainment", "sports"): (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.SPORTS),
    ("entertainment", "concerts_and_shows"): (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.LIVE_EVENTS),
    ("entertainment", "theater"): (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.THEATER),
    ("entertainment", "gyms_and_fitness"): (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.FITNESS_MEMBERSHIP),
    ("entertainment", "tv_shows_and_movies"): (ZappPrimaryCategory.SUBSCRIPTIONS, ZappSubcategory.STREAMING),
    # SHOPPING
    ("shopping", ""): (ZappPrimaryCategory.SHOPPING, ZappSubcategory.GENERAL),
    ("shopping", "electronics"): (ZappPrimaryCategory.SHOPPING, ZappSubcategory.ELECTRONICS),
    ("shopping", "clothing"): (ZappPrimaryCategory.SHOPPING, ZappSubcategory.CLOTHING),
    # PERSONAL_CARE
    ("personal_care", ""): (ZappPrimaryCategory.HEALTH_WELLNESS, ""),
    ("personal_care", "pharmacy"): (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.PHARMACY_ESSENTIALS),
    # HEALTHCARE
    ("healthcare", ""): (ZappPrimaryCategory.HEALTH_WELLNESS, ""),
    ("healthcare", "doctor"): (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.DOCTOR),
    ("healthcare", "pharmacy"): (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.MEDICATION),
    ("healthcare", "dentist"): (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.DENTAL),
    ("healthcare", "therapist"): (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.THERAPY),
    # INSURANCE
    ("insurance", ""): (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.INSURANCE),
    # TRANSFER
    ("transfer", ""): (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    ("transfer", "payroll"): (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    # SUBSCRIPTIONS
    ("subscription", ""): (ZappPrimaryCategory.SUBSCRIPTIONS, ""),
    ("subscriptions", ""): (ZappPrimaryCategory.SUBSCRIPTIONS, ""),
}

# Legacy Plaid category (from category array) -> Zapp mapping
# Used when personal_finance_category is absent
PLAID_LEGACY_TO_ZAPP: dict[str, tuple[str, str]] = {
    "Rent": (ZappPrimaryCategory.HOUSING_LIVING, ZappSubcategory.RENT),
    "Utilities": (ZappPrimaryCategory.UTILITIES_BILLS, ""),
    "Mortgage": (ZappPrimaryCategory.HOUSING_LIVING, ""),
    "Home": (ZappPrimaryCategory.HOUSING_LIVING, ""),
    "Phone": (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.PHONE),
    "Internet": (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.INTERNET),
    "Cable": (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.INTERNET),
    "Insurance": (ZappPrimaryCategory.UTILITIES_BILLS, ZappSubcategory.INSURANCE),
    "Bank Fees": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    "Bank Fees and Charges": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    "Food and Drink": (ZappPrimaryCategory.DINING_CAFES, ""),
    "Groceries": (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.GROCERIES),
    "Supermarkets and Groceries": (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.GROCERIES),
    "Pharmacies": (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.PHARMACY_ESSENTIALS),
    "Drugstores": (ZappPrimaryCategory.GROCERIES_ESSENTIALS, ZappSubcategory.PHARMACY_ESSENTIALS),
    "Restaurants": (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.RESTAURANTS),
    "Coffee Shop": (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.COFFEE),
    "Fast Food": (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.FAST_FOOD),
    "Bars": (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.NIGHTLIFE),
    "Nightlife": (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ZappSubcategory.NIGHTLIFE),
    "Travel": (ZappPrimaryCategory.TRANSPORTATION, ""),
    "Gas": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.GAS),
    "Gas Stations": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.GAS),
    "Parking": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.PARKING),
    "Tolls": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.TOLLS),
    "Rideshare": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.RIDE_SHARE),
    "Taxi": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.RIDE_SHARE),
    "Public Transportation": (ZappPrimaryCategory.TRANSPORTATION, ZappSubcategory.PUBLIC_TRANSIT),
    "Airlines": (ZappPrimaryCategory.TRAVEL, ZappSubcategory.FLIGHTS),
    "Lodging": (ZappPrimaryCategory.TRAVEL, ZappSubcategory.HOTELS),
    "Hotels": (ZappPrimaryCategory.TRAVEL, ZappSubcategory.HOTELS),
    "Shops": (ZappPrimaryCategory.SHOPPING, ZappSubcategory.GENERAL),
    "Merchandise": (ZappPrimaryCategory.SHOPPING, ZappSubcategory.GENERAL),
    "Clothing": (ZappPrimaryCategory.SHOPPING, ZappSubcategory.CLOTHING),
    "Electronics": (ZappPrimaryCategory.SHOPPING, ZappSubcategory.ELECTRONICS),
    "Electronics and Software": (ZappPrimaryCategory.SHOPPING, ZappSubcategory.ELECTRONICS),
    "Sporting Goods": (ZappPrimaryCategory.SHOPPING, ""),
    "Subscription": (ZappPrimaryCategory.SUBSCRIPTIONS, ""),
    "Subscriptions": (ZappPrimaryCategory.SUBSCRIPTIONS, ""),
    "Recreation": (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ""),
    "Entertainment": (ZappPrimaryCategory.ENTERTAINMENT_SOCIAL, ""),
    "Gyms": (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.FITNESS_MEMBERSHIP),
    "Fitness": (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.FITNESS_MEMBERSHIP),
    "Healthcare": (ZappPrimaryCategory.HEALTH_WELLNESS, ""),
    "Health": (ZappPrimaryCategory.HEALTH_WELLNESS, ""),
    "Gyms and Fitness Centers": (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.FITNESS_MEMBERSHIP),
    "Fitness and Recreation": (ZappPrimaryCategory.HEALTH_WELLNESS, ZappSubcategory.FITNESS_MEMBERSHIP),
    "Education": (ZappPrimaryCategory.EDUCATION_CAREER, ""),
    "Schools": (ZappPrimaryCategory.EDUCATION_CAREER, ""),
    "Tuition": (ZappPrimaryCategory.EDUCATION_CAREER, ""),
    "Transfer": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    "Interest": (ZappPrimaryCategory.INCOME, ""),
    "Income": (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    "Payroll": (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    "Salary": (ZappPrimaryCategory.INCOME, ZappSubcategory.PAYROLL),
    "Payment": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    "Loan": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
    "Credit": (ZappPrimaryCategory.FINANCIAL_TRANSFERS, ""),
}


def normalize_merchant_name(name: str) -> str:
    """
    Normalize merchant/transaction name for pattern matching.
    Lowercase, collapse whitespace, remove common noise.
    """
    if not name:
        return ""
    s = str(name).lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)  # remove punctuation for matching
    return s


def _normalize_pfc_value(val: str) -> str:
    """Normalize Plaid PFC value for lookup (lowercase, underscores)."""
    if not val:
        return ""
    s = str(val).lower().strip().replace(" ", "_").replace("-", "_")
    return re.sub(r"_+", "_", s)


def _extract_plaid_pfc(raw: dict) -> tuple[str, str]:
    """
    Extract Plaid personal_finance_category from raw payload.
    Plaid structure: {"primary": "TRAVEL", "detailed": "AIRLINES"}
    Returns (primary, detailed) - normalized for lookup (lowercase, underscores).
    """
    pfc = raw.get("personal_finance_category")
    if isinstance(pfc, dict):
        primary = pfc.get("primary") or pfc.get("primary_category") or ""
        detailed = pfc.get("detailed") or pfc.get("detailed_category") or ""
        return _normalize_pfc_value(primary), _normalize_pfc_value(detailed)
    return "", ""


def map_plaid_pfc_to_zapp(pfc_primary: str, pfc_detailed: str) -> tuple[str, str] | None:
    """
    Map Plaid PFC (primary + detailed) to Zapp primary and subcategory.
    Returns (zapp_primary, zapp_subcategory) or None if no match.
    """
    if not pfc_primary:
        return None
    # Try exact (primary, detailed) first
    key = (pfc_primary, pfc_detailed)
    if key in PLAID_PFC_TO_ZAPP:
        return PLAID_PFC_TO_ZAPP[key]
    # Try primary-only
    key_primary = (pfc_primary, "")
    if key_primary in PLAID_PFC_TO_ZAPP:
        return PLAID_PFC_TO_ZAPP[key_primary]
    return None


def map_plaid_legacy_to_zapp(
    category_primary: str,
    category_detailed: str,
    categories_list: list[str] | None = None,
) -> tuple[str, str]:
    """
    Map legacy Plaid category (from category array) to Zapp.
    Returns (zapp_primary, zapp_subcategory). Fallback to MISCELLANEOUS if no match.
    """
    candidates = []
    if categories_list:
        candidates.extend(categories_list)
    if category_primary:
        candidates.append(category_primary)
    if category_detailed:
        for part in category_detailed.split(","):
            candidates.append(part.strip())

    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if c in PLAID_LEGACY_TO_ZAPP:
            return PLAID_LEGACY_TO_ZAPP[c]
        for k, v in PLAID_LEGACY_TO_ZAPP.items():
            if k in c or c in k:
                return v

    return ZappPrimaryCategory.MISCELLANEOUS, ""


def apply_merchant_override_rules(
    normalized_name: str,
) -> tuple[str, str] | None:
    """
    Apply merchant override rules. Returns (zapp_primary, zapp_subcategory) if match, else None.
    """
    rules = (
        MerchantCategoryRule.objects.filter(is_active=True)
        .annotate(pattern_length=Length("match_pattern"))
        .order_by("-priority", "-pattern_length", "match_pattern")
    )
    for rule in rules:
        if rule.match_pattern.lower() in normalized_name:
            return rule.zapp_primary_category, (rule.zapp_subcategory or "")
    return None


def classify_with_ml_fallback(
    transaction: BankTransaction,
    plaid_primary: str,
    plaid_detailed: str,
) -> tuple[str, str, float] | None:
    """
    Placeholder for future ML-based classification fallback.

    When Plaid PFC and legacy mapping both fail, this hook can:
    - Run an ML classifier on merchant name / amount / date patterns
    - Return (zapp_primary, zapp_subcategory, confidence_score)
    - Support user correction feedback loop for model retraining

    Returns None until implemented. Do not wire a model yet.
    """
    # TODO: Implement ML fallback classifier for unknown merchants
    # TODO: Add confidence scoring
    # TODO: Add user correction feedback loop for retraining
    return None


def _infer_recurring_subscription(
    transaction: BankTransaction,
) -> tuple[str, str] | None:
    """
    Placeholder for future recurring/subscription inference.
    Returns (zapp_primary, zapp_subcategory) if inferred as subscription, else None.
    """
    # TODO: Implement recurring charge detection, link to Subscription model
    return None


def categorize_bank_transaction(
    transaction: BankTransaction,
) -> dict[str, Any]:
    """
    Full categorization pipeline for a BankTransaction.

    Pipeline order:
    1. user_override_category (if present)
    2. merchant override rule (if matched)
    3. Plaid personal_finance_category mapping
    4. legacy Plaid category fallback
    5. ML fallback (placeholder)
    6. Miscellaneous / Unclassified

    Returns dict with zapp_primary_category, zapp_subcategory, category_source.
    Does NOT modify user_override_category. Raw Plaid fields are preserved.
    """
    # 1. User override takes precedence
    if transaction.user_override_category:
        return {
            "zapp_primary_category": "",
            "zapp_subcategory": "",
            "category_source": "user_override",
        }

    raw = transaction.raw_payload or {}
    display_name = transaction.merchant_name or transaction.name or ""
    normalized = normalize_merchant_name(display_name)

    # 2. Merchant override rules
    override = apply_merchant_override_rules(normalized)
    if override:
        prim, sub = override
        return {
            "zapp_primary_category": prim,
            "zapp_subcategory": sub,
            "category_source": "merchant_override",
        }

    # 3. Plaid personal_finance_category mapping
    pfc_primary, pfc_detailed = _extract_plaid_pfc(raw)
    pfc_result = map_plaid_pfc_to_zapp(pfc_primary, pfc_detailed)
    if pfc_result:
        prim, sub = pfc_result
        return {
            "zapp_primary_category": prim,
            "zapp_subcategory": sub,
            "category_source": "plaid_pfc",
        }

    # 4. Legacy Plaid category fallback
    categories_list = raw.get("category") or []
    categories_list = [c for c in categories_list if c] if isinstance(categories_list, list) else []
    category_primary = transaction.category_primary or ""
    category_detailed = transaction.category_detailed or ""
    prim, sub = map_plaid_legacy_to_zapp(
        category_primary,
        category_detailed,
        categories_list,
    )
    if prim and prim != ZappPrimaryCategory.MISCELLANEOUS:
        return {
            "zapp_primary_category": prim,
            "zapp_subcategory": sub,
            "category_source": "plaid_legacy",
        }

    # 5. ML fallback (placeholder - returns None until implemented)
    ml_result = classify_with_ml_fallback(transaction, pfc_primary, pfc_detailed)
    if ml_result:
        prim, sub, _confidence = ml_result
        return {
            "zapp_primary_category": prim,
            "zapp_subcategory": sub,
            "category_source": "ml_fallback",
        }

    # 6. Fallback to Miscellaneous
    return {
        "zapp_primary_category": ZappPrimaryCategory.MISCELLANEOUS,
        "zapp_subcategory": "",
        "category_source": "fallback",
    }


def resolve_effective_category(transaction: BankTransaction) -> str:
    """
    Centralized effective category resolution. Delegates to model property.
    """
    return transaction.effective_category


def run_categorization_on_transaction(transaction: BankTransaction) -> None:
    """
    Run categorization pipeline and persist results on BankTransaction.
    Skips if user_override_category is set.
    Raw Plaid fields (category_primary, category_detailed, raw_payload) are never modified.
    """
    if transaction.user_override_category:
        return
    result = categorize_bank_transaction(transaction)
    transaction.zapp_primary_category = result["zapp_primary_category"]
    transaction.zapp_subcategory = result["zapp_subcategory"]
    transaction.category_source = result["category_source"]
    transaction.save(update_fields=["zapp_primary_category", "zapp_subcategory", "category_source", "updated_at"])
