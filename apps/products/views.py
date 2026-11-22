from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.access import accessible_branch_ids, can_access_branch, scope_to_branches
from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.audit.services import log_action
from apps.branches.models import Branch
from config.schema import DetailSerializer

from .models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
    ProductType,
)
from .pricing import with_current_prices
from .serializers import (
    BranchProductAvailabilitySerializer,
    CategorySerializer,
    ProductAvailabilityUpdateSerializer,
    ProductBranchAvailabilitySerializer,
    ProductCreateSerializer,
    ProductPriceCreateSerializer,
    ProductPriceSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
)

PRODUCT_MANAGERS = (UserRole.OWNER, UserRole.INVENTORY_MANAGER)
# PROD-008: who may switch a product on/off at a branch (only their branches).
AVAILABILITY_MANAGERS = (UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER)


def _is_active_param(request):
    raw = request.query_params.get("is_active")
    return None if raw in (None, "") else raw.lower() == "true"


# ------------------------------------------------------------------------------
# Category
# ------------------------------------------------------------------------------
@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter("search", OpenApiTypes.STR, description="Name contains (case-insensitive)."),
    OpenApiParameter("is_active", OpenApiTypes.BOOL),
]))
class CategoryViewSet(viewsets.ModelViewSet):
    """
    Product categories (PROD-001). DELETE deactivates instead of deleting:
    products and past sales keep their category.
    """
    queryset = Category.objects.none()  # schema only; real queryset in get_queryset
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        self.allowed_roles = PRODUCT_MANAGERS
        return [HasAnyRole()]

    def get_queryset(self):
        qs = Category.objects.filter(business_id=self.request.user.business_id).annotate(
            active_product_count=Count("products", filter=Q(products__is_active=True)),
        )
        if self.action == "list":
            is_active = _is_active_param(self.request)
            if is_active is not None:
                qs = qs.filter(is_active=is_active)
            search = self.request.query_params.get("search", "").strip()
            if search:
                qs = qs.filter(name__icontains=search)
        return qs.order_by("-is_active", "name")

    def _log(self, category, action, **metadata):
        log_action(
            business=self.request.user.business,
            user=self.request.user,
            action=action,
            target_type="Category",
            target_id=str(category.pk),
            metadata={"name": category.name, **metadata},
        )

    def perform_create(self, serializer):
        self._log(serializer.save(business=self.request.user.business), "category_created")

    def perform_update(self, serializer):
        self._log(serializer.save(), "category_updated")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        self._log(instance, "category_deactivated")


# ------------------------------------------------------------------------------
# Product
# ------------------------------------------------------------------------------
PRODUCT_ORDERINGS = {
    "name": ("name",),
    "-name": ("-name",),
    "price": (F("current_selling_price").asc(nulls_last=True), "name"),
    "-price": (F("current_selling_price").desc(nulls_last=True), "name"),
    "-created_at": ("-created_at",),
}


