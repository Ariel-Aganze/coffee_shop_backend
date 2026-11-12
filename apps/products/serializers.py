from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.access import can_see_costs

from apps.branches.models import Branch
from django.core.validators import MinValueValidator
from drf_spectacular.utils import extend_schema_field


from .models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
    ProductType,
)


# ------------------------------------------------------------------------------
# Category
# ------------------------------------------------------------------------------
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "description", "is_active", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


# ------------------------------------------------------------------------------
# ProductPrice
# ------------------------------------------------------------------------------
def _request_user(serializer):
    request = serializer.context.get("request")
    return getattr(request, "user", None)


class ProductPriceSerializer(serializers.ModelSerializer):
    created_by_display = serializers.CharField(
        source="created_by.username", read_only=True
    )

    class Meta:
        model = ProductPrice
        fields = (
            "id",
            "selling_price",
            "cost_price",
            "effective_from",
            "created_by",
            "created_by_display",
            "created_at",
        )
        read_only_fields = ("id", "created_by", "created_at")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not can_see_costs(_request_user(self)):
            data.pop("cost_price", None)
        return data


# ------------------------------------------------------------------------------
# Product (read)
# ------------------------------------------------------------------------------
class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    current_price = serializers.SerializerMethodField()
    current_cost_price = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "sku",
            "description",
            "image",
            "category",
            "category_name",
            "product_type",
            "is_active",
            "current_price",
            "current_cost_price",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def _price(self, obj, field):
        # Use the SQL annotation from products.pricing when the queryset has
        # it (lists); fall back to a query for single objects.
        if hasattr(obj, f"current_{field}"):
            value = getattr(obj, f"current_{field}")
            return str(value) if value is not None else None
        row = obj.current_price()
        return str(getattr(row, field)) if row else None

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=4, allow_null=True))
    def get_current_price(self, obj):
        return self._price(obj, "selling_price")

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=4, allow_null=True))
    def get_current_cost_price(self, obj):
        return self._price(obj, "cost_price")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not can_see_costs(_request_user(self)):
            data.pop("current_cost_price", None)
        return data


# ------------------------------------------------------------------------------
# Product (write)
# ------------------------------------------------------------------------------

class ProductCreateSerializer(serializers.ModelSerializer):
    initial_selling_price = serializers.DecimalField(
        max_digits=14, decimal_places=4, write_only=True,
        min_value=Decimal("0"),
    )
    initial_cost_price = serializers.DecimalField(
        max_digits=14, decimal_places=4, write_only=True, required=False,
        min_value=Decimal("0"), default=Decimal("0"),
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "sku",
            "description",
            "image",
            "category",
            "product_type",
            "is_active",
            "initial_selling_price",
            "initial_cost_price",
        )
        read_only_fields = ("id",)

    def validate_category(self, value):
        if value and value.business_id != self.context["request"].user.business_id:
            raise serializers.ValidationError(
                "Category does not belong to your business."
            )
        return value

    def validate_sku(self, value):
        business_id = self.context["request"].user.business_id
        if Product.objects.filter(business_id=business_id, sku=value).exists():
            raise serializers.ValidationError(
                "A product with this SKU already exists in your business."
            )
        return value

    @transaction.atomic
    def create(self, validated_data):
        selling = validated_data.pop("initial_selling_price")
        cost = validated_data.pop("initial_cost_price", Decimal("0"))
        user = self.context["request"].user

        product = Product.objects.create(business=user.business, **validated_data)
        ProductPrice.objects.create(
            product=product,
            selling_price=selling,
            cost_price=cost,
            effective_from=timezone.now(),
            created_by=user,
        )
        return product


class ProductUpdateSerializer(serializers.ModelSerializer):
    """
    Updates product metadata only. To change price, use the
    /products/{id}/prices/ endpoint.
    """

    class Meta:
        model = Product
        fields = (
            "name",
            "sku",
            "description",
            "image",
            "category",
            "product_type",
            "is_active",
        )

    def validate_category(self, value):
        if value and value.business_id != self.context["request"].user.business_id:
            raise serializers.ValidationError(
                "Category does not belong to your business."
            )
        return value

    def validate_sku(self, value):
        business_id = self.context["request"].user.business_id
        if (
            Product.objects.filter(business_id=business_id, sku=value)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise serializers.ValidationError(
                "A product with this SKU already exists in your business."
            )
        return value


# ------------------------------------------------------------------------------
# New Price
# ------------------------------------------------------------------------------
class ProductPriceCreateSerializer(serializers.Serializer):
    selling_price = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
    )
    cost_price = serializers.DecimalField(
        max_digits=14, decimal_places=4, min_value=Decimal("0"),
        required=False, default=Decimal("0"),
    )
    effective_from = serializers.DateTimeField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


# ------------------------------------------------------------------------------
# Branch availability
# ------------------------------------------------------------------------------
class BranchProductAvailabilitySerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = BranchProductAvailability
        fields = (
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "is_available",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")

    def validate(self, attrs):
        request = self.context["request"]
        branch = attrs.get("branch") or getattr(self.instance, "branch", None)
        product = attrs.get("product") or getattr(self.instance, "product", None)
        if branch and branch.business_id != request.user.business_id:
            raise serializers.ValidationError("Branch does not belong to your business.")
        if product and product.business_id != request.user.business_id:
            raise serializers.ValidationError("Product does not belong to your business.")
        return attrs