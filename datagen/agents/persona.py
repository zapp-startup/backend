"""
Agent 1: Persona Agent (Section 7)
Creates user identity, latent profile, and populates:
  - users_user
  - users_userrawexplicit
  - users_usercomputed
Initializes the full latent state Su.
"""

from __future__ import annotations

import random as _stdlib_random
from datetime import date
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    ARCHETYPE_EMPLOYMENT,
    ARCHETYPE_LIFE_STAGE,
    DIRICHLET_ALPHA,
    FIXED_EXPENSE_RATIO_BETA,
    FIXED_EXPENSE_RATIO_BY_HOUSING,
    FIXED_EXPENSE_RATIO_GLOBAL,
    HOUSEHOLD_POISSON_LAMBDA,
    HOUSING_INDEPENDENCE_PRIORS,
    INCOME_LOGNORMAL,
    INCOME_STABILITY_PARAMS,
    LIFE_STAGE_AGE_WEIGHTS,
    PRICE_SENSITIVITY_BETA,
    SPEND_CATEGORIES,
    SUBSCRIPTION_ENGAGEMENT_STATES,
    TRAIT_BETA_PARAMS,
)
from datagen.distributions import (
    sample_archetype,
    sample_beta,
    sample_dirichlet,
    sample_lognormal_decimal,
    sample_payday_params,
    sample_poisson,
)
from datagen.state import UserState

HOUSING_STATES = ["dependent", "shared", "independent", "homeowner"]
INCOME_STABILITY_STATES = ["stable", "variable", "fragile"]


def _age_bucket_key(age: int) -> str:
    if age < 25:
        return "18_24"
    if age < 35:
        return "25_34"
    if age < 45:
        return "35_44"
    return "45_plus"


def _sample_life_stage(rng, arch: str, age: int) -> str:
    if arch == "student":
        return "student"
    if arch == "retired":
        return "other"
    valid = list(ARCHETYPE_LIFE_STAGE[arch])
    bucket = _age_bucket_key(age)
    table = LIFE_STAGE_AGE_WEIGHTS.get(bucket, {}).get(arch)
    if table is None:
        return str(rng.choice(valid))
    # table order: early_career, mid_career, family, other
    stage_order = ["early_career", "mid_career", "family", "other"]
    weights = []
    stages = []
    for st, w in zip(stage_order, table):
        if st in valid:
            stages.append(st)
            weights.append(w)
    if not stages:
        return str(rng.choice(valid))
    s = sum(weights)
    p = [w / s for w in weights]
    picked = str(rng.choice(stages, p=p))
    if picked not in valid:
        return str(rng.choice(valid))
    return picked


