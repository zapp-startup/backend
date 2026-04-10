from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FeatureBundle:
    data: dict[str, Any]
    entity_id: int