@extend_schema_view(
    # Writes accept the write serializers but answer with the full product.
    create=extend_schema(responses={201: ProductSerializer}),
    update=extend_schema(responses=ProductSerializer),
    partial_update=extend_schema(responses=ProductSerializer),
    list=extend_schema(parameters=[
    OpenApiParameter("search", OpenApiTypes.STR, description="Name or SKU contains."),
    OpenApiParameter("category", OpenApiTypes.INT),
    OpenApiParameter("product_type", OpenApiTypes.STR, enum=ProductType.values),
    OpenApiParameter("is_active", OpenApiTypes.BOOL),
    OpenApiParameter("ordering", OpenApiTypes.STR, enum=list(PRODUCT_ORDERINGS)),
]),
)
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.none()  # schema only; real queryset in get_queryset
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # `prices` and `availability` serve GET (read) and a write; only the GET is open.
        is_read = self.request.method in ("GET", "HEAD", "OPTIONS")
        if self.action in ("list", "retrieve", "current_price"):
            return [IsAuthenticated()]
        if self.action in ("prices", "availability") and is_read:
            return [IsAuthenticated()]
        self.allowed_roles = (
            AVAILABILITY_MANAGERS if self.action == "availability" else PRODUCT_MANAGERS
        )
        return [HasAnyRole()]

    def get_serializer_class(self):
        if self.action == "create":
            return ProductCreateSerializer
        if self.action in ("update", "partial_update"):
            return ProductUpdateSerializer
        return ProductSerializer

    def get_queryset(self):
        qs = with_current_prices(
            Product.objects.filter(business_id=self.request.user.business_id)
            .select_related("category")
            .prefetch_related(Prefetch(
                "branch_availabilities",
                queryset=BranchProductAvailability.objects.filter(is_available=False),
                to_attr="hidden_availabilities",
            ))
        )
        params = self.request.query_params
        if self.action == "list":
            if params.get("category"):
                qs = qs.filter(category_id=params["category"])
            if params.get("product_type"):
                qs = qs.filter(product_type=params["product_type"])
            is_active = _is_active_param(self.request)
            if is_active is not None:
                qs = qs.filter(is_active=is_active)
            search = params.get("search", "").strip()
            if search:
                qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search))
        ordering = PRODUCT_ORDERINGS.get(params.get("ordering", ""), ("name",))
        return qs.order_by(*ordering, "id")

    def _log(self, product, action, **metadata):
        log_action(
            business=product.business,
            user=self.request.user,
            action=action,
            target_type="Product",
            target_id=str(product.pk),
            metadata=metadata,
        )

    def create(self, request, *args, **kwargs):
        # Respond with the full read shape (current price, category name…).
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        self._log(product, "product_created", sku=product.sku, name=product.name)
        return Response(self._read(product.pk), status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self._log(instance, "product_updated", fields=sorted(serializer.validated_data))
        return Response(self._read(instance.pk))

    def _read(self, pk):
        return ProductSerializer(self.get_queryset().get(pk=pk),
                                 context=self.get_serializer_context()).data

    def perform_destroy(self, instance):
        # PROD-007: deactivate instead of hard delete
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        self._log(instance, "product_deactivated")

    # ---------- Prices ----------
    @extend_schema(methods=["GET"], responses=ProductPriceSerializer(many=True))
    @extend_schema(methods=["POST"], request=ProductPriceCreateSerializer,
                   responses={201: ProductPriceSerializer})
    # Price history is short and returned whole (not paginated).
    @action(detail=True, methods=["get", "post"], url_path="prices", pagination_class=None)
    def prices(self, request, pk=None):
        """
        GET  /api/products/{id}/prices/   → every price row, newest start first
        POST /api/products/{id}/prices/   → new price, now or scheduled
        """
        product = self.get_object()

        if request.method == "GET":
            rows = product.prices.select_related("created_by").order_by("-effective_from", "-id")
            return Response(ProductPriceSerializer(rows, many=True, context={"request": request}).data)

        payload = ProductPriceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        effective_from = data.get("effective_from") or timezone.now()

        new_price = ProductPrice.objects.create(
            product=product,
            selling_price=data["selling_price"],
            cost_price=data.get("cost_price", 0),
            effective_from=effective_from,
            reason=data.get("reason", ""),
            created_by=request.user,
        )
        self._log(
            product, "product_price_changed",
            new_price_id=str(new_price.pk),
            selling_price=str(new_price.selling_price),
            effective_from=effective_from.isoformat(),
            reason=new_price.reason,
        )
        return Response(ProductPriceSerializer(new_price, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: ProductPriceSerializer, 404: DetailSerializer})
    @action(detail=True, methods=["get"], url_path="current-price")
    def current_price(self, request, pk=None):
        product = self.get_object()
        row = product.current_price()
        if not row:
            return Response({"detail": "No price is set for this product."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(ProductPriceSerializer(row, context={"request": request}).data)

    # ---------- Branch availability (PROD-008) ----------
    def _availability(self, product):
        user = self.request.user
        branches = Branch.objects.filter(business_id=user.business_id, is_active=True)
        allowed = accessible_branch_ids(user)
        if allowed is not None:
            branches = branches.filter(pk__in=allowed)
        hidden = set(product.branch_availabilities.filter(is_available=False)
                     .values_list("branch_id", flat=True))
        may_edit = user.role in AVAILABILITY_MANAGERS
        rows = [
            {"branch": b.pk, "branch_name": b.name, "is_available": b.pk not in hidden,
             "can_edit": may_edit}
            for b in branches.order_by("name")
        ]
        return ProductBranchAvailabilitySerializer(rows, many=True).data

    @extend_schema(methods=["GET"], responses=ProductBranchAvailabilitySerializer(many=True))
    @extend_schema(methods=["PUT"], request=ProductAvailabilityUpdateSerializer,
                   responses=ProductBranchAvailabilitySerializer(many=True))
    @action(detail=True, methods=["get", "put"], url_path="availability", pagination_class=None)
    def availability(self, request, pk=None):
        """
        Where this product is sold. A branch with no setting sells it.
        GET lists the active branches you can see; PUT switches branches on/off
        (branch managers and inventory managers: their own branches only).
        """
        product = self.get_object()
        if request.method == "GET":
            return Response(self._availability(product))

        payload = ProductAvailabilityUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        changes = payload.validated_data["branches"]
        branches = {b.pk: b for b in Branch.objects.filter(
            business_id=request.user.business_id, pk__in=[c["branch"] for c in changes])}
        for change in changes:
            branch = branches.get(change["branch"])
            if branch is None:
                raise ValidationError({"branches": f"Unknown branch {change['branch']}."})
            if not can_access_branch(request.user, branch):
                raise PermissionDenied(f"You do not have access to {branch.name}.")

        with transaction.atomic():
            for change in changes:
                BranchProductAvailability.objects.update_or_create(
                    branch_id=change["branch"], product=product,
                    defaults={"is_available": change["is_available"]},
                )
        self._log(product, "product_availability_changed",
                  branches={str(c["branch"]): c["is_available"] for c in changes})
        return Response(self._availability(product))


# ------------------------------------------------------------------------------
# Branch product availability (raw rows; the product `availability` action is
# what the app uses)
# ------------------------------------------------------------------------------
class BranchProductAvailabilityViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = BranchProductAvailability.objects.none()  # schema only; real queryset in get_queryset
    serializer_class = BranchProductAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = BranchProductAvailability.objects.filter(
            branch__business_id=self.request.user.business_id
        ).select_related("branch", "product").order_by("branch__name", "product__name")

        branch_id = self.request.query_params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)

        return scope_to_branches(qs, self.request.user)

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        self.allowed_roles = AVAILABILITY_MANAGERS
        return [HasAnyRole()]
