from rest_framework import serializers

from .models import BankAccount, BankConnection, BankTransaction


class BankConnectionSerializer(serializers.ModelSerializer):
    """Serializer for BankConnection. Excludes sensitive plaid_access_token."""

    class Meta:
        model = BankConnection
        fields = [
            "id",
            "plaid_item_id",
            "institution_id",
            "institution_name",
            "status",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BankAccountSerializer(serializers.ModelSerializer):
    """Serializer for BankAccount."""

    connection = BankConnectionSerializer(read_only=True)

    class Meta:
        model = BankAccount
        fields = [
            "id",
            "connection",
            "plaid_account_id",
            "name",
            "official_name",
            "mask",
            "type",
            "subtype",
            "current_balance",
            "available_balance",
            "iso_currency_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BankAccountListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for account listing (no nested connection)."""

    class Meta:
        model = BankAccount
        fields = [
            "id",
            "plaid_account_id",
            "name",
            "official_name",
            "mask",
            "type",
            "subtype",
            "current_balance",
            "available_balance",
            "iso_currency_code",
        ]


class BankTransactionSerializer(serializers.ModelSerializer):
    """Serializer for BankTransaction."""

    account = BankAccountListSerializer(read_only=True)
    effective_category = serializers.ReadOnlyField()
    personal_value_score = serializers.IntegerField(source="feedback_transaction.personal_value_score", read_only=True)
    value_score_confidence = serializers.FloatField(source="feedback_transaction.value_score_confidence", read_only=True)
    value_score_model_version = serializers.CharField(source="feedback_transaction.value_score_model_version", read_only=True)
    value_score_computed_at = serializers.DateTimeField(source="feedback_transaction.value_score_computed_at", read_only=True)

    class Meta:
        model = BankTransaction
        fields = [
            "id",
            "account",
            "plaid_transaction_id",
            "name",
            "merchant_name",
            "amount",
            "iso_currency_code",
            "date",
            "authorized_date",
            "pending",
            "removed",
            "category_primary",
            "category_detailed",
            "zapp_primary_category",
            "zapp_subcategory",
            "user_override_category",
            "effective_category",
            "personal_value_score",
            "value_score_confidence",
            "value_score_model_version",
            "value_score_computed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BankTransactionFeedbackSerializer(serializers.Serializer):
    """
    Write-only serializer for bank transaction feedback.

    Accepts the same fields the live feedback form collects and stores them on
    the associated feedback Transaction (not on BankTransaction itself, which is
    a read-only Plaid-synced record).

    Fields NOT collected by the live form (impulse_score, regret_score,
    feedback_value_score, feedback_confidence, self_report_researched) are
    intentionally absent; they are derived or left null by backend services.
    """

    satisfaction_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=10
    )
    regret_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=100
    )
    repurchase_likelihood = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=100
    )
    usage_frequency = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="Expected times-per-week of usage. Must be >= 1 when provided.",
    )
    reflection_text = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=2000
    )
    # considered_at is accepted but validated server-side; see
    # TransactionSerializer._sanitize_considered_at for the rule.
    considered_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_usage_frequency(self, value):
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
                "usage_frequency cannot exceed 365."
            )
        return int_value


class ExchangePublicTokenSerializer(serializers.Serializer):
    """Request body for exchanging public token. Accepts public_token or publicToken."""

    public_token = serializers.CharField(required=True, allow_blank=False)

    def to_internal_value(self, data):
        # Accept camelCase from frontend (Plaid Link may pass publicToken)
        data = dict(data) if data else {}
        if "publicToken" in data and "public_token" not in data:
            data["public_token"] = data["publicToken"]
        return super().to_internal_value(data)
