"""
Backend derivation of feedback-related scoring fields.

These fields are NOT collected directly from the live user-facing feedback form:
  - feedback_value_score   (0-1 composite from ratings)
  - feedback_confidence    (0-1 based on how many signals are present)
  - impulse_score          (not computed here – requires broader behavioural analysis)
  - regret_score           (not computed here – requires broader behavioural analysis)
  - self_report_researched (boolean, not collected in live feedback flow)

Only feedback_value_score and feedback_confidence are derived here, using a
deterministic, reproducible formula from the user-supplied ratings.
impulse_score, regret_score, and self_report_researched remain null unless
set by a separate process (e.g. datagen, future behavioural inference pass).

The formula is intentionally simple and auditable.  It is NOT a model; it is
a lightweight normalisation so the scoring model receives a meaningful signal
instead of a fabricated zero.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.transactions.models import Transaction


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def derive_feedback_fields(transaction: "Transaction") -> dict[str, float | None]:
    """
    Derive feedback_value_score and feedback_confidence from the satisfaction,
    regret, and repurchase ratings that ARE collected by the live feedback form.

    Returns a dict with keys:
      - feedback_value_score  (float 0-1, or None if insufficient data)
      - feedback_confidence   (float 0-1, or None if insufficient data)

    Intentionally keeps unknown as None rather than forcing 0, so the model's
    _safe_fill defaults take over rather than receiving a false zero.
    """
    sat = transaction.satisfaction_rating   # 1-10, nullable
    regret = transaction.regret_rating      # 0-100, nullable
    repurchase = transaction.repurchase_likelihood  # 0-100, nullable

    present = sum(x is not None for x in [sat, regret, repurchase])
    if present == 0:
        # No signal at all – return null so model uses its own defaults.
        return {"feedback_value_score": None, "feedback_confidence": None}

    # Normalise each component to [0, 1] where 1 = good value.
    sat_norm = (float(sat) / 10.0) if sat is not None else None
    regret_inv = (1.0 - float(regret) / 100.0) if regret is not None else None
    repurchase_norm = (float(repurchase) / 100.0) if repurchase is not None else None

    components = [c for c in [sat_norm, regret_inv, repurchase_norm] if c is not None]
    score = _clamp(sum(components) / len(components))

    # Confidence reflects how many of the three signals were provided.
    # 1/3 present → low, 2/3 → medium, 3/3 → full.
    confidence_map = {1: 0.4, 2: 0.7, 3: 1.0}
    confidence = confidence_map.get(present, 0.4)

    # If reflection_text is also present, give a small confidence boost.
    if transaction.reflection_text and transaction.reflection_text.strip():
        confidence = _clamp(confidence + 0.1)

    return {
        "feedback_value_score": score,
        "feedback_confidence": _clamp(confidence),
    }


def apply_feedback_scoring(transaction: "Transaction", *, save: bool = True) -> None:
    """
    Compute and persist feedback_value_score and feedback_confidence onto the
    transaction.  Safe to call on any Transaction; is a no-op when no ratings
    are present (leaves both fields as None).
    """
    derived = derive_feedback_fields(transaction)
    changed = False
    for field, value in derived.items():
        if getattr(transaction, field) != value:
            setattr(transaction, field, value)
            changed = True
    if save and changed:
        transaction.save(update_fields=list(derived.keys()))
