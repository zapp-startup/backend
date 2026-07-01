from __future__ import annotations


def fallback_value_score_payload(*, reason: str) -> dict:
    return {
        "value_score": None,
        "base_value_score": None,
        "confidence": 0.0,
        "tier_used": "fallback",
        "inference_status": reason,
        "evidence_json": {"reason": reason},
    }
