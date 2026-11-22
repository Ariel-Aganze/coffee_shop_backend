from decimal import Decimal

from django.db.models import Count, Q, Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.access import accessible_branch_ids
from apps.accounts.constants import UserRole
from apps.accounts.permissions import IsOwner
from apps.audit.services import log_action
from apps.branches.models import Branch

from .models import Expense, ExpenseCategory, ExpenseStatus
from .serializers import (
    ExpenseCategorySerializer,
    ExpenseCreateSerializer,
    ExpenseReviewSerializer,
    ExpenseSerializer,
    ExpenseSummarySerializer,
    ExpenseUpdateSerializer,
)
from .services import approve_expense, create_expense, reject_expense, update_expense

EXPENSE_RECORDERS = (
    UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.INVENTORY_MANAGER, UserRole.ACCOUNTANT,
)
EXPENSE_APPROVERS = (UserRole.OWNER, UserRole.BRANCH_MANAGER)


# ------------------------------------------------------------------------------
# Categories (EXP-001)
# ------------------------------------------------------------------------------
@extend_schema_view(list=extend_schema(parameters=[
    OpenApiParameter("search", str, description="Name contains."),
    OpenApiParameter("is_active", bool),
]))
class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    """DELETE deactivates: expenses already filed keep their category."""
    queryset = ExpenseCategory.objects.none()  # schema only; real queryset in get_queryset
    serializer_class = ExpenseCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsOwner()]

    def get_queryset(self):
        qs = ExpenseCategory.objects.filter(
            business_id=self.request.user.business_id
        ).annotate(num_expenses=Count("expenses"))
        if self.action == "list":
            params = self.request.query_params
            if params.get("is_active") not in (None, ""):
                qs = qs.filter(is_active=params["is_active"].lower() == "true")
            if params.get("search", "").strip():
                qs = qs.filter(name__icontains=params["search"].strip())
        return qs.order_by("-is_active", "name")

    def _log(self, category, action):
        log_action(
            business=self.request.user.business, user=self.request.user, action=action,
            target_type="ExpenseCategory", target_id=str(category.pk),
            metadata={"name": category.name},
        )

    def perform_create(self, serializer):
        self._log(serializer.save(business=self.request.user.business), "expense_category_created")

    def perform_update(self, serializer):
        self._log(serializer.save(), "expense_category_updated")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        self._log(instance, "expense_category_deactivated")


# ------------------------------------------------------------------------------
# Expenses (EXP-002..EXP-011)
# ------------------------------------------------------------------------------
LIST_PARAMETERS = [
    OpenApiParameter("branch", int),
    OpenApiParameter("status", str, enum=ExpenseStatus.values),
    OpenApiParameter("category", int),
    OpenApiParameter("from", OpenApiTypes.DATE, description="Expense date on or after."),
    OpenApiParameter("to", OpenApiTypes.DATE, description="Expense date on or before."),
    OpenApiParameter("search", str, description="Description contains."),
    OpenApiParameter("mine", bool, description="Only expenses I filed."),
]


