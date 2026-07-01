import logging

from rest_framework import serializers
from .models import Transaction, TransactionReflection

logger = logging.getLogger(__name__)


class TransactionSerializer(serializers.ModelSerializer):
    personal_value_score = serializers.IntegerField(read_only=True)
    value_score_confidence = serializers.FloatField(read_only=True)
    considered_at = serializers.DateTimeField(required=False, allow_null=True)

    # These fields are derived by backend services and must not be written by
    # clients.  Clients that accidentally send them will have the values ignored.
    #   - feedback_value_score / feedback_confidence: computed by
    #     transactions.services.feedback_scoring.apply_feedback_scoring after save.
    #   - impulse_score / regret_score: not produced in the live feedback flow;
    #     populated only by datagen / future behavioural inference.
    #   - self_report_researched: not collected in the live feedback form.
    feedback_value_score = serializers.FloatField(read_only=True)
    feedback_confidence = serializers.FloatField(read_only=True)
    impulse_score = serializers.FloatField(read_only=True)
    regret_score = serializers.FloatField(read_only=True)
    self_report_researched = serializers.BooleanField(read_only=True)

    class Meta:
        model = Transaction
        fields = "__all__"
        read_only_fields = (
            "user",
            "personal_value_score",
            "value_score_confidence",
            "value_score_model_version",
            "value_score_computed_at",
            # Backend-derived; see field declarations above.
            "feedback_value_score",
            "feedback_confidence",
            "impulse_score",
            "regret_score",
            "self_report_researched",
        )

    def validate_usage_frequency(self, value):
        """
        usage_frequency represents times-per-week of expected usage.
        Must be a positive integer (>=1) when supplied; 0 or negative values
        indicate a data-entry error or "unknown" and are coerced to None so the
        model pipeline can infer usage from transaction history instead.
        Maximum: 365 (once daily upper-bound sanity check).

        Note: EncryptedIntegerField stores values as text internally, so the
        value may arrive as a string from DRF's auto-serializer. Coerce to int.
        """
        if value is None:
            return value
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError("usage_frequency must be an integer.")
        if int_value <= 0:
            return None
        if int_value > 365:
            raise serializers.ValidationError(
                "usage_frequency cannot exceed 365 (max once-daily over a year)."
            )
        return int_value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        self._sanitize_considered_at(attrs)
        return attrs

    def _sanitize_considered_at(self, attrs: dict) -> None:
        """
        Sanitize considered_at against occurred_at.

        The field is meant to record when the user *started* considering the
        purchase (before it happened).  The live feedback form incorrectly sets
        it to the form-submission timestamp, which is always *after* occurred_at.

        Rule: if considered_at >= occurred_at we cannot trust it as a real
        pre-purchase consideration timestamp, so we discard it (set to None).
        This prevents the decision-time inference in state_inference from
        computing meaningless negative durations.
        """
        considered_at = attrs.get("considered_at")
        if considered_at is None:
            return

        # Resolve occurred_at: prefer the value being set now, fall back to
        # the existing instance value (e.g. on a PATCH of an existing txn).
        occurred_at = attrs.get("occurred_at")
        if occurred_at is None and self.instance is not None:
            occurred_at = getattr(self.instance, "occurred_at", None)

        if occurred_at is not None and considered_at >= occurred_at:
            logger.debug(
                "considered_at (%s) >= occurred_at (%s); discarding as "
                "likely feedback-submission timestamp.",
                considered_at,
                occurred_at,
            )
            attrs["considered_at"] = None


class TransactionReflectionSerializer(serializers.ModelSerializer):
    regret_score = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=100,
    )
    was_worth_it = serializers.BooleanField(required=False, allow_null=True)

    class Meta:
        model = TransactionReflection
        fields = "__all__"
        read_only_fields = ("user", "reflected_same_day")

    def validate_transaction(self, transaction):
        request = self.context.get("request")
        if request and transaction.user_id != request.user.id:
            raise serializers.ValidationError("You can only reflect on your own transactions.")
        return transaction

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        transaction = attrs.get("transaction") or getattr(self.instance, "transaction", None)
        if (
            request
            and transaction
            and self.instance is None
            and TransactionReflection.objects.filter(user=request.user, transaction=transaction).exists()
        ):
            raise serializers.ValidationError({"transaction": "A reflection already exists for this transaction."})
        return attrs
