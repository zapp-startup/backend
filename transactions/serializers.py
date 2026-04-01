from rest_framework import serializers
from .models import Transaction, TransactionReflection


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = "__all__"
        read_only_fields = ("user",)


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