@extend_schema_view(list=extend_schema(parameters=LIST_PARAMETERS))
class ExpenseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Expense.objects.none()  # schema only; real queryset in get_queryset
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return ExpenseCreateSerializer
        if self.action == "partial_update":
            return ExpenseUpdateSerializer
        return ExpenseSerializer

    def _visible(self):
        user = self.request.user
        qs = Expense.objects.filter(business_id=user.business_id).select_related(
            "category", "branch", "submitted_by", "reviewed_by",
        )
        allowed = accessible_branch_ids(user)
        if allowed is not None:
            # Branch-scoped users see their branches' expenses plus central
            # ones they filed themselves.
            qs = qs.filter(Q(branch_id__in=allowed) | Q(branch__isnull=True, submitted_by=user))
        return qs

    def get_queryset(self):
        qs = self._visible()
        if self.action not in ("list", "summary"):
            return qs
        params = self.request.query_params
        if params.get("branch"):
            qs = qs.filter(branch_id=params["branch"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("category"):
            qs = qs.filter(category_id=params["category"])
        if params.get("from"):
            qs = qs.filter(expense_date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(expense_date__lte=params["to"])
        if params.get("search", "").strip():
            qs = qs.filter(description__icontains=params["search"].strip())
        if (params.get("mine") or "").lower() == "true":
            qs = qs.filter(submitted_by=self.request.user)
        return qs.order_by("-expense_date", "-created_at")

    def _read(self, expense):
        return ExpenseSerializer(self._visible().get(pk=expense.pk),
                                 context=self.get_serializer_context()).data

    @extend_schema(
        request={"multipart/form-data": ExpenseCreateSerializer,
                 "application/json": ExpenseCreateSerializer},
        responses={201: ExpenseSerializer},
    )
    def create(self, request, *args, **kwargs):
        if request.user.role not in EXPENSE_RECORDERS:
            raise PermissionDenied("Your role cannot record expenses.")
        payload = ExpenseCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        category = ExpenseCategory.objects.filter(
            pk=data["category"], business_id=request.user.business_id).first()
        if category is None:
            raise ValidationError({"category": "Category not found."})
        branch = None
        if data.get("branch"):
            branch = Branch.objects.filter(pk=data["branch"]).first()
            if branch is None:
                raise ValidationError({"branch": "Branch not found."})

        expense = create_expense(
            user=request.user,
            category=category,
            amount=data["amount"],
            description=data["description"],
            branch=branch,
            expense_date=data.get("expense_date"),
            attachment=data.get("attachment"),
        )
        return Response(self._read(expense), status=status.HTTP_201_CREATED)

    @extend_schema(
        request={"multipart/form-data": ExpenseUpdateSerializer,
                 "application/json": ExpenseUpdateSerializer},
        responses=ExpenseSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        expense = self.get_object()
        payload = ExpenseUpdateSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        update_expense(expense=expense, user=request.user, changes=payload.validated_data)
        return Response(self._read(expense))

    def update(self, request, *args, **kwargs):
        if not kwargs.get("partial"):
            return self.http_method_not_allowed(request, *args, **kwargs)
        return super().update(request, *args, **kwargs)

    # ---------- Totals for the current filters ----------
    @extend_schema(parameters=LIST_PARAMETERS, responses=ExpenseSummarySerializer)
    @action(detail=False, methods=["get"], url_path="summary", pagination_class=None)
    def summary(self, request):
        totals = self.get_queryset().order_by().aggregate(
            approved_total=Sum("amount", filter=Q(status=ExpenseStatus.APPROVED)),
            approved_count=Count("id", filter=Q(status=ExpenseStatus.APPROVED)),
            pending_total=Sum("amount", filter=Q(status=ExpenseStatus.PENDING)),
            pending_count=Count("id", filter=Q(status=ExpenseStatus.PENDING)),
            rejected_count=Count("id", filter=Q(status=ExpenseStatus.REJECTED)),
        )
        for key in ("approved_total", "pending_total"):
            totals[key] = totals[key] or Decimal("0")
        return Response(ExpenseSummarySerializer(totals).data)

    # ---------- Approve / reject (EXP-006) ----------
    @extend_schema(request=ExpenseReviewSerializer, responses=ExpenseSerializer)
    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        if request.user.role not in EXPENSE_APPROVERS:
            raise PermissionDenied("Only owners and branch managers can approve.")
        expense = self.get_object()
        payload = ExpenseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        approve_expense(expense=expense, user=request.user,
                        note=payload.validated_data.get("note", ""))
        return Response(self._read(expense))

    @extend_schema(request=ExpenseReviewSerializer, responses=ExpenseSerializer)
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        if request.user.role not in EXPENSE_APPROVERS:
            raise PermissionDenied("Only owners and branch managers can reject.")
        expense = self.get_object()
        payload = ExpenseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reject_expense(expense=expense, user=request.user,
                       note=payload.validated_data.get("note", ""))
        return Response(self._read(expense))
