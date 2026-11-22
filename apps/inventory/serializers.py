from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.access import can_see_costs

from .models import Inventory, StockMovement, StockMovementType


def _money(value) -> str | None:
    return None if value is None else f"{value:.4f}"


class InventorySerializer(serializers.ModelSerializer):
    """
    A product's stock at a branch. `unit_cost`/`stock_value` use the product's
    current cost price (INV-013, costing method: latest cost price) and are
    left out for roles that may not see costs.
    """
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_unit = serializers.CharField(source="product.unit", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    product_image = serializers.ImageField(source="product.image", read_only=True, allow_null=True)
    product_is_active = serializers.BooleanField(source="product.is_active", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    unit_cost = serializers.SerializerMethodField()
    stock_value = serializers.SerializerMethodField()

    class Meta:
        model = Inventory
        fields = (
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "product_sku",
            "product_unit",
            "product_type",
            "product_image",
            "product_is_active",
            "quantity",
            "minimum_threshold",
            "is_low_stock",
            "unit_cost",
            "stock_value",
            "updated_at",
        )
        read_only_fields = fields

    def _cost(self, obj):
        if hasattr(obj, "current_cost"):
            return obj.current_cost
        row = obj.product.current_price()
        return row.cost_price if row else None

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=4, allow_null=True))
    def get_unit_cost(self, obj):
        return _money(self._cost(obj))

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=4, allow_null=True))
    def get_stock_value(self, obj):
        cost = self._cost(obj)
        return None if cost is None else _money(cost * obj.quantity)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not can_see_costs(getattr(request, "user", None)):
            data.pop("unit_cost", None)
            data.pop("stock_value", None)
        return data


class InventorySummarySerializer(serializers.Serializer):
    tracked_products = serializers.IntegerField()
    low_stock = serializers.IntegerField(help_text="At or below their minimum, but not empty.")
    out_of_stock = serializers.IntegerField()
    total_value = serializers.DecimalField(max_digits=16, decimal_places=4, allow_null=True)
    costing_method = serializers.CharField(
        help_text="How stock is valued (INV-013): 'latest_cost'.")


class StockCountLineSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    counted_quantity = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=Decimal("0"))


class StockCountSerializer(serializers.Serializer):
    branch = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500)
    lines = StockCountLineSerializer(many=True, allow_empty=False)

    def validate_lines(self, value):
        ids = [line["product_id"] for line in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each product may appear only once.")
        return value


class StockCountResultLineSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    product_name = serializers.CharField()
    expected = serializers.DecimalField(max_digits=14, decimal_places=4)
    counted = serializers.DecimalField(max_digits=14, decimal_places=4)
    difference = serializers.DecimalField(max_digits=14, decimal_places=4)


class StockCountResultSerializer(serializers.Serializer):
    adjusted = serializers.IntegerField(help_text="Lines whose stock changed.")
    lines = StockCountResultLineSerializer(many=True)


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    performed_by_display = serializers.CharField(
        source="performed_by.username", read_only=True
    )
    performed_by_name = serializers.SerializerMethodField()
    product_unit = serializers.CharField(source="product.unit", read_only=True)
    sale_number = serializers.CharField(
        source="sale.transaction_number", read_only=True, allow_null=True, default=None)

    def get_performed_by_name(self, obj) -> str:
        return obj.performed_by.get_full_name() or obj.performed_by.username

    class Meta:
        model = StockMovement
        fields = (
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "product_sku",
            "product_unit",
            "movement_type",
            "delta",
            "quantity_after",
            "reason",
            "reference",
            "sale",
            "sale_number",
            "performed_by",
            "performed_by_display",
            "performed_by_name",
            "created_at",
        )
        read_only_fields = fields


class ThresholdUpdateSerializer(serializers.Serializer):
    minimum_threshold = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )


class InflowSerializer(serializers.Serializer):
    branch = serializers.IntegerField(
        required=False,
        help_text="Branch id (may also be passed as ?branch=). Required in one of the two.",
    )
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
    branch = serializers.IntegerField(
        required=False,
        help_text="Branch id (may also be passed as ?branch=). Required in one of the two.",
    )
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
    branch = serializers.IntegerField(
        required=False,
        help_text="Branch id (may also be passed as ?branch=). Required in one of the two.",
    )
    product_id = serializers.IntegerField(min_value=1)
    new_quantity = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )
    reason = serializers.CharField()