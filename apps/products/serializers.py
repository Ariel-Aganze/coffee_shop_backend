from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.access import can_see_costs
from config.uploads import validate_image_upload

from .models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
)

# A price may start "now" give or take clock drift between browser and server;
# anything earlier would rewrite what past sales and reports were based on.
PRICE_BACKDATE_GRACE = timedelta(minutes=5)


def _request_user(serializer):
    request = serializer.context.get("request")
    return getattr(request, "user", None)


def _business_id(serializer):
    return serializer.context["request"].user.business_id


# ------------------------------------------------------------------------------
# Category
# ------------------------------------------------------------------------------
class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField(
        help_text="Active products in this category.")

    class Meta:
        model = Category
        fields = ("id", "name", "description", "is_active", "product_count",
                  "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    @extend_schema_field(serializers.IntegerField())
    def get_product_count(self, obj) -> int:
        if hasattr(obj, "active_product_count"):
            return obj.active_product_count
        return obj.products.filter(is_active=True).count()

    def validate_name(self, value):
        value = value.strip()
        qs = Category.objects.filter(business_id=_business_id(self), name__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("A category with this name already exists.")
        return value


# ------------------------------------------------------------------------------
# ProductPrice
# ------------------------------------------------------------------------------
class ProductPriceSerializer(serializers.ModelSerializer):
    created_by_display = serializers.CharField(
        source="created_by.username", read_only=True, allow_null=True, default=None
    )
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductPrice
        fields = (
            "id",
            "selling_price",
            "cost_price",
            "effective_from",
            "reason",
            "created_by",
            "created_by_display",
            "created_by_name",
            "created_at",
        )
        read_only_fields = ("id", "created_by", "created_at")

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj):
        user = obj.created_by
        if user is None:
            return None
        return user.get_full_name() or user.username

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not can_see_costs(_request_user(self)):
            data.pop("cost_price", None)
        return data


# ------------------------------------------------------------------------------
# Product (read)
# ------------------------------------------------------------------------------
class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True,
                                          allow_null=True, default=None)
    current_price = serializers.SerializerMethodField()
    current_cost_price = serializers.SerializerMethodField()
    hidden_branch_ids = serializers.SerializerMethodField(
        help_text="Branches where this product is switched off (PROD-008). "
                  "Every other branch sells it.")

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
            "unit",
            "is_active",
            "current_price",
            "current_cost_price",
            "hidden_branch_ids",
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

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_hidden_branch_ids(self, obj):
        rows = getattr(obj, "hidden_availabilities", None)
        if rows is None:  # not prefetched (single object)
            rows = obj.branch_availabilities.filter(is_available=False)
        return sorted(r.branch_id for r in rows)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not can_see_costs(_request_user(self)):
            data.pop("current_cost_price", None)
        return data


# ------------------------------------------------------------------------------
# Product (write)
# ------------------------------------------------------------------------------
class _ProductWriteMixin:
    """Checks shared by create and update."""

    def validate_image(self, value):
        return validate_image_upload(value, "The image")

    def validate_category(self, value):
        if value is None:
            return value
        if value.business_id != _business_id(self):
            raise serializers.ValidationError("Category does not belong to your business.")
        current = getattr(self.instance, "category_id", None)
        if not value.is_active and value.pk != current:
            raise serializers.ValidationError(
                "This category is deactivated. Reactivate it or choose another one.")
        return value

    def validate_sku(self, value):
        value = value.strip()
        qs = Product.objects.filter(business_id=_business_id(self), sku__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A product with this SKU already exists in your business.")
        return value

    def validate_name(self, value):
        return value.strip()


class ProductCreateSerializer(_ProductWriteMixin, serializers.ModelSerializer):
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
            "unit",
            "is_active",
            "initial_selling_price",
            "initial_cost_price",
        )
        read_only_fields = ("id",)

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
            reason="Initial price",
        )
        return product


class ProductUpdateSerializer(_ProductWriteMixin, serializers.ModelSerializer):
    """
    Updates product metadata only. To change price, use the
    /products/{id}/prices/ endpoint. Send `image: null` to remove the photo.
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
            "unit",
            "is_active",
        )


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
    effective_from = serializers.DateTimeField(
        required=False,
        help_text="When the price starts. Omit for immediately; a future time "
                  "schedules it. Past times are refused (PROD-009).")
    reason = serializers.CharField(required=False, allow_blank=True, default="",
                                   max_length=255)

    def validate_effective_from(self, value):
        if value < timezone.now() - PRICE_BACKDATE_GRACE:
            raise serializers.ValidationError(
                "A price can't start in the past: past sales keep the price they "
                "were sold at. Leave it empty to start now, or pick a future time.")
        return value


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
        from apps.accounts.access import can_access_branch

        user = self.context["request"].user
        branch = attrs.get("branch") or getattr(self.instance, "branch", None)
        product = attrs.get("product") or getattr(self.instance, "product", None)
        if branch and not can_access_branch(user, branch):
            raise serializers.ValidationError({"branch": "You do not have access to this branch."})
        if product and product.business_id != user.business_id:
            raise serializers.ValidationError("Product does not belong to your business.")
        return attrs


class ProductBranchAvailabilitySerializer(serializers.Serializer):
    """One branch's row in a product's availability panel."""
    branch = serializers.IntegerField()
    branch_name = serializers.CharField()
    is_available = serializers.BooleanField()
    can_edit = serializers.BooleanField(
        help_text="Whether the caller may switch this branch on or off.")


class _AvailabilityChange(serializers.Serializer):
    branch = serializers.IntegerField()
    is_available = serializers.BooleanField()


class ProductAvailabilityUpdateSerializer(serializers.Serializer):
    branches = _AvailabilityChange(many=True, allow_empty=False)

    def validate_branches(self, value):
        ids = [row["branch"] for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each branch may appear only once.")
        return value
