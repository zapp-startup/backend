"""
Agent 3: Merchant Agent (Section 9)
Builds merchant catalog entries and generates realistic raw transaction strings
with probabilistic noise rendering.
Writes to: subscriptions_merchant, transactions_transaction.description_raw
"""

from __future__ import annotations

from datagen.agents.base import BaseAgent
from datagen.config import (
    MERCHANT_CATALOG,
    MERCHANT_CATEGORY_COMPAT,
    PROCESSOR_PREFIXES,
    compat_status_family,
)
from datagen.state import UserState


class MerchantAgent(BaseAgent):
    """Build merchant catalog and provide noisy description rendering."""

    def run(self, state: UserState, context: dict) -> dict:
        """Ensure merchant catalog is built and return it."""
        if "merchant_catalog" in context:
            return {}

        catalog = []
        for m in MERCHANT_CATALOG:
            catalog.append({
                "name": m["name"],
                "category": m["category"],
                "merchant_family": m.get("merchant_family", m.get("category", "other")),
                "domain": m.get("domain", ""),
                "eligibility": m.get("eligibility", "not_subscribable"),
                "yearly_billing_mode": m.get("yearly_billing_mode"),
            })

        return {"merchant_catalog": catalog}

    def render_description_raw(self, canonical_name: str, domain: str = "") -> str:
        """
        Section 9.5: Probabilistic rendering of a merchant name into
        a noisy raw transaction description.
        """
        rng = self.rng
        parts = [canonical_name]

        # Processor prefix (P=0.35)
        if rng.random() < 0.35:
            prefix = rng.choice(PROCESSOR_PREFIXES)
            parts = [prefix + canonical_name]

        result = parts[0]

        # Website suffix (P=0.40)
        if domain and rng.random() < 0.40:
            suffix = domain.upper().split("/")[0]
            if not result.upper().endswith(suffix):
                result = result + " " + suffix

        # Store number (P=0.25)
        if rng.random() < 0.25:
            store_num = int(rng.integers(100, 9999))
            result = result + f" #{store_num}"

        # Phone fragment (P=0.15)
        if rng.random() < 0.15:
            phone = f"{int(rng.integers(800,899))}{int(rng.integers(1000000,9999999))}"
            result = result + " " + phone

        # Uppercase (P=0.80)
        if rng.random() < 0.80:
            result = result.upper()

        # Truncation (P=0.30) to 18-25 chars
        if rng.random() < 0.30:
            max_len = int(rng.integers(18, 26))
            result = result[:max_len]

        return result.strip()

    def pick_merchant_for_category(
        self, category: str, catalog: list[dict], anomaly_mode: bool = False,
    ) -> dict | None:
        """Pick a random merchant from catalog matching a category and compatibility matrix."""
        rng = self.rng
        category_map = {
            "groceries": ["grocery"],
            "dining": ["food"],
            "transport": ["other"],
            "shopping": ["other"],
            "entertainment": ["streaming"],
            "health": ["other", "fitness"],
            "travel": ["other"],
            "utilities": ["utilities"],
            "subscriptions": ["streaming", "software", "fitness", "food", "education"],
            "fees": ["other"],
        }
        allowed_cats = category_map.get(category, ["other"])
        candidates = [m for m in catalog if m["category"] in allowed_cats]
        if not candidates:
            candidates = catalog

        def ok(m: dict) -> bool:
            mc = m.get("category", "other")
            status = MERCHANT_CATEGORY_COMPAT.get((mc, category), "allowed")
            if status == "allowed":
                return True
            if status == "rare":
                return rng.random() < 0.05
            if status == "disallowed":
                return anomaly_mode and rng.random() < 0.02
            return True

        matches = [m for m in candidates if ok(m)]
        if not matches:
            matches = candidates
        return dict(rng.choice(matches))

    def pick_merchant_for_family(
        self,
        merchant_family: str,
        spend_category: str,
        catalog: list[dict],
        *,
        anomaly_mode: bool = False,
    ) -> dict | None:
        """Pick merchant by family + spend_category; never pairs disallowed compat in normal mode."""
        rng = self.rng
        candidates = [m for m in catalog if m.get("merchant_family") == merchant_family]
        if not candidates:
            return None

        def ok(m: dict) -> bool:
            fam = m.get("merchant_family", merchant_family)
            status = compat_status_family(fam, spend_category)
            if status == "allowed":
                return True
            if status == "rare":
                return rng.random() < (0.06 if anomaly_mode else 0.012)
            if status == "disallowed":
                return anomaly_mode and rng.random() < 0.015
            return True

        matches = [m for m in candidates if ok(m)]
        if not matches:
            matches = candidates
        return dict(rng.choice(matches))
