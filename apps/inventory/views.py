from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from decimal import Decimal

from django.db.models import DecimalField, F, OuterRef, Q, Subquery
from django.db.models.functions import Now
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema_view
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.access import can_see_costs
from apps.audit.services import log_action
from apps.products.models import ProductPrice

from apps.accounts.access import can_access_branch, scope_to_branches
from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.branches.models import Branch
from apps.products.models import Product
from apps.settings.regional import business_zone, day_end, day_start
from drf_spectacular.utils import extend_schema

from .models import Inventory, StockMovement, StockMovementType
from .serializers import (
    AdjustmentSerializer,
    InflowSerializer,
    InventorySerializer,
    InventorySummarySerializer,
    StockCountResultSerializer,
    StockCountSerializer,
    OutflowSerializer,
    StockMovementSerializer,
    ThresholdUpdateSerializer,
)
from .services import (
    stock_count,
    manual_adjustment,
    record_inflow,
    record_outflow,
)


STOCK_ROLES = (UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER)
INVENTORY_ORDERINGS = {
    "name": ("product__name",),
    "quantity": ("quantity",),
    "-quantity": ("-quantity",),
}


def _latest_cost():
    """Current cost price of the row's product (INV-013: latest cost)."""
    return Subquery(
        ProductPrice.objects.filter(product=OuterRef("product_id"), effective_from__lte=Now())
        .order_by("-effective_from", "-id").values("cost_price")[:1],
        output_field=DecimalField(max_digits=14, decimal_places=4),
    )


def _require_stock_role(user):
    if user.role not in STOCK_ROLES:
        raise PermissionDenied("Only owners, branch managers and inventory managers change stock.")


