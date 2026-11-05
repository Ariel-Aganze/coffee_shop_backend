from decimal import Decimal

from rest_framework import serializers

from .models import Payment, PaymentMethod


class PaymentCreateSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=PaymentMethod.choices)
    amount = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=Decimal("0.0001"))
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class PaymentsBulkCreateSerializer(serializers.Serializer):
    payments = PaymentCreateSerializer(many=True, allow_empty=False)


class RefundCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
        required=False, allow_null=True, default=None,
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")