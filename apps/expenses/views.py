from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from apps.accounts.constants import UserRole
from apps.accounts.permissions import IsOwner
from apps.branches.models import Branch

from .models import Expense, ExpenseCategory
from .serializers import (
    ExpenseCategorySerializer,
    ExpenseCreateSerializer,
    ExpenseReviewSerializer,
    ExpenseSerializer,
)
from .services import approve_expense, create_expense, reject_expense
from django.db import models
from rest_framework import mixins, status, viewsets


class ExpenseCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseCategorySerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsOwner()]

    def get_queryset(self):
        return ExpenseCategory.objects.filter(
            business_id=self.request.user.business_id
        ).order_by("name")

    def perform_create(self, serializer):
        serializer.save(business=self.request.user.business)


class ExpenseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return ExpenseCreateSerializer
        return ExpenseSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Expense.objects.filter(
            business_id=user.business_id
        ).select_related("category", "branch", "submitted_by", "reviewed_by")

        if user.role != UserRole.OWNER:
            # Non-owners see expenses of their branches plus central ones
            # they submitted.
            qs = qs.filter(
                models.Q(branch_id__in=user.assigned_branch_ids)
                | models.Q(branch__isnull=True, submitted_by=user)
            )

        params = self.request.query_params
        branch_id = params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        category_id = params.get("category")
        if category_id:
            qs = qs.filter(category_id=category_id)
        date_from = params.get("from")
        if date_from:
            qs = qs.filter(expense_date__gte=date_from)
        date_to = params.get("to")
        if date_to:
            qs = qs.filter(expense_date__lte=date_to)

        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER,
            UserRole.INVENTORY_MANAGER, UserRole.ACCOUNTANT,
        ):
            raise PermissionDenied("Your role cannot create expenses.")

        payload = ExpenseCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            category = ExpenseCategory.objects.get(
                pk=payload.validated_data["category"],
                business_id=request.user.business_id,
            )
        except ExpenseCategory.DoesNotExist:
            raise ValidationError("Category not found.")

        branch = None
        branch_id = payload.validated_data.get("branch")
        if branch_id:
            try:
                branch = Branch.objects.get(pk=branch_id)
            except Branch.DoesNotExist:
                raise ValidationError("Branch not found.")

        expense = create_expense(
            user=request.user,
            category=category,
            amount=payload.validated_data["amount"],
            description=payload.validated_data["description"],
            branch=branch,
            expense_date=payload.validated_data.get("expense_date"),
            attachment=payload.validated_data.get("attachment"),
        )
        return Response(ExpenseSerializer(expense).data,
                        status=status.HTTP_201_CREATED)

    # ---------- Approve ----------
    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        if request.user.role not in (UserRole.OWNER, UserRole.BRANCH_MANAGER):
            raise PermissionDenied("Only owners and branch managers can approve.")
        expense = self.get_object()
        payload = ExpenseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        approve_expense(
            expense=expense, user=request.user,
            note=payload.validated_data.get("note", ""),
        )
        expense.refresh_from_db()
        return Response(ExpenseSerializer(expense).data)

    # ---------- Reject ----------
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        if request.user.role not in (UserRole.OWNER, UserRole.BRANCH_MANAGER):
            raise PermissionDenied("Only owners and branch managers can reject.")
        expense = self.get_object()
        payload = ExpenseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        reject_expense(
            expense=expense, user=request.user,
            note=payload.validated_data.get("note", ""),
        )
        expense.refresh_from_db()
        return Response(ExpenseSerializer(expense).data)