def _writable_branch(user, branch_id):
    branch = Branch.objects.filter(pk=branch_id, business_id=user.business_id).first()
    if branch is None:
        raise ValidationError({"branch": "Branch not found."})
    if not can_access_branch(user, branch):
        raise PermissionDenied("You are not assigned to this branch.")
    return branch


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter("branch", int),
    OpenApiParameter("product", int),
    OpenApiParameter("search", str, description="Product name or SKU contains."),
    OpenApiParameter("level", str, enum=["low", "out", "ok"],
                     description="low: at/below minimum but not empty; out: empty; ok: above minimum."),
    OpenApiParameter("active", bool, description="Only active (true) or deactivated (false) products."),
    OpenApiParameter("ordering", str, enum=list(INVENTORY_ORDERINGS)),
]))
class InventoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Read-only list of stock levels with per-branch scoping.
    Writes happen through dedicated actions.
    """
    queryset = Inventory.objects.none()  # schema only; real queryset in get_queryset
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Inventory.objects.filter(
            branch__business_id=user.business_id
        ).select_related("branch", "product").annotate(
            current_cost=_latest_cost(),
        )
        qs = scope_to_branches(qs, user)

        params = self.request.query_params
        if params.get("branch"):
            qs = qs.filter(branch_id=params["branch"])
        if params.get("product"):
            qs = qs.filter(product_id=params["product"])
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(product__name__icontains=search) | Q(product__sku__icontains=search))
        level = params.get("level")
        if level == "low":
            qs = qs.filter(quantity__lte=F("minimum_threshold"), quantity__gt=0)
        elif level == "out":
            qs = qs.filter(quantity__lte=0)
        elif level == "ok":
            qs = qs.filter(quantity__gt=F("minimum_threshold"))
        low_only = params.get("low_stock")  # older flag: low OR empty
        if low_only is not None and low_only.lower() == "true":
            qs = qs.filter(quantity__lte=F("minimum_threshold"))
        if params.get("active") is not None:
            qs = qs.filter(product__is_active=params["active"].lower() == "true")
        ordering = INVENTORY_ORDERINGS.get(params.get("ordering", ""), ("product__name",))
        return qs.order_by(*ordering, "branch__name", "id")

    # ---------- Summary ----------
    @extend_schema(
        parameters=[OpenApiParameter("branch", int, description="One branch; omit for all you can see.")],
        responses=InventorySummarySerializer,
    )
    @action(detail=False, methods=["get"], url_path="summary", pagination_class=None)
    def summary(self, request):
        rows = list(
            scope_to_branches(
                Inventory.objects.filter(branch__business_id=request.user.business_id)
                .annotate(current_cost=_latest_cost()),
                request.user,
            )
            .filter(**({"branch_id": request.query_params["branch"]}
                       if request.query_params.get("branch") else {}))
            .values("quantity", "minimum_threshold", "current_cost")
        )
        total = sum((r["quantity"] * r["current_cost"] for r in rows if r["current_cost"] is not None),
                    Decimal("0"))
        data = {
            "tracked_products": len(rows),
            "low_stock": sum(1 for r in rows if 0 < r["quantity"] <= r["minimum_threshold"]),
            "out_of_stock": sum(1 for r in rows if r["quantity"] <= 0),
            "total_value": total,
            "costing_method": "latest_cost",
        }
        out = InventorySummarySerializer(data).data
        if not can_see_costs(request.user):
            out.pop("total_value", None)
        return Response(out)

    # ---------- Physical count ----------
    @extend_schema(request=StockCountSerializer, responses={201: StockCountResultSerializer})
    @action(detail=False, methods=["post"], url_path="count")
    def count(self, request):
        _require_stock_role(request.user)
        payload = StockCountSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        branch = _writable_branch(request.user, payload.validated_data["branch"])
        result = stock_count(
            branch=branch,
            lines=payload.validated_data["lines"],
            reason=payload.validated_data["reason"],
            performed_by=request.user,
        )
        return Response(StockCountResultSerializer(result).data, status=status.HTTP_201_CREATED)

    # ---------- Threshold update ----------
    @extend_schema(request=ThresholdUpdateSerializer, responses=InventorySerializer)
    @action(detail=True, methods=["patch"], url_path="threshold")
    def update_threshold(self, request, pk=None):
        _require_stock_role(request.user)
        inventory = self.get_object()
        payload = ThresholdUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        old = inventory.minimum_threshold
        inventory.minimum_threshold = payload.validated_data["minimum_threshold"]
        inventory.save(update_fields=["minimum_threshold", "updated_at"])
        log_action(
            business=inventory.branch.business, branch=inventory.branch, user=request.user,
            action="stock_threshold_changed", target_type="Inventory",
            target_id=f"{inventory.branch_id}:{inventory.product_id}",
            metadata={"from": str(old), "to": str(inventory.minimum_threshold)},
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=inventory.pk)).data)


@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter("branch", int),
    OpenApiParameter("product", int),
    OpenApiParameter("type", str, enum=StockMovementType.values),
    OpenApiParameter("from", OpenApiTypes.DATE, description="On or after this day (shop time)."),
    OpenApiParameter("to", OpenApiTypes.DATE, description="On or before this day (shop time)."),
]))
class StockMovementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Immutable ledger of stock movements (INV-008).
    Writes happen through the dedicated actions below.
    """
    queryset = StockMovement.objects.none()  # schema only; real queryset in get_queryset
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = StockMovement.objects.filter(
            branch__business_id=user.business_id
        ).select_related("branch", "product", "performed_by", "sale")

        qs = scope_to_branches(qs, user)

        params = self.request.query_params
        branch_id = params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        product_id = params.get("product")
        if product_id:
            qs = qs.filter(product_id=product_id)
        movement_type = params.get("type")
        if movement_type:
            qs = qs.filter(movement_type=movement_type)
        if params.get("from") or params.get("to"):
            tz = business_zone(self.request.user.business_id)  # shop days, not UTC
            if params.get("from"):
                qs = qs.filter(created_at__gte=day_start(params["from"], "from", tz))
            if params.get("to"):
                qs = qs.filter(created_at__lte=day_end(params["to"], "to", tz))

        return qs.order_by("-created_at")

    def _get_branch_for_write(self, request):
        branch_id = request.data.get("branch") or request.query_params.get("branch")
        if not branch_id:
            raise ValidationError("'branch' is required.")
        try:
            branch = Branch.objects.get(pk=branch_id)
        except Branch.DoesNotExist:
            raise ValidationError("Branch not found.")
        if branch.business_id != request.user.business_id:
            raise ValidationError("Branch not found.")
        if not can_access_branch(request.user, branch):
            raise PermissionDenied("You are not assigned to this branch.")
        return branch

    def _get_product(self, request, product_id):
        try:
            product = Product.objects.get(
                pk=product_id, business_id=request.user.business_id,
            )
        except Product.DoesNotExist:
            raise ValidationError("Product not found.")
        return product

    # ---------- Inflow ----------
    @extend_schema(request=InflowSerializer, responses={201: StockMovementSerializer})
    @action(detail=False, methods=["post"], url_path="inflow")
    def inflow(self, request):
        _require_stock_role(request.user)
        branch = self._get_branch_for_write(request)
        payload = InflowSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        product = self._get_product(request, payload.validated_data["product_id"])

        movement = record_inflow(
            branch=branch,
            product=product,
            quantity=payload.validated_data["quantity"],
            movement_type=payload.validated_data["movement_type"],
            reason=payload.validated_data.get("reason", ""),
            reference=payload.validated_data.get("reference", ""),
            performed_by=request.user,
        )
        return Response(StockMovementSerializer(movement).data,
                        status=status.HTTP_201_CREATED)

    # ---------- Outflow ----------
    @extend_schema(request=OutflowSerializer, responses={201: StockMovementSerializer})
    @action(detail=False, methods=["post"], url_path="outflow")
    def outflow(self, request):
        _require_stock_role(request.user)
        branch = self._get_branch_for_write(request)
        payload = OutflowSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        product = self._get_product(request, payload.validated_data["product_id"])

        movement = record_outflow(
            branch=branch,
            product=product,
            quantity=payload.validated_data["quantity"],
            movement_type=payload.validated_data["movement_type"],
            reason=payload.validated_data.get("reason", ""),
            reference=payload.validated_data.get("reference", ""),
            performed_by=request.user,
        )
        return Response(StockMovementSerializer(movement).data,
                        status=status.HTTP_201_CREATED)

    # ---------- Manual adjustment (physical count) ----------
    @extend_schema(request=AdjustmentSerializer, responses={201: StockMovementSerializer})
    @action(detail=False, methods=["post"], url_path="adjust")
    def adjust(self, request):
        _require_stock_role(request.user)
        branch = self._get_branch_for_write(request)
        payload = AdjustmentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        product = self._get_product(request, payload.validated_data["product_id"])

        movement = manual_adjustment(
            branch=branch,
            product=product,
            new_quantity=payload.validated_data["new_quantity"],
            reason=payload.validated_data["reason"],
            performed_by=request.user,
        )
        return Response(StockMovementSerializer(movement).data,
                        status=status.HTTP_201_CREATED)