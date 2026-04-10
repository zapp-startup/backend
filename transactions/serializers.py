from rest_framework import serializers
from .models import Transaction, TransactionReflection


class TransactionSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)

        feedback_value = instance.feedback_value_score
        feedback_confidence = instance.feedback_confidence

        if feedback_value is not None:
            data["value_score"] = max(0, min(150, round(float(feedback_value) * 150)))
            data["value_score_source"] = "feedback_value_score"
            data["value_score_confidence"] = (
                round(float(feedback_confidence), 3)
                if feedback_confidence is not None
                else None
            )
            return data

        satisfaction = instance.satisfaction_rating
        regret = instance.regret_score
        impulse = instance.impulse_score

        if satisfaction is None and regret is None and impulse is None:
            data["value_score"] = None
            data["value_score_source"] = None
            data["value_score_confidence"] = None
            return data

        satisfaction_01 = (
            max(0.0, min(1.0, float(satisfaction) / 10.0))
            if satisfaction is not None
            else 0.67
        )
        regret_01 = max(0.0, min(1.0, float(regret))) if regret is not None else 0.0
        impulse_01 = max(0.0, min(1.0, float(impulse))) if impulse is not None else 0.0

        heuristic_score_01 = max(
            0.0,
            min(1.0, satisfaction_01 + 0.33 - (0.45 * regret_01) - (0.35 * impulse_01)),
        )
        data["value_score"] = round(heuristic_score_01 * 150)
        data["value_score_source"] = "heuristic"
        signal_count = sum(value is not None for value in (satisfaction, regret, impulse))
        data["value_score_confidence"] = round(min(0.75, 0.3 + (0.15 * signal_count)), 3)
        return data

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
