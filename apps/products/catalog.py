"""
POS catalogue (POS-001..POS-003, SRS 5.5 "POS Interface Requirements").

    GET /api/pos/catalog/?branch=<id>

Everything the POS screen needs in one unpaginated call: the branch's
sellable products with their current price, the categories to tab by, the
currency to format with, and the caller's discount allowance. "Sellable"
means exactly what `create_sale` will accept, so the POS never shows a
product the backend would then reject:
  - product active, and its category (if any) active;
  - not hidden at this branch (BranchProductAvailability);
  - has a current price.
Cost prices are never included — this endpoint is used by cashiers.
"""
from decimal import Decimal

from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.accounts.access import can_access_branch
from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.branches.models import Branch
from apps.inventory.models import Inventory
from apps.sales.services import DISCOUNT_APPROVER_ROLES
from apps.settings.models import BusinessSettings

from .models import BranchProductAvailability, Category, Product
from .pricing import with_current_prices


# ------------------------------------------------------------------------------
# Response shape (also documents the endpoint in the OpenAPI schema)
# ------------------------------------------------------------------------------
class _CatalogBranch(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class _CatalogCurrency(serializers.Serializer):
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()


class _CatalogDiscount(serializers.Serializer):
    unlimited = serializers.BooleanField(
        help_text="True for owners and branch managers: any discount up to the line value.")
    max_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, allow_null=True,
        help_text="Cashier limit as % of the line value; null when unlimited.")


class _CatalogCategory(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class _CatalogProduct(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    sku = serializers.CharField()
    description = serializers.CharField()
    image = serializers.CharField(allow_null=True)
    category_id = serializers.IntegerField(allow_null=True)
    product_type = serializers.CharField()
    price = serializers.DecimalField(max_digits=14, decimal_places=4)
    stock_tracked = serializers.BooleanField()
    stock_quantity = serializers.DecimalField(max_digits=14, decimal_places=4, allow_null=True)
    is_low_stock = serializers.BooleanField()
    in_stock = serializers.BooleanField(
        help_text="False only for tracked products with nothing on hand.")


class PosCatalogSerializer(serializers.Serializer):
    branch = _CatalogBranch()
    currency = _CatalogCurrency()
    discount = _CatalogDiscount()
    categories = _CatalogCategory(many=True)
    products = _CatalogProduct(many=True)


# ------------------------------------------------------------------------------
# View
# ------------------------------------------------------------------------------
class PosCatalogView(APIView):
    permission_classes = [HasAnyRole]
    # The roles allowed to create sales (apps.sales.services).
    allowed_roles = (UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.CASHIER)

    @extend_schema(
        parameters=[OpenApiParameter("branch", int, required=True,
                                     description="Branch the POS is selling at.")],
        responses=PosCatalogSerializer,
    )
    def get(self, request):
        branch = self._branch(request)
        settings = BusinessSettings.objects.select_related("primary_currency").get(
            business_id=branch.business_id,
        )
        currency = settings.primary_currency

        hidden = BranchProductAvailability.objects.filter(
            branch=branch, is_available=False,
        ).values("product_id")
        products = (
            with_current_prices(
                Product.objects.filter(business_id=branch.business_id, is_active=True)
                .filter(Q(category__isnull=True) | Q(category__is_active=True))
                .exclude(id__in=hidden)
            )
            .filter(current_selling_price__isnull=False)
            .order_by("name")
        )
        stock = {
            inv.product_id: inv
            for inv in Inventory.objects.filter(branch=branch)
        }

        used_categories = {p.category_id for p in products if p.category_id}
        categories = Category.objects.filter(id__in=used_categories).order_by("name")

        unlimited = request.user.role in DISCOUNT_APPROVER_ROLES
        return Response({
            "branch": {"id": branch.id, "name": branch.name},
            "currency": {
                "code": currency.code, "symbol": currency.symbol,
                "decimal_places": currency.decimal_places,
            },
            "discount": {
                "unlimited": unlimited,
                "max_percent": None if unlimited else str(settings.cashier_max_discount_percent),
            },
            "categories": [{"id": c.id, "name": c.name} for c in categories],
            "products": [self._product(request, p, stock.get(p.id)) for p in products],
        })

    @staticmethod
    def _product(request, p, inv):
        tracked = inv is not None
        return {
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "description": p.description,
            "image": request.build_absolute_uri(p.image.url) if p.image else None,
            "category_id": p.category_id,
            "product_type": p.product_type,
            "price": str(p.current_selling_price),
            "stock_tracked": tracked,
            "stock_quantity": str(inv.quantity) if tracked else None,
            "is_low_stock": tracked and inv.quantity <= inv.minimum_threshold,
            "in_stock": (not tracked) or inv.quantity > Decimal("0"),
        }

    @staticmethod
    def _branch(request):
        raw = request.query_params.get("branch")
        if not raw:
            raise ValidationError({"branch": "This query parameter is required."})
        try:
            branch = Branch.objects.get(pk=int(raw), business_id=request.user.business_id)
        except (ValueError, Branch.DoesNotExist):
            raise ValidationError({"branch": "Branch not found."})
        if not branch.is_active:
            raise ValidationError({"branch": "This branch is deactivated."})
        if not can_access_branch(request.user, branch):
            raise PermissionDenied("You are not assigned to this branch.")
        return branch
