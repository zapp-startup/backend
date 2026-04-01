from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.db import models
from django.utils.dateparse import parse_date, parse_datetime

from .data_encryption import decrypt_app_data, encrypt_app_data


class EncryptedValueMixin:
    description = "Encrypted application data"

    def get_internal_type(self) -> str:
        return "TextField"

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or value == "":
            return value
        if self.is_plain_value(value):
            return value
        plaintext, _was_encrypted = decrypt_app_data(str(value))
        return self.deserialize_plain_value(plaintext)

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        plaintext = self.serialize_plain_value(value)
        return encrypt_app_data(plaintext)

    def is_plain_value(self, value: Any) -> bool:
        return False

    def serialize_plain_value(self, value: Any) -> str:
        return str(value)

    def deserialize_plain_value(self, value: str) -> Any:
        return value


class EncryptedTextField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, str) and not value.startswith("gAAAA")


class EncryptedCharField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, str) and not value.startswith("gAAAA")


class EncryptedJSONField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, (dict, list))

    def serialize_plain_value(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))

    def deserialize_plain_value(self, value: str) -> Any:
        return json.loads(value)


class EncryptedDecimalField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, Decimal)

    def serialize_plain_value(self, value: Any) -> str:
        return str(Decimal(value))

    def deserialize_plain_value(self, value: str) -> Decimal:
        return Decimal(value)


class EncryptedIntegerField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def deserialize_plain_value(self, value: str) -> int:
        return int(value)


class EncryptedFloatField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, float)

    def deserialize_plain_value(self, value: str) -> float:
        return float(value)


class EncryptedBooleanField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, bool)

    def serialize_plain_value(self, value: Any) -> str:
        return "1" if bool(value) else "0"

    def deserialize_plain_value(self, value: str) -> bool:
        return value == "1"


class EncryptedDateField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, date) and not isinstance(value, datetime)

    def serialize_plain_value(self, value: Any) -> str:
        return value.isoformat()

    def deserialize_plain_value(self, value: str) -> date:
        parsed = parse_date(value)
        if parsed is None:
            raise ValueError(f"Invalid encrypted date value: {value}")
        return parsed


class EncryptedDateTimeField(EncryptedValueMixin, models.TextField):
    def is_plain_value(self, value: Any) -> bool:
        return isinstance(value, datetime)

    def serialize_plain_value(self, value: Any) -> str:
        return value.isoformat()

    def deserialize_plain_value(self, value: str) -> datetime:
        parsed = parse_datetime(value)
        if parsed is None:
            raise ValueError(f"Invalid encrypted datetime value: {value}")
        return parsed
