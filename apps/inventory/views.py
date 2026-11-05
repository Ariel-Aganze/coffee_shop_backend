from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.branches.models import Branch
from apps.products.models import Product

from .models import Inventory, StockMovement
from .serializers import (
    AdjustmentSerializer,
    InflowSerializer,
    InventorySerializer,
    OutflowSerializer,
    StockMovementSerializer,
    ThresholdUpdateSerializer,
)
from .services import (
    manual_adjustment,
    record_inflow,
    record_outflow,
)


class InventoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Read-only list of stock levels with per-branch scoping.
    Writes happen through dedicated actions.
    """
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Inventory.objects.filter(
            branch__business_id=user.business_id
        ).select_related("branch", "product")

        if user.role != UserRole.OWNER:
            qs = qs.filter(branch_id__in=user.assigned_branch_ids)

        params = self.request.query_params
        branch_id = params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        product_id = params.get("product")
        if product_id:
            qs = qs.filter(product_id=product_id)
        low_only = params.get("low_stock")
        if low_only is not None and low_only.lower() == "true":
            from django.db.models import F
            qs = qs.filter(quantity__lte=F("minimum_threshold"))

        return qs.order_by("branch__name", "product__name")

    # ---------- Threshold update ----------
    @action(detail=True, methods=["patch"], url_path="threshold")
    def update_threshold(self, request, pk=None):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER,
        ):
            return Response({"error": "Not allowed."},
                            status=status.HTTP_403_FORBIDDEN)
        inventory = self.get_object()
        payload = ThresholdUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        inventory.minimum_threshold = payload.validated_data["minimum_threshold"]
        inventory.save(update_fields=["minimum_threshold", "updated_at"])
        return Response(InventorySerializer(inventory).data)


class StockMovementViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Immutable ledger of stock movements (INV-008).
    Writes happen through the dedicated actions below.
    """
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = StockMovement.objects.filter(
            branch__business_id=user.business_id
        ).select_related("branch", "product", "performed_by", "sale")

        if user.role != UserRole.OWNER:
            qs = qs.filter(branch_id__in=user.assigned_branch_ids)

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
        date_from = params.get("from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = params.get("to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

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
            raise ValidationError("Branch does not belong to your business.")
        if request.user.role != UserRole.OWNER and branch.id not in request.user.assigned_branch_ids:
            raise ValidationError("You are not assigned to this branch.")
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
    @action(detail=False, methods=["post"], url_path="inflow")
    def inflow(self, request):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER,
        ):
            return Response({"error": "Not allowed."},
                            status=status.HTTP_403_FORBIDDEN)
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
    @action(detail=False, methods=["post"], url_path="outflow")
    def outflow(self, request):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER,
        ):
            return Response({"error": "Not allowed."},
                            status=status.HTTP_403_FORBIDDEN)
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
    @action(detail=False, methods=["post"], url_path="adjust")
    def adjust(self, request):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER,
        ):
            return Response({"error": "Not allowed."},
                            status=status.HTTP_403_FORBIDDEN)
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