class PersonaAgent(BaseAgent):
    """Build a coherent user profile and initialize latent state."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng

        arch = sample_archetype(rng)
        state.archetype = arch

        traits = TRAIT_BETA_PARAMS[arch]
        state.impulse = sample_beta(rng, *traits["impulse"])
        state.budget_adherence = sample_beta(rng, *traits["budget_adherence"])
        state.regret_sensitivity = sample_beta(rng, *traits["regret_sensitivity"])
        state.quality_preference = sample_beta(rng, *traits["quality_preference"])
        state.novelty_seeking = sample_beta(rng, *traits["novelty_seeking"])
        state.household_pressure = sample_beta(rng, *traits["household_pressure"])
        state.credit_stress = sample_beta(rng, *traits["credit_stress"])
        state.luxury_affinity = sample_beta(rng, *traits["luxury_affinity"])

        # Subscription engagement (Section 4.1)
        eng_weights = {
            "salary_biweekly": [0.2, 0.45, 0.25, 0.10],
            "salary_monthly":  [0.15, 0.40, 0.30, 0.15],
            "hourly_weekly":   [0.30, 0.40, 0.15, 0.15],
            "gig":             [0.25, 0.35, 0.20, 0.20],
            "student":         [0.15, 0.30, 0.35, 0.20],
            "retired":         [0.35, 0.40, 0.15, 0.10],
            "high_income":     [0.10, 0.25, 0.50, 0.15],
            "credit_constrained": [0.35, 0.30, 0.10, 0.25],
        }
        w = eng_weights[arch]
        state.subscription_engagement = SUBSCRIPTION_ENGAGEMENT_STATES[
            int(rng.choice(len(SUBSCRIPTION_ENGAGEMENT_STATES), p=w))
        ]

        # Section 7.6: Household size = 1 + Poisson(lambda)
        hs_raw = 1 + sample_poisson(rng, HOUSEHOLD_POISSON_LAMBDA[arch])
        state.household_size = max(1, min(hs_raw, 7))

        # Dependents: at most household_size - 1
        if arch == "student":
            state.dependents_count = 0
        else:
            max_dep = max(0, state.household_size - 1)
            state.dependents_count = int(rng.integers(0, max_dep + 1)) if max_dep > 0 else 0

        # Age range conditioned on archetype
        age_ranges = {
            "student": (18, 26),
            "retired": (58, 78),
            "high_income": (30, 55),
            "credit_constrained": (22, 50),
        }
        lo, hi = age_ranges.get(arch, (22, 55))
        state.age = int(rng.integers(lo, hi + 1))

        # Housing independence (affects rent / fixed ratio in obligation + persona)
        hw = HOUSING_INDEPENDENCE_PRIORS[arch]
        state.housing_independence_state = HOUSING_STATES[int(rng.choice(4, p=hw))]
        if arch == "student" and state.housing_independence_state == "homeowner" and rng.random() < 0.85:
            state.housing_independence_state = str(rng.choice(["dependent", "shared"]))

        life_stage = _sample_life_stage(rng, arch, state.age)
        if life_stage == "family" and state.household_size < 2:
            state.household_size = max(2, state.household_size + int(rng.integers(1, 3)))

        # Section 7.6: Monthly income ~ LogNormal (take-home monthly estimate)
        mu_i, sig_i = INCOME_LOGNORMAL[arch]
        state.monthly_income = sample_lognormal_decimal(rng, mu_i, sig_i)

        # Income stability + price sensitivity
        is_w = INCOME_STABILITY_PARAMS[arch]
        state.income_stability_state = INCOME_STABILITY_STATES[int(rng.choice(3, p=is_w))]
        ps_a, ps_b = PRICE_SENSITIVITY_BETA[arch]
        state.price_sensitivity = float(sample_beta(rng, ps_a, ps_b))
        income_signal = min(1.0, max(0.0, (float(state.monthly_income) - 2500.0) / 9000.0))
        quality_signal = max(0.0, min(1.0, state.quality_preference))
        novelty_signal = max(0.0, min(1.0, state.novelty_seeking))
        price_relief = max(0.0, min(1.0, 1.0 - state.price_sensitivity))
        aspiration = 0.0
        if state.credit_stress > 0.55 and float(state.monthly_income) < 5000:
            aspiration = float(rng.uniform(0.0, 0.08))
        luxury = (
            0.42 * state.luxury_affinity
            + 0.24 * quality_signal
            + 0.12 * novelty_signal
            + 0.16 * income_signal
            + 0.10 * price_relief
            + aspiration
            + float(rng.normal(0, 0.035))
        )
        state.luxury_affinity = max(0.0, min(1.0, luxury))

        # Debt carry from credit_stress trait
        cs = state.credit_stress
        if cs < 0.3:
            state.debt_carry_state = "none"
        elif cs < 0.55:
            state.debt_carry_state = "managed"
        elif cs < 0.75:
            state.debt_carry_state = "revolving"
        else:
            state.debt_carry_state = "stressed"

        # Fixed expense ratio: housing-conditioned band (primary), global sanity clamp
        lo_r, hi_r = FIXED_EXPENSE_RATIO_BY_HOUSING[state.housing_independence_state]
        fe_a, fe_b = FIXED_EXPENSE_RATIO_BETA[arch]
        ratio = sample_beta(rng, fe_a, fe_b)
        ratio = lo_r + ratio * (hi_r - lo_r)
        g_lo, g_hi = FIXED_EXPENSE_RATIO_GLOBAL
        if ratio < g_lo or ratio > g_hi:
            ratio = max(g_lo, min(g_hi, ratio))
        state.monthly_fixed_expenses = Decimal(str(
            round(float(state.monthly_income) * ratio, 2)
        ))

        # Section 7.5: fixed_expenses <= income (enforced by ratio cap)
        # Section 7.5: household_size >= dependents + 1 (enforced above)

        # Initial balance proxy: 1-3 months of discretionary income
        discretionary = float(state.monthly_income - state.monthly_fixed_expenses)
        state.balance_proxy = Decimal(str(
            round(max(0, discretionary * float(rng.uniform(1.0, 3.0))), 2)
        ))
        state.update_liquidity()

        # Section 5.3: Category budget shares via Dirichlet
        alpha = DIRICHLET_ALPHA[arch]
        shares = sample_dirichlet(rng, alpha)
        state.category_budgets = dict(zip(SPEND_CATEGORIES, shares))

        # Payday effect params
        eta, tau = sample_payday_params(rng, state.impulse)
        state.payday_eta = eta
        state.payday_tau = tau

        # Build age range string
        if state.age < 25:
            age_range_str = "18-24"
        elif state.age < 35:
            age_range_str = "25-34"
        elif state.age < 45:
            age_range_str = "35-44"
        else:
            age_range_str = "45+"

        # Income range string
        mi = float(state.monthly_income)
        annual = mi * 12
        if annual < 25000:
            income_range = "<25k"
        elif annual < 50000:
            income_range = "25k-50k"
        elif annual < 100000:
            income_range = "50k-100k"
        else:
            income_range = "100k+"

        # Map quality/cost preferences to risk tolerance and budget style
        if state.budget_adherence > 0.65:
            budget_style = "strict"
        elif state.quality_preference > 0.6:
            budget_style = "optimize_value"
        else:
            budget_style = "flexible"

        if state.credit_stress > 0.6:
            risk_tolerance = "low"
        elif state.quality_preference > 0.6 and state.credit_stress < 0.3:
            risk_tolerance = "high"
        else:
            risk_tolerance = "medium"

        # Financial goal conditioned on archetype
        goal_weights = {
            "salary_biweekly":    ["save_more", "invest", "control_subs"],
            "salary_monthly":     ["invest", "save_more", "control_subs"],
            "hourly_weekly":      ["save_more", "reduce_debt", "build_credit"],
            "gig":                ["save_more", "reduce_debt", "invest"],
            "student":            ["save_more", "build_credit", "reduce_debt"],
            "retired":            ["save_more", "invest", "control_subs"],
            "high_income":        ["invest", "save_more", "control_subs"],
            "credit_constrained": ["reduce_debt", "build_credit", "save_more"],
        }
        financial_goal = str(rng.choice(goal_weights[arch]))

        employment_type = str(rng.choice(ARCHETYPE_EMPLOYMENT[arch]))

        reference_year = context["end_date"].year
        dob = date(
            reference_year - state.age,
            int(rng.integers(1, 13)),
            int(rng.integers(1, 29)),
        )

        # Value priority sliders (0-100) conditioned on traits
        vp_cost = int(max(10, min(95, state.budget_adherence * 60 + (1 - state.quality_preference) * 40 + rng.normal(0, 8))))
        vp_quality = int(max(10, min(95, state.quality_preference * 70 + 20 + rng.normal(0, 8))))
        vp_sustainability = int(max(0, min(80, 30 + rng.normal(0, 15))))

        # Computed profile labels
        if state.impulse > 0.6:
            spending_personality = "Impulse Prone"
        elif state.budget_adherence > 0.65:
            spending_personality = "Value Hunter"
        elif state.luxury_affinity > 0.72 and float(state.monthly_income) > 5500 and state.credit_stress < 0.45:
            spending_personality = "Premium Seeker"
        elif state.quality_preference > 0.6:
            spending_personality = "Brand Loyalist"
        elif state.novelty_seeking > 0.6:
            spending_personality = "Convenience First"
        else:
            spending_personality = "Balanced"

        if state.impulse > 0.55:
            product_spending_style = "Impulsive"
        elif state.budget_adherence > 0.55:
            product_spending_style = "Planned"
        else:
            product_spending_style = "Mixed"

        eng_label_map = {
            "minimal": "Minimalist",
            "moderate": "Power-user",
            "heavy": "Over-subscribed",
            "churn_prone": "Churn-heavy",
        }
        subscription_behavior_type = eng_label_map.get(
            state.subscription_engagement, "Bundle-driven"
        )

        # Normalize weights
        total_vp = vp_cost + vp_quality + vp_sustainability
        if total_vp > 0:
            cost_w = round(vp_cost / total_vp, 4)
            quality_w = round(vp_quality / total_vp, 4)
            sustainability_w = round(vp_sustainability / total_vp, 4)
        else:
            cost_w = quality_w = sustainability_w = None

        return {
            "raw_explicit_data": {
                "dob": dob,
                "age_range": age_range_str,
                "household_size": state.household_size,
                "location_zip": f"{int(rng.integers(10000, 99999))}",
                "life_stage": life_stage,
                "employment_type": employment_type,
                "dependents_count": state.dependents_count,
                "income_range": income_range,
                "monthly_income": state.monthly_income,
                "monthly_fixed_expenses": state.monthly_fixed_expenses,
                "financial_goal": financial_goal,
                "risk_tolerance": risk_tolerance,
                "budget_style": budget_style,
                "value_priority_cost": vp_cost,
                "value_priority_quality": vp_quality,
                "value_priority_sustainability": vp_sustainability,
                "self_report_research_habit": int(max(0, min(100,
                    state.budget_adherence * 60 + state.quality_preference * 30 + rng.normal(0, 10)))),
            },
            "computed_data": {
                "spending_personality": spending_personality,
                "product_spending_style": product_spending_style,
                "subscription_behavior_type": subscription_behavior_type,
                "cost_weight": cost_w,
                "quality_weight": quality_w,
                "sustainability_weight": sustainability_w,
                "impulse_susceptibility_score": round(state.impulse, 3),
                "regret_sensitivity": round(state.regret_sensitivity, 3),
                "budget_adherence_score": round(state.budget_adherence, 3),
            },
        }
