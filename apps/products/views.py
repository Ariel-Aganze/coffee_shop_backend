from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.audit.services import log_action
from apps.branches.models import Branch

from .models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
)
from .serializers import (
    BranchProductAvailabilitySerializer,
    CategorySerializer,
    ProductCreateSerializer,
    ProductPriceCreateSerializer,
    ProductPriceSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
)


# ------------------------------------------------------------------------------
# Category
# ------------------------------------------------------------------------------
class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        # Only owner and inventory managers can write
        self.allowed_roles = (UserRole.OWNER, UserRole.INVENTORY_MANAGER)
        return [HasAnyRole()]

    def get_queryset(self):
        return Category.objects.filter(
            business_id=self.request.user.business_id
        ).order_by("name")

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)


# ------------------------------------------------------------------------------
# Product
# ------------------------------------------------------------------------------
class ProductViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "prices", "current_price"):
            return [IsAuthenticated()]
        self.allowed_roles = (UserRole.OWNER, UserRole.INVENTORY_MANAGER)
        return [HasAnyRole()]

    def get_serializer_class(self):
        if self.action == "create":
            return ProductCreateSerializer
        if self.action in ("update", "partial_update"):
            return ProductUpdateSerializer
        return ProductSerializer

    def get_queryset(self):
        qs = (
            Product.objects.filter(business_id=self.request.user.business_id)
            .select_related("category")
            .order_by("name")
        )
        category_id = self.request.query_params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == "true")
        search = self.request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(sku__icontains=search)
        return qs.distinct()

    def perform_create(self, serializer):
        product = serializer.save()
        log_action(
            business=product.business,
            user=self.request.user,
            action="product_created",
            target_type="Product",
            target_id=str(product.pk),
            metadata={"sku": product.sku, "name": product.name},
        )

    def perform_update(self, serializer):
        product = serializer.save()
        log_action(
            business=product.business,
            user=self.request.user,
            action="product_updated",
            target_type="Product",
            target_id=str(product.pk),
        )

    def perform_destroy(self, instance):
        # PROD-007: deactivate instead of hard delete
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        log_action(
            business=instance.business,
            user=self.request.user,
            action="product_deactivated",
            target_type="Product",
            target_id=str(instance.pk),
        )

    # ---------- Prices ----------
    @action(detail=True, methods=["get", "post"], url_path="prices")
    def prices(self, request, pk=None):
        """
        GET  /api/products/{id}/prices/   → list all price rows
        POST /api/products/{id}/prices/   → create a new price row
        """
        product = self.get_object()

        if request.method == "GET":
            rows = product.prices.select_related("created_by").all()
            return Response(ProductPriceSerializer(rows, many=True).data)

        # POST → new price
        payload = ProductPriceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        effective_from = payload.validated_data.get("effective_from") or timezone.now()

        new_price = ProductPrice.objects.create(
            product=product,
            selling_price=payload.validated_data["selling_price"],
            cost_price=payload.validated_data.get("cost_price", 0),
            effective_from=effective_from,
            created_by=request.user,
        )

        log_action(
            business=product.business,
            user=request.user,
            action="product_price_changed",
            target_type="Product",
            target_id=str(product.pk),
            metadata={
                "new_price_id": str(new_price.pk),
                "selling_price": str(new_price.selling_price),
                "effective_from": effective_from.isoformat(),
                "reason": payload.validated_data.get("reason", ""),
            },
        )
        return Response(ProductPriceSerializer(new_price).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="current-price")
    def current_price(self, request, pk=None):
        product = self.get_object()
        row = product.current_price()
        if not row:
            return Response({"detail": "No price is set for this product."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(ProductPriceSerializer(row).data)


# ------------------------------------------------------------------------------
# Branch product availability
# ------------------------------------------------------------------------------
class BranchProductAvailabilityViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = BranchProductAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = BranchProductAvailability.objects.filter(
            branch__business_id=self.request.user.business_id
        ).select_related("branch", "product")

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)

        if self.request.user.role != UserRole.OWNER:
            qs = qs.filter(branch_id__in=self.request.user.assigned_branch_ids)

        return qs

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        self.allowed_roles = (
            UserRole.OWNER,
            UserRole.BRANCH_MANAGER,
            UserRole.INVENTORY_MANAGER,
        )
        return [HasAnyRole()]