from decimal import Decimal

from rest_framework import serializers

from .models import CashMovement, CashMovementType, CashSession


class CashMovementSerializer(serializers.ModelSerializer):
    performed_by_display = serializers.CharField(
        source="performed_by.username", read_only=True
    )
    movement_type_display = serializers.CharField(
        source="get_movement_type_display", read_only=True
    )

    class Meta:
        model = CashMovement
        fields = (
            "id",
            "session",
            "movement_type",
            "movement_type_display",
            "amount",
            "reason",
            "reference",
            "payment",
            "performed_by",
            "performed_by_display",
            "created_at",
        )
        read_only_fields = fields


class CashSessionSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    cashier_display = serializers.CharField(source="cashier.username", read_only=True)
    movements = CashMovementSerializer(many=True, read_only=True)
    live_expected_cash = serializers.SerializerMethodField()

    class Meta:
        model = CashSession
        fields = (
            "id",
            "branch",
            "branch_name",
            "cashier",
            "cashier_display",
            "status",
            "opening_balance",
            "opened_at",
            "expected_cash",
            "actual_cash",
            "difference",
            "discrepancy_explanation",
            "closed_at",
            "closed_by",
            "currency_code",
            "currency_decimal_places",
            "created_at",
            "live_expected_cash",
            "movements",
        )
        read_only_fields = fields

    def get_live_expected_cash(self, obj):
        if obj.status != "open":
            return None
        from .services import compute_expected_cash
        return str(compute_expected_cash(obj)["expected_cash"])


class OpenSessionSerializer(serializers.Serializer):
    branch = serializers.IntegerField(min_value=1)
    opening_balance = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"), default=Decimal("0"),
    )


class CloseSessionSerializer(serializers.Serializer):
    actual_cash = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )
    explanation = serializers.CharField(required=False, allow_blank=True, default="")


class CashMovementCreateSerializer(serializers.Serializer):
    movement_type = serializers.ChoiceField(
        choices=[
            CashMovementType.INFLOW,
            CashMovementType.WITHDRAWAL,
            CashMovementType.EXPENSE,
            CashMovementType.REFUND,
        ],
    )
    amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
    )
    reason = serializers.CharField()
    reference = serializers.CharField(required=False, allow_blank=True, default="")