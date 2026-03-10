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
ANNUAL_BILLING_PROBABILITY = {
    "software": 0.40,
    "cloud_storage": 0.35,
    "education": 0.30,
    "default": 0.10,
}

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
ITEM_REC_THRESHOLDS = {"buy": 60, "wait": 35}

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
# Section 16  AI Conversation Agent parameters
# ---------------------------------------------------------------------------
CONVERSATION_LAMBDA_BASE = 2.0
CONVERSATION_LAMBDA_ENGAGED = 5.0
MESSAGE_COUNT_NU = 3.0
FACT_MIN_OBSERVATIONS = 3

# ---------------------------------------------------------------------------
# Merchant catalog for Merchant Agent (Section 9)
# ---------------------------------------------------------------------------
MERCHANT_CATALOG: list[dict] = [
    {"name": "Netflix", "category": "streaming", "domain": "netflix.com"},
    {"name": "Spotify", "category": "streaming", "domain": "spotify.com"},
    {"name": "Hulu", "category": "streaming", "domain": "hulu.com"},
    {"name": "Disney+", "category": "streaming", "domain": "disneyplus.com"},
    {"name": "YouTube Premium", "category": "streaming", "domain": "youtube.com"},
    {"name": "Apple Music", "category": "streaming", "domain": "apple.com"},
    {"name": "HBO Max", "category": "streaming", "domain": "max.com"},
    {"name": "Amazon Prime", "category": "software", "domain": "amazon.com"},
    {"name": "Apple iCloud", "category": "software", "domain": "apple.com/icloud"},
    {"name": "Google One", "category": "software", "domain": "one.google.com"},
    {"name": "Dropbox", "category": "software", "domain": "dropbox.com"},
    {"name": "Microsoft 365", "category": "software", "domain": "microsoft.com"},
    {"name": "Adobe Creative Cloud", "category": "software", "domain": "adobe.com"},
    {"name": "Planet Fitness", "category": "fitness", "domain": "planetfitness.com"},
    {"name": "LA Fitness", "category": "fitness", "domain": "lafitness.com"},
    {"name": "Peloton", "category": "fitness", "domain": "onepeloton.com"},
    {"name": "Xfinity", "category": "utilities", "domain": "xfinity.com"},
    {"name": "ComEd", "category": "utilities", "domain": "comed.com"},
    {"name": "AT&T", "category": "utilities", "domain": "att.com"},
    {"name": "Verizon", "category": "utilities", "domain": "verizon.com"},
    {"name": "T-Mobile", "category": "utilities", "domain": "t-mobile.com"},
    {"name": "DoorDash", "category": "food", "domain": "doordash.com"},
    {"name": "Uber Eats", "category": "food", "domain": "ubereats.com"},
    {"name": "Grubhub", "category": "food", "domain": "grubhub.com"},
    {"name": "Walmart", "category": "grocery", "domain": "walmart.com"},
    {"name": "Target", "category": "grocery", "domain": "target.com"},
    {"name": "Costco", "category": "grocery", "domain": "costco.com"},
    {"name": "Whole Foods", "category": "grocery", "domain": "wholefoodsmarket.com"},
    {"name": "Kroger", "category": "grocery", "domain": "kroger.com"},
    {"name": "Trader Joe's", "category": "grocery", "domain": "traderjoes.com"},
    {"name": "Coursera", "category": "education", "domain": "coursera.org"},
    {"name": "Udemy", "category": "education", "domain": "udemy.com"},
    {"name": "LinkedIn Learning", "category": "education", "domain": "linkedin.com"},
    {"name": "Starbucks", "category": "food", "domain": "starbucks.com"},
    {"name": "Chipotle", "category": "food", "domain": "chipotle.com"},
    {"name": "McDonald's", "category": "food", "domain": "mcdonalds.com"},
    {"name": "Amazon", "category": "other", "domain": "amazon.com"},
    {"name": "Walgreens", "category": "other", "domain": "walgreens.com"},
    {"name": "CVS Pharmacy", "category": "other", "domain": "cvs.com"},
    {"name": "Shell", "category": "other", "domain": "shell.com"},
    {"name": "Chevron", "category": "other", "domain": "chevron.com"},
    {"name": "Lyft", "category": "other", "domain": "lyft.com"},
    {"name": "Uber", "category": "other", "domain": "uber.com"},
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
