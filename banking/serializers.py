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
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ExchangePublicTokenSerializer(serializers.Serializer):
    """Request body for exchanging public token. Accepts public_token or publicToken."""

    public_token = serializers.CharField(required=True, allow_blank=False)

    def to_internal_value(self, data):
        # Accept camelCase from frontend (Plaid Link may pass publicToken)
        data = dict(data) if data else {}
        if "publicToken" in data and "public_token" not in data:
            data["public_token"] = data["publicToken"]
        return super().to_internal_value(data)
