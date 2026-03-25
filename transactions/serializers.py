from rest_framework import serializers
from .models import Transaction, TransactionReflection


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = "__all__"
        read_only_fields = ("user",)


class TransactionReflectionSerializer(serializers.ModelSerializer):
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

    def validate_regret_score(self, value):
        if value is None:
            return value
        if value < 0 or value > 100:
            raise serializers.ValidationError("regret_score must be between 0 and 100.")
        return value
