"""
All archetype priors, distribution parameters, and probabilistic defaults
from the rules document (Sections 4-5, 19).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Section 5.1  Archetype prior  P(a_u)
# ---------------------------------------------------------------------------
ARCHETYPES = [
    "salary_biweekly",
    "salary_monthly",
    "hourly_weekly",
    "gig",
    "student",
    "retired",
    "high_income",
    "credit_constrained",
]

ARCHETYPE_PRIOR = {
    "salary_biweekly": 0.30,
    "salary_monthly": 0.12,
    "hourly_weekly": 0.14,
    "gig": 0.10,
    "student": 0.08,
    "retired": 0.08,
    "high_income": 0.10,
    "credit_constrained": 0.08,
}

# ---------------------------------------------------------------------------
# Section 5.2  Latent trait Beta(alpha, beta) per archetype
# Keys: impulse, budget_adherence, regret_sensitivity, quality_preference,
#        novelty_seeking, household_pressure, credit_stress
# ---------------------------------------------------------------------------
TRAIT_BETA_PARAMS: dict[str, dict[str, tuple[float, float]]] = {
    "salary_biweekly": {
        "impulse": (2.0, 5.0),
        "budget_adherence": (5.0, 2.0),
        "regret_sensitivity": (3.0, 4.0),
        "quality_preference": (3.0, 3.0),
        "novelty_seeking": (2.0, 4.0),
        "household_pressure": (2.5, 4.0),
        "credit_stress": (2.0, 6.0),
    },
    "salary_monthly": {
        "impulse": (2.0, 5.0),
        "budget_adherence": (5.0, 2.5),
        "regret_sensitivity": (3.0, 3.5),
        "quality_preference": (3.5, 3.0),
        "novelty_seeking": (2.0, 4.0),
        "household_pressure": (3.0, 3.5),
        "credit_stress": (2.0, 5.5),
    },
    "hourly_weekly": {
        "impulse": (3.0, 4.0),
        "budget_adherence": (3.0, 3.5),
        "regret_sensitivity": (3.5, 3.0),
        "quality_preference": (2.5, 4.0),
        "novelty_seeking": (2.5, 3.5),
        "household_pressure": (3.5, 3.0),
        "credit_stress": (3.5, 3.5),
    },
    "gig": {
        "impulse": (3.5, 3.5),
        "budget_adherence": (2.5, 4.0),
        "regret_sensitivity": (3.0, 3.0),
        "quality_preference": (2.5, 3.5),
        "novelty_seeking": (4.0, 3.0),
        "household_pressure": (2.5, 4.0),
        "credit_stress": (3.5, 3.0),
    },
    "student": {
        "impulse": (3.5, 3.0),
        "budget_adherence": (2.5, 4.5),
        "regret_sensitivity": (3.5, 3.0),
        "quality_preference": (2.0, 4.0),
        "novelty_seeking": (5.0, 2.5),
        "household_pressure": (1.5, 5.0),
        "credit_stress": (3.0, 4.0),
    },
    "retired": {
        "impulse": (1.5, 5.5),
        "budget_adherence": (5.5, 2.0),
        "regret_sensitivity": (2.5, 4.0),
        "quality_preference": (4.0, 2.5),
        "novelty_seeking": (1.5, 5.0),
        "household_pressure": (2.0, 5.0),
        "credit_stress": (2.0, 5.0),
    },
    "high_income": {
        "impulse": (3.0, 3.0),
        "budget_adherence": (3.5, 3.0),
        "regret_sensitivity": (2.0, 5.0),
        "quality_preference": (6.0, 2.0),
        "novelty_seeking": (3.5, 3.0),
        "household_pressure": (2.0, 5.0),
        "credit_stress": (1.5, 6.0),
    },
    "credit_constrained": {
        "impulse": (4.0, 3.0),
        "budget_adherence": (2.0, 5.0),
        "regret_sensitivity": (4.5, 2.5),
        "quality_preference": (2.0, 5.0),
        "novelty_seeking": (2.5, 4.0),
        "household_pressure": (4.5, 2.5),
        "credit_stress": (5.5, 2.0),
    },
}

# ---------------------------------------------------------------------------
# Section 7.6  Income LogNormal(mu, sigma) per archetype  (monthly $)
# ---------------------------------------------------------------------------
INCOME_LOGNORMAL: dict[str, tuple[float, float]] = {
    "salary_biweekly": (8.3, 0.35),    # ~median $4000/mo
    "salary_monthly": (8.5, 0.40),     # ~median $5000/mo
    "hourly_weekly": (7.9, 0.45),      # ~median $2700/mo
    "gig": (7.8, 0.55),               # ~median $2400/mo, high variance
    "student": (7.0, 0.50),           # ~median $1100/mo
    "retired": (8.0, 0.40),           # ~median $3000/mo
    "high_income": (9.1, 0.35),       # ~median $9000/mo
    "credit_constrained": (7.6, 0.45), # ~median $2000/mo
}

# ---------------------------------------------------------------------------
# Section 7.6  Fixed expense ratio Beta(alpha, beta) per archetype
# ---------------------------------------------------------------------------
FIXED_EXPENSE_RATIO_BETA: dict[str, tuple[float, float]] = {
    "salary_biweekly": (4.0, 5.0),
    "salary_monthly": (4.5, 5.0),
    "hourly_weekly": (4.0, 4.0),
    "gig": (3.5, 4.5),
    "student": (3.0, 6.0),
    "retired": (4.0, 4.5),
    "high_income": (3.0, 6.0),
    "credit_constrained": (5.0, 3.5),
}

# ---------------------------------------------------------------------------
# Section 7.6  Household size Poisson lambda per archetype
# ---------------------------------------------------------------------------
HOUSEHOLD_POISSON_LAMBDA: dict[str, float] = {
    "salary_biweekly": 1.5,
    "salary_monthly": 1.8,
    "hourly_weekly": 1.2,
    "gig": 0.8,
    "student": 0.3,
    "retired": 0.8,
    "high_income": 2.0,
    "credit_constrained": 1.5,
}

# ---------------------------------------------------------------------------
# Section 5.3  Dirichlet alpha vectors per archetype for category budget shares
# Categories: rent, groceries, dining, transport, shopping, entertainment,
#             health, travel, utilities, subscriptions, fees
# ---------------------------------------------------------------------------
SPEND_CATEGORIES = [
    "rent", "groceries", "dining", "transport", "shopping",
    "entertainment", "health", "travel", "utilities", "subscriptions", "fees",
]

DIRICHLET_ALPHA: dict[str, list[float]] = {
    "salary_biweekly": [8.0, 5.0, 3.0, 3.0, 3.0, 2.0, 1.5, 1.0, 3.0, 2.0, 0.5],
    "salary_monthly":  [8.0, 4.5, 3.5, 3.0, 3.5, 2.5, 1.5, 1.5, 3.0, 2.5, 0.5],
    "hourly_weekly":   [7.0, 5.5, 3.0, 4.0, 2.5, 2.0, 1.0, 0.5, 3.0, 1.5, 0.5],
    "gig":             [6.0, 5.0, 4.0, 4.5, 2.5, 2.5, 1.0, 1.0, 2.5, 2.0, 0.5],
    "student":         [5.0, 4.0, 5.0, 2.5, 3.0, 4.0, 0.5, 1.0, 1.5, 3.0, 0.5],
    "retired":         [6.0, 6.0, 3.0, 2.0, 2.0, 2.0, 4.0, 2.5, 3.5, 1.5, 0.5],
    "high_income":     [5.0, 3.0, 5.0, 2.5, 5.0, 4.0, 2.0, 4.0, 2.0, 3.0, 0.5],
    "credit_constrained": [9.0, 6.0, 2.0, 3.0, 1.5, 1.0, 1.0, 0.3, 3.5, 1.5, 1.0],
}

# ---------------------------------------------------------------------------
# Section 5.4  Transaction count NegBin(r, p) per spend category
# ---------------------------------------------------------------------------
NEGBIN_PARAMS: dict[str, tuple[float, float]] = {
    "rent": (1.0, 0.50),
    "groceries": (4.0, 0.45),
    "dining": (3.0, 0.40),
    "transport": (5.0, 0.50),
    "shopping": (2.0, 0.35),
    "entertainment": (2.0, 0.40),
    "health": (1.0, 0.50),
    "travel": (0.5, 0.60),
    "utilities": (2.0, 0.55),
    "subscriptions": (3.0, 0.50),
    "fees": (0.5, 0.70),
}

# ---------------------------------------------------------------------------
# Section 5.5  Amount LogNormal(mu, sigma) per spend category
# ---------------------------------------------------------------------------
AMOUNT_LOGNORMAL: dict[str, tuple[float, float]] = {
    "rent": (7.2, 0.30),        # ~$1300
    "groceries": (3.8, 0.50),   # ~$45
    "dining": (3.2, 0.60),      # ~$25
    "transport": (2.8, 0.70),   # ~$16
    "shopping": (3.8, 0.80),    # ~$45
    "entertainment": (3.0, 0.65), # ~$20
    "health": (4.0, 0.90),      # ~$55
    "travel": (5.5, 0.80),      # ~$245
    "utilities": (4.5, 0.30),   # ~$90
    "subscriptions": (2.5, 0.50), # ~$12
    "fees": (3.0, 0.50),        # ~$20
}

# ---------------------------------------------------------------------------
# Section 5.6  Payday effect parameters
# ---------------------------------------------------------------------------
PAYDAY_ETA_RANGE = (0.3, 0.8)      # impulse-scaled boost
PAYDAY_TAU_RANGE = (3.0, 7.0)      # decay in days

# ---------------------------------------------------------------------------
# Section 5.7  Weekend multipliers
# ---------------------------------------------------------------------------
WEEKEND_MULTIPLIER = {
    4: 1.40,  # Friday
    5: 1.40,  # Saturday
    6: 1.15,  # Sunday
}

# ---------------------------------------------------------------------------
# Section 5.8  Seasonality multipliers  S_{c,m}  (month 1..12)
# ---------------------------------------------------------------------------
SEASONALITY: dict[str, list[float]] = {
    "rent":          [1.0]*12,
    "groceries":     [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.05, 1.10],
    "dining":        [0.90, 0.90, 0.95, 1.0, 1.0, 1.10, 1.15, 1.15, 1.0, 1.0, 1.05, 1.20],
    "transport":     [1.0]*12,
    "shopping":      [0.85, 0.85, 0.90, 0.95, 1.0, 1.0, 1.05, 1.00, 0.95, 1.0, 1.30, 1.40],
    "entertainment": [0.90, 0.90, 0.95, 1.0, 1.05, 1.15, 1.20, 1.15, 1.0, 1.0, 1.10, 1.15],
    "health":        [1.10, 1.05, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "travel":        [0.60, 0.60, 0.80, 0.90, 1.0, 1.40, 1.50, 1.40, 1.0, 0.80, 0.70, 1.30],
    "utilities":     [1.30, 1.25, 1.10, 0.95, 0.85, 0.90, 1.10, 1.15, 0.95, 0.90, 1.05, 1.25],
    "subscriptions": [1.0]*12,
    "fees":          [1.0]*12,
}

# ---------------------------------------------------------------------------
# Section 10.4-10.5  Subscription engagement parameters
# ---------------------------------------------------------------------------
SUBSCRIPTION_ENGAGEMENT_STATES = ["minimal", "moderate", "heavy", "churn_prone"]

SUBSCRIPTION_COUNT_LAMBDA: dict[str, tuple[float, float]] = {
    "minimal": (1.0, 3.0),
    "moderate": (3.0, 6.0),
    "heavy": (6.0, 12.0),
    "churn_prone": (3.0, 7.0),
}

SUBSCRIPTION_CATEGORIES = [
    "streaming", "music", "fitness", "software", "cloud_storage",
    "news", "food_delivery", "gaming", "education", "productivity",
]

SUBSCRIPTION_PRICE_LOGNORMAL: dict[str, tuple[float, float]] = {
    "streaming": (2.6, 0.30),     # ~$13
    "music": (2.3, 0.20),         # ~$10
    "fitness": (3.2, 0.35),       # ~$25
    "software": (2.8, 0.45),      # ~$16
    "cloud_storage": (1.8, 0.40), # ~$6
    "news": (2.3, 0.35),          # ~$10
    "food_delivery": (2.3, 0.25), # ~$10
    "gaming": (2.5, 0.30),        # ~$12
    "education": (3.0, 0.50),     # ~$20
    "productivity": (2.5, 0.40),  # ~$12
}

# ---------------------------------------------------------------------------
# Section 10.5  Subscription lifecycle probabilities
# ---------------------------------------------------------------------------
TRIAL_PROBABILITY = (0.10, 0.30)
PRICE_INCREASE_YEARLY = (0.08, 0.20)
CANCEL_BASE_GAMMA = 0.02
CANCEL_CHURN_GAMMA = 0.04
CANCEL_TIGHT_GAMMA = 0.03
REACTIVATION_PROBABILITY = (0.05, 0.20)
FAILED_CHARGE_PROBABILITY = (0.005, 0.02)

# ---------------------------------------------------------------------------
# Section 14  Anomaly probabilities (Section 19 defaults table)
# ---------------------------------------------------------------------------
ANOMALY_DUPLICATE_P = 0.002
ANOMALY_REFUND_P = (0.01, 0.03)
ANOMALY_REVERSAL_P = (0.001, 0.005)
ANOMALY_OVERDRAFT_MONTHLY_P = (0.05, 0.20)
ANOMALY_OVERDRAFT_AMOUNT = (20.0, 35.0)
ANOMALY_FRAUD_YEARLY_P = (0.001, 0.005)
ANOMALY_CATEGORY_MISLABEL_P = (0.03, 0.05)

# ---------------------------------------------------------------------------
# Section 8.5  Income Agent parameters
# ---------------------------------------------------------------------------
INCOME_NOISE_SIGMA = (0.005, 0.02)
INCOME_SHOCK_MONTHLY_P = (0.002, 0.02)
GIG_DEPOSITS_PER_WEEK_LAMBDA = 4.0

# ---------------------------------------------------------------------------
# Section 11.4  Obligation Agent parameters
# ---------------------------------------------------------------------------
RENT_RATIO_BETA = (3.5, 5.0)
RENT_RATIO_CAP = (0.15, 0.45)
UTILITY_AMOUNT_MEAN = (80.0, 150.0)
UTILITY_AMOUNT_STD = (15.0, 35.0)
INSURANCE_MONTHLY_P = 0.70
LATE_FEE_P = (0.002, 0.02)

# ---------------------------------------------------------------------------
# Section 12.5  Spend Agent logistic coefficients
# ---------------------------------------------------------------------------
SPEND_LOGISTIC = {
    "groceries":     {"b0": -0.5, "b_weekend": 0.2, "b_payday": 0.3, "b_liq": -0.4, "b_impulse": 0.1, "b_novelty": 0.05},
    "dining":        {"b0": -1.2, "b_weekend": 0.8, "b_payday": 0.5, "b_liq": -0.5, "b_impulse": 0.4, "b_novelty": 0.2},
    "transport":     {"b0": -0.3, "b_weekend": -0.3, "b_payday": 0.1, "b_liq": -0.2, "b_impulse": 0.0, "b_novelty": 0.0},
    "shopping":      {"b0": -1.8, "b_weekend": 0.4, "b_payday": 0.6, "b_liq": -0.6, "b_impulse": 0.7, "b_novelty": 0.5},
    "entertainment": {"b0": -1.5, "b_weekend": 0.7, "b_payday": 0.4, "b_liq": -0.4, "b_impulse": 0.3, "b_novelty": 0.3},
    "health":        {"b0": -2.5, "b_weekend": 0.0, "b_payday": 0.1, "b_liq": -0.2, "b_impulse": 0.0, "b_novelty": 0.0},
    "travel":        {"b0": -3.5, "b_weekend": 0.3, "b_payday": 0.2, "b_liq": -0.8, "b_impulse": 0.2, "b_novelty": 0.4},
}

# ---------------------------------------------------------------------------
# Section 13.5  Behavior Agent scoring coefficients
# ---------------------------------------------------------------------------
IMPULSE_COEFFICIENTS = {
    "a0": 0.15, "a_impulse": 0.30, "a_novelty": 0.10,
    "a_late_night": 0.15, "a_payday": 0.10, "a_budget": 0.20,
    "sigma": 0.08,
}

REGRET_COEFFICIENTS = {
    "t0": 0.10, "t_impulse": 0.25, "t_amount_ratio": 0.20,
    "t_need": 0.15, "t_satisfaction": 0.20, "sigma": 0.08,
}

RESEARCHED_COEFFICIENTS = {
    "d0": -0.5, "d_budget": 1.5, "d_quality": 0.8, "d_impulse": 1.2,
}

# ---------------------------------------------------------------------------
# Section 15.4  Valuation Agent weights
# ---------------------------------------------------------------------------
SUBSCRIPTION_VALUE_WEIGHTS = {
    "w_usage": 0.30, "w_fit": 0.25, "w_habit": 0.15,
    "w_friction": 0.15, "w_cost": 0.15,
}

ITEM_VALUE_WEIGHTS = {
    "l_quality_fit": 0.30, "l_price_fairness": 0.30,
    "l_need_fit": 0.25, "l_budget_strain": 0.15,
}

SUBSCRIPTION_REC_THRESHOLDS = {"keep": 0.10, "cancel": -0.10}
# Thresholds for 0-150 score range: underused <80, match 85-105, extremely useful >105
ITEM_REC_THRESHOLDS = {"buy": 105, "wait": 85}

# Valuation score bands: underused <80, match ~100, extremely useful >100 (up to 150)
VALUATION_SCORE_BANDS = {
    "underused_max": 80,
    "match_lo": 85,
    "match_hi": 105,
    "extremely_useful_min": 105,
    "extremely_useful_max": 150,
}

# ---------------------------------------------------------------------------
# Section 18  State transition: liquidity thresholds
# ---------------------------------------------------------------------------
LIQUIDITY_THRESHOLDS = {
    "comfortable": 1.5,
    "stable_upper": 1.5,
    "stable_lower": 0.5,
    "tight": 0.5,
}

# ---------------------------------------------------------------------------
# Subscription burden (monthlyized sub cost / monthly income)
# ---------------------------------------------------------------------------
SUBSCRIPTION_BURDEN_THRESHOLDS = {
    "light": 0.03,
    "normal": 0.08,
    "stretched": 0.15,
}

CANCEL_STRETCH_GAMMA = 0.025
CANCEL_OVERLOAD_GAMMA = 0.06

DEBT_WORSEN_MONTHS = 3
DEBT_RECOVER_MONTHS = 4

# ---------------------------------------------------------------------------
# Fixed expense ratio by housing (primary band); global [0.10, 0.80] is sanity clamp
# ---------------------------------------------------------------------------
FIXED_EXPENSE_RATIO_BY_HOUSING: dict[str, tuple[float, float]] = {
    "dependent": (0.10, 0.35),
    "shared": (0.25, 0.55),
    "independent": (0.35, 0.75),
    "homeowner": (0.45, 0.80),
}

FIXED_EXPENSE_RATIO_GLOBAL = (0.10, 0.80)

# Housing independence priors per archetype (weights for dependent, shared, independent, homeowner)
HOUSING_INDEPENDENCE_PRIORS: dict[str, list[float]] = {
    "salary_biweekly": [0.05, 0.10, 0.55, 0.30],
    "salary_monthly": [0.05, 0.10, 0.50, 0.35],
    "hourly_weekly": [0.10, 0.20, 0.55, 0.15],
    "gig": [0.15, 0.25, 0.50, 0.10],
    "student": [0.45, 0.40, 0.12, 0.03],
    "retired": [0.08, 0.12, 0.40, 0.40],
    "high_income": [0.02, 0.05, 0.38, 0.55],
    "credit_constrained": [0.20, 0.30, 0.45, 0.05],
}

# Income stability categorical weights per archetype: stable, variable, fragile
INCOME_STABILITY_PARAMS: dict[str, list[float]] = {
    "salary_biweekly": [0.65, 0.25, 0.10],
    "salary_monthly": [0.70, 0.22, 0.08],
    "hourly_weekly": [0.45, 0.40, 0.15],
    "gig": [0.15, 0.45, 0.40],
    "student": [0.45, 0.40, 0.15],
    "retired": [0.55, 0.30, 0.15],
    "high_income": [0.60, 0.30, 0.10],
    "credit_constrained": [0.20, 0.35, 0.45],
}

# Price sensitivity Beta (alpha, beta) per archetype
PRICE_SENSITIVITY_BETA: dict[str, tuple[float, float]] = {
    "salary_biweekly": (3.0, 2.5),
    "salary_monthly": (3.0, 2.5),
    "hourly_weekly": (2.5, 3.0),
    "gig": (2.5, 3.0),
    "student": (4.0, 2.0),
    "retired": (3.5, 2.5),
    "high_income": (2.0, 4.0),
    "credit_constrained": (5.0, 2.0),
}

# Age bucket -> life-stage weights [early_career, mid_career, family, other] per archetype
LIFE_STAGE_AGE_WEIGHTS: dict[str, dict[str, list[float]]] = {
    "18_24": {
        "salary_biweekly": [0.55, 0.30, 0.10, 0.05],
        "salary_monthly": [0.50, 0.35, 0.10, 0.05],
        "hourly_weekly": [0.60, 0.30, 0.08, 0.02],
        "gig": [0.55, 0.35, 0.08, 0.02],
        "credit_constrained": [0.50, 0.35, 0.12, 0.03],
        "high_income": [0.40, 0.45, 0.12, 0.03],
    },
    "25_34": {
        "salary_biweekly": [0.35, 0.40, 0.15, 0.10],
        "salary_monthly": [0.25, 0.45, 0.20, 0.10],
        "hourly_weekly": [0.40, 0.45, 0.10, 0.05],
        "gig": [0.35, 0.45, 0.12, 0.08],
        "credit_constrained": [0.35, 0.40, 0.15, 0.10],
        "high_income": [0.20, 0.45, 0.25, 0.10],
    },
    "35_44": {
        "salary_biweekly": [0.10, 0.45, 0.35, 0.10],
        "salary_monthly": [0.08, 0.40, 0.40, 0.12],
        "hourly_weekly": [0.15, 0.50, 0.25, 0.10],
        "gig": [0.12, 0.45, 0.30, 0.13],
        "credit_constrained": [0.12, 0.45, 0.30, 0.13],
        "high_income": [0.05, 0.35, 0.45, 0.15],
    },
    "45_plus": {
        "salary_biweekly": [0.02, 0.35, 0.45, 0.18],
        "salary_monthly": [0.02, 0.30, 0.48, 0.20],
        "hourly_weekly": [0.05, 0.40, 0.40, 0.15],
        "gig": [0.05, 0.40, 0.40, 0.15],
        "credit_constrained": [0.05, 0.40, 0.40, 0.15],
        "high_income": [0.02, 0.30, 0.48, 0.20],
    },
}

# ---------------------------------------------------------------------------
# Merchant catalog category x spend_category compatibility (allowed | rare | disallowed)
# spend_category uses internal spend keys (groceries, dining, ...) matching SpendAgent
# ---------------------------------------------------------------------------
def _mc(m: str, s: str) -> tuple[str, str]:
    return (m, s)


MERCHANT_CATEGORY_COMPAT: dict[tuple[str, str], str] = {
    _mc("streaming", "groceries"): "disallowed",
    _mc("streaming", "dining"): "rare",
    _mc("streaming", "entertainment"): "allowed",
    _mc("streaming", "subscriptions"): "allowed",
    _mc("streaming", "shopping"): "rare",
    _mc("software", "subscriptions"): "allowed",
    _mc("software", "shopping"): "allowed",
    _mc("software", "groceries"): "disallowed",
    _mc("fitness", "health"): "allowed",
    _mc("fitness", "groceries"): "disallowed",
    _mc("utilities", "utilities"): "allowed",
    _mc("utilities", "subscriptions"): "allowed",
    _mc("utilities", "groceries"): "disallowed",
    _mc("food", "dining"): "allowed",
    _mc("food", "groceries"): "disallowed",
    _mc("grocery", "groceries"): "allowed",
    _mc("grocery", "dining"): "rare",
    _mc("grocery", "health"): "disallowed",
    _mc("education", "education"): "allowed",
    _mc("education", "health"): "disallowed",
    _mc("other", "shopping"): "allowed",
    _mc("other", "transport"): "allowed",
    _mc("other", "groceries"): "rare",
}

# ---------------------------------------------------------------------------
# Merchant family x internal spend_category (SpendAgent keys) compatibility
# Primary guard for intent-first generation; overrides loose (mc, sc) pairs.
# ---------------------------------------------------------------------------
def _fs(f: str, s: str) -> tuple[str, str]:
    return (f, s)


FAMILY_SPEND_COMPAT: dict[tuple[str, str], str] = {
    _fs("pharmacy", "groceries"): "rare",
    _fs("pharmacy", "health"): "allowed",
    _fs("pharmacy", "transport"): "disallowed",
    _fs("pharmacy", "shopping"): "rare",
    _fs("pharmacy", "dining"): "disallowed",
    _fs("fuel", "transport"): "allowed",
    _fs("fuel", "shopping"): "disallowed",
    _fs("fuel", "groceries"): "disallowed",
    _fs("rideshare", "transport"): "allowed",
    _fs("rideshare", "shopping"): "disallowed",
    _fs("rideshare", "groceries"): "disallowed",
    _fs("ecommerce", "shopping"): "allowed",
    _fs("ecommerce", "groceries"): "rare",
    _fs("ecommerce", "transport"): "disallowed",
    _fs("grocery_retail", "groceries"): "allowed",
    _fs("grocery_retail", "dining"): "rare",
    _fs("grocery_retail", "shopping"): "rare",
    _fs("food_quick", "dining"): "allowed",
    _fs("delivery_membership", "dining"): "allowed",
    _fs("delivery_membership", "groceries"): "disallowed",
    _fs("retail_big_box", "shopping"): "allowed",
    _fs("retail_big_box", "groceries"): "rare",
    _fs("streaming", "entertainment"): "allowed",
    _fs("streaming", "subscriptions"): "allowed",
    _fs("software", "subscriptions"): "allowed",
    _fs("software", "shopping"): "allowed",
    _fs("fitness", "health"): "allowed",
    _fs("utilities", "utilities"): "allowed",
    _fs("telecom", "utilities"): "allowed",
    _fs("food", "dining"): "allowed",
    _fs("education", "education"): "allowed",
}


def compat_status_family(merchant_family: str, spend_category: str) -> str:
    """Compatibility for intent-first spend: allowed | rare | disallowed."""
    return FAMILY_SPEND_COMPAT.get((merchant_family, spend_category), "allowed")


# When repair fixes a row, map merchant_family -> canonical internal spend_category
MERCHANT_FAMILY_DEFAULT_SPEND_CATEGORY: dict[str, str] = {
    "pharmacy": "health",
    "fuel": "transport",
    "rideshare": "transport",
    "ecommerce": "shopping",
    "grocery_retail": "groceries",
    "delivery_membership": "dining",
    "food_quick": "dining",
    "retail_big_box": "shopping",
    "utilities": "utilities",
    "telecom": "utilities",
    "streaming": "entertainment",
    "software": "shopping",
    "fitness": "health",
    "education": "education",
    "food": "dining",
}

# DB TransactionCategory value strings -> SpendAgent internal keys
TXN_CATEGORY_TO_SPEND: dict[str, str] = {
    "groceries": "groceries",
    "eating_out": "dining",
    "transport": "transport",
    "shopping": "shopping",
    "entertainment": "entertainment",
    "health": "health",
    "education": "education",
    "subscriptions": "subscriptions",
    "bills": "utilities",
    "other": "travel",
}


# LogNormal-ish (mu, sigma) for amount sampling after merchant family is chosen
AMOUNT_PRIORS_BY_FAMILY: dict[str, tuple[float, float]] = {
    "rideshare": (2.7, 0.45),
    "fuel": (3.1, 0.35),
    "pharmacy": (3.5, 0.45),
    "grocery_retail": (3.9, 0.42),
    "ecommerce": (3.5, 0.55),
    "food_quick": (3.0, 0.50),
    "delivery_membership": (3.2, 0.45),
    "retail_big_box": (3.6, 0.48),
    "streaming": (2.6, 0.28),
    "software": (2.7, 0.35),
    "fitness": (3.1, 0.38),
    "utilities": (4.2, 0.25),
    "telecom": (4.3, 0.28),
    "food": (2.8, 0.35),
    "education": (3.0, 0.40),
}

# Merchant-specific subscription monthly price tiers (weights sum ~1); small noise applied in agent
SUBSCRIPTION_MERCHANT_PRICE_TIERS: dict[str, list[tuple[str, float]]] = {
    "Netflix": [("15.49", 0.45), ("24.99", 0.40), ("6.99", 0.15)],
    "Spotify": [("10.99", 0.55), ("16.99", 0.45)],
    "Hulu": [("7.99", 0.35), ("17.99", 0.65)],
    "Disney+": [("13.99", 0.6), ("19.99", 0.4)],
    "YouTube Premium": [("13.99", 0.7), ("22.99", 0.3)],
    "Apple Music": [("10.99", 0.6), ("16.99", 0.4)],
    "HBO Max": [("15.99", 0.5), ("19.99", 0.5)],
    "Amazon Prime": [("14.99", 0.85), ("7.49", 0.15)],
    "Apple iCloud": [("0.99", 0.2), ("2.99", 0.35), ("9.99", 0.45)],
    "Google One": [("1.99", 0.25), ("2.99", 0.35), ("9.99", 0.40)],
    "Dropbox": [("11.99", 0.5), ("19.99", 0.5)],
    "Microsoft 365": [("6.99", 0.3), ("9.99", 0.45), ("12.99", 0.25)],
    "Adobe Creative Cloud": [("54.99", 0.7), ("79.49", 0.3)],
    "Planet Fitness": [("10.00", 0.4), ("24.99", 0.6)],
    "LA Fitness": [("34.99", 0.6), ("44.99", 0.4)],
    "Peloton": [("44.00", 0.5), ("12.99", 0.5)],
    "Xfinity": [("89.99", 0.4), ("119.99", 0.35), ("149.99", 0.25)],
    "ComEd": [("95.00", 0.5), ("120.00", 0.5)],
    "AT&T": [("75.00", 0.4), ("85.00", 0.35), ("95.00", 0.25)],
    "Verizon": [("80.00", 0.4), ("90.00", 0.35), ("100.00", 0.25)],
    "T-Mobile": [("70.00", 0.45), ("85.00", 0.35), ("95.00", 0.2)],
    "DoorDash": [("9.99", 0.85), ("4.99", 0.15)],
    "Uber Eats": [("9.99", 0.8), ("4.99", 0.2)],
    "Grubhub": [("9.99", 0.9), ("4.99", 0.1)],
    "Coursera": [("49.00", 0.5), ("59.00", 0.5)],
    "Udemy": [("20.00", 0.6), ("29.99", 0.4)],
    "LinkedIn Learning": [("39.99", 0.7), ("29.99", 0.3)],
}

# Yearly billing: forbidden (utilities, food memberships), rare telecom, normal streaming/software
ANNUAL_BILLING_PROBABILITY = {
    "software": 0.22,
    "cloud_storage": 0.18,
    "education": 0.12,
    "streaming": 0.20,
    "music": 0.15,
    "fitness": 0.12,
    "gaming": 0.10,
    "food_delivery": 0.02,
    "productivity": 0.12,
    "news": 0.08,
    "default": 0.08,
}

# ---------------------------------------------------------------------------
# Section 16  AI Conversation Agent parameters
# ---------------------------------------------------------------------------
CONVERSATION_LAMBDA_BASE = 2.0
CONVERSATION_LAMBDA_ENGAGED = 5.0
MESSAGE_COUNT_NU = 3.0
FACT_MIN_OBSERVATIONS = 3

# ---------------------------------------------------------------------------
# Merchant catalog for Merchant Agent (Section 9)
# ---------------------------------------------------------------------------
# eligibility: not_subscribable | membership | standard_subscription | utility_recurring | insurance_recurring
# merchant_family: intent-first routing (SpendAgent); yearly_billing_mode: subscription yearly eligibility
MERCHANT_CATALOG: list[dict] = [
    {"name": "Netflix", "category": "streaming", "merchant_family": "streaming", "domain": "netflix.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Spotify", "category": "streaming", "merchant_family": "streaming", "domain": "spotify.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Hulu", "category": "streaming", "merchant_family": "streaming", "domain": "hulu.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Disney+", "category": "streaming", "merchant_family": "streaming", "domain": "disneyplus.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "YouTube Premium", "category": "streaming", "merchant_family": "streaming", "domain": "youtube.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Apple Music", "category": "streaming", "merchant_family": "streaming", "domain": "apple.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "HBO Max", "category": "streaming", "merchant_family": "streaming", "domain": "max.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Amazon Prime", "category": "software", "merchant_family": "software", "domain": "amazon.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Apple iCloud", "category": "software", "merchant_family": "software", "domain": "apple.com/icloud", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Google One", "category": "software", "merchant_family": "software", "domain": "one.google.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Dropbox", "category": "software", "merchant_family": "software", "domain": "dropbox.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Microsoft 365", "category": "software", "merchant_family": "software", "domain": "microsoft.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Adobe Creative Cloud", "category": "software", "merchant_family": "software", "domain": "adobe.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Planet Fitness", "category": "fitness", "merchant_family": "fitness", "domain": "planetfitness.com", "eligibility": "membership", "yearly_billing_mode": "normal"},
    {"name": "LA Fitness", "category": "fitness", "merchant_family": "fitness", "domain": "lafitness.com", "eligibility": "membership", "yearly_billing_mode": "normal"},
    {"name": "Peloton", "category": "fitness", "merchant_family": "fitness", "domain": "onepeloton.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Xfinity", "category": "utilities", "merchant_family": "utilities", "domain": "xfinity.com", "eligibility": "utility_recurring", "yearly_billing_mode": "forbidden"},
    {"name": "ComEd", "category": "utilities", "merchant_family": "utilities", "domain": "comed.com", "eligibility": "utility_recurring", "yearly_billing_mode": "forbidden"},
    {"name": "AT&T", "category": "utilities", "merchant_family": "telecom", "domain": "att.com", "eligibility": "utility_recurring", "yearly_billing_mode": "low"},
    {"name": "Verizon", "category": "utilities", "merchant_family": "telecom", "domain": "verizon.com", "eligibility": "utility_recurring", "yearly_billing_mode": "low"},
    {"name": "T-Mobile", "category": "utilities", "merchant_family": "telecom", "domain": "t-mobile.com", "eligibility": "utility_recurring", "yearly_billing_mode": "low"},
    {"name": "DoorDash", "category": "food", "merchant_family": "delivery_membership", "domain": "doordash.com", "eligibility": "membership", "yearly_billing_mode": "forbidden"},
    {"name": "Uber Eats", "category": "food", "merchant_family": "delivery_membership", "domain": "ubereats.com", "eligibility": "membership", "yearly_billing_mode": "forbidden"},
    {"name": "Grubhub", "category": "food", "merchant_family": "delivery_membership", "domain": "grubhub.com", "eligibility": "membership", "yearly_billing_mode": "forbidden"},
    {"name": "Walmart", "category": "grocery", "merchant_family": "grocery_retail", "domain": "walmart.com", "eligibility": "not_subscribable"},
    {"name": "Target", "category": "grocery", "merchant_family": "retail_big_box", "domain": "target.com", "eligibility": "not_subscribable"},
    {"name": "Costco", "category": "grocery", "merchant_family": "grocery_retail", "domain": "costco.com", "eligibility": "membership"},
    {"name": "Whole Foods", "category": "grocery", "merchant_family": "grocery_retail", "domain": "wholefoodsmarket.com", "eligibility": "not_subscribable"},
    {"name": "Kroger", "category": "grocery", "merchant_family": "grocery_retail", "domain": "kroger.com", "eligibility": "not_subscribable"},
    {"name": "Trader Joe's", "category": "grocery", "merchant_family": "grocery_retail", "domain": "traderjoes.com", "eligibility": "not_subscribable"},
    {"name": "Coursera", "category": "education", "merchant_family": "education", "domain": "coursera.org", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Udemy", "category": "education", "merchant_family": "education", "domain": "udemy.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "LinkedIn Learning", "category": "education", "merchant_family": "education", "domain": "linkedin.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"},
    {"name": "Starbucks", "category": "food", "merchant_family": "food_quick", "domain": "starbucks.com", "eligibility": "not_subscribable"},
    {"name": "Chipotle", "category": "food", "merchant_family": "food_quick", "domain": "chipotle.com", "eligibility": "not_subscribable"},
    {"name": "McDonald's", "category": "food", "merchant_family": "food_quick", "domain": "mcdonalds.com", "eligibility": "not_subscribable"},
    {"name": "Amazon", "category": "other", "merchant_family": "ecommerce", "domain": "amazon.com", "eligibility": "not_subscribable"},
    {"name": "Walgreens", "category": "other", "merchant_family": "pharmacy", "domain": "walgreens.com", "eligibility": "not_subscribable"},
    {"name": "CVS Pharmacy", "category": "other", "merchant_family": "pharmacy", "domain": "cvs.com", "eligibility": "not_subscribable"},
    {"name": "Shell", "category": "other", "merchant_family": "fuel", "domain": "shell.com", "eligibility": "not_subscribable"},
    {"name": "Chevron", "category": "other", "merchant_family": "fuel", "domain": "chevron.com", "eligibility": "not_subscribable"},
    {"name": "Lyft", "category": "other", "merchant_family": "rideshare", "domain": "lyft.com", "eligibility": "not_subscribable"},
    {"name": "Uber", "category": "other", "merchant_family": "rideshare", "domain": "uber.com", "eligibility": "not_subscribable"},
]

# Processor prefixes for noisy merchant rendering (Section 9.5)
PROCESSOR_PREFIXES = [
    "PAYPAL *", "SQ *", "TST*", "STRIPE*", "VENMO *",
    "GOOGLE *", "APPLE.COM/BILL ", "CKO*",
]

# Employment types mapped from archetypes
ARCHETYPE_EMPLOYMENT: dict[str, list[str]] = {
    "salary_biweekly": ["full_time"],
    "salary_monthly": ["full_time"],
    "hourly_weekly": ["part_time", "full_time"],
    "gig": ["contract"],
    "student": ["student", "part_time"],
    "retired": ["unemployed"],
    "high_income": ["full_time"],
    "credit_constrained": ["part_time", "full_time", "contract"],
}

ARCHETYPE_LIFE_STAGE: dict[str, list[str]] = {
    "salary_biweekly": ["early_career", "mid_career", "family"],
    "salary_monthly": ["mid_career", "family"],
    "hourly_weekly": ["early_career", "mid_career"],
    "gig": ["early_career", "mid_career"],
    "student": ["student"],
    "retired": ["other"],
    "high_income": ["mid_career", "family"],
    "credit_constrained": ["early_career", "mid_career", "family"],
}
