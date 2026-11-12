from decimal import Decimal

from rest_framework import serializers

from .models import Sale, SaleItem

from apps.payments.models import Payment



class SaleItemReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = (
            "id",
            "product",
            "product_name",
            "sku",
            "quantity",
            "unit_price",
            "discount_amount",
            "line_total",
        )

class PaymentReadSerializer(serializers.ModelSerializer):
    method_display = serializers.CharField(source="get_method_display", read_only=True)
    received_by_display = serializers.CharField(source="received_by.username", read_only=True)

    class Meta:
        model = Payment
        fields = (
            "id",
            "method",
            "method_display",
            "amount",
            "currency_code",
            "reference",
            "is_refund",
            "refunded_payment",
            "received_by",
            "received_by_display",
            "created_at",
        )
        read_only_fields = fields


class SaleReadSerializer(serializers.ModelSerializer):
    items = SaleItemReadSerializer(many=True, read_only=True)
    payments = PaymentReadSerializer(many=True, read_only=True)
    cashier_display = serializers.CharField(source="cashier.username", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = Sale
        fields = (
            "id",
            "transaction_number",
            "branch",
            "branch_name",
            "cashier",
            "cashier_display",
            "status",
            "subtotal",
            "discount_total",
            "total_amount",
            "currency_code",
            "currency_decimal_places",
            "note",
            "created_at",
            "completed_at",
            "cancelled_at",
            "cancellation_reason",
            "items",
            "payments",
        )
        read_only_fields = fields

class SaleItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=4, min_value=Decimal("0.0001"))
    discount_amount = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"), required=False, default=Decimal("0"),
    )


class SaleCreateSerializer(serializers.Serializer):
    branch = serializers.IntegerField(min_value=1)
    items = SaleItemInputSerializer(many=True, allow_empty=False)
    note = serializers.CharField(required=False, allow_blank=True, default="")




# ------------------------------------------------------------------------------
# Receipt (documentation of GET /api/sales/{id}/receipt/, SRS 8.3)
# ------------------------------------------------------------------------------
class _ReceiptItemSerializer(serializers.Serializer):
    name = serializers.CharField()
    sku = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=14, decimal_places=4)
    discount = serializers.DecimalField(max_digits=14, decimal_places=4)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=4)


class _ReceiptPaymentSerializer(serializers.Serializer):
    method = serializers.CharField(help_text="Display name, e.g. 'Mobile Money'.")
    amount = serializers.DecimalField(max_digits=14, decimal_places=4)
    reference = serializers.CharField()
    is_refund = serializers.BooleanField()


class ReceiptSerializer(serializers.Serializer):
    business = serializers.CharField()
    branch = serializers.CharField()
    transaction_number = serializers.CharField()
    datetime = serializers.DateTimeField(allow_null=True)
    cashier = serializers.CharField()
    items = _ReceiptItemSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=14, decimal_places=4)
    discount_total = serializers.DecimalField(max_digits=14, decimal_places=4)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=4)
    currency_code = serializers.CharField()
    payments = _ReceiptPaymentSerializer(many=True)
    status = serializers.CharField()
    note = serializers.CharField()
