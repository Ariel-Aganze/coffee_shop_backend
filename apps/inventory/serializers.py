from decimal import Decimal

from rest_framework import serializers

from .models import Inventory, StockMovement, StockMovementType


class InventorySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Inventory
        fields = (
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "product_sku",
            "quantity",
            "minimum_threshold",
            "is_low_stock",
            "updated_at",
        )
        read_only_fields = fields


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    performed_by_display = serializers.CharField(
        source="performed_by.username", read_only=True
    )

    class Meta:
        model = StockMovement
        fields = (
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "product_sku",
            "movement_type",
            "delta",
            "quantity_after",
            "reason",
            "reference",
            "sale",
            "performed_by",
            "performed_by_display",
            "created_at",
        )
        read_only_fields = fields


class ThresholdUpdateSerializer(serializers.Serializer):
    minimum_threshold = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )


class InflowSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
    )
    movement_type = serializers.ChoiceField(
        choices=[StockMovementType.PURCHASE, StockMovementType.RETURN,
                 StockMovementType.TRANSFER_IN, StockMovementType.ADJUSTMENT],
        default=StockMovementType.PURCHASE,
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class OutflowSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
    )
    movement_type = serializers.ChoiceField(
        choices=[StockMovementType.ADJUSTMENT, StockMovementType.DAMAGED,
                 StockMovementType.EXPIRED, StockMovementType.TRANSFER_OUT],
        default=StockMovementType.ADJUSTMENT,
    )
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class AdjustmentSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    new_quantity = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )
    reason = serializers.CharField()