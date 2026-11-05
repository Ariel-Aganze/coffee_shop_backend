from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.branches.models import Branch

from .models import CashSession
from .serializers import (
    CashMovementCreateSerializer,
    CashSessionSerializer,
    CloseSessionSerializer,
    OpenSessionSerializer,
)
from .services import (
    add_cash_movement,
    close_cash_session,
    compute_expected_cash,
    open_cash_session,
)


class CashSessionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = CashSessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = CashSession.objects.filter(
            branch__business_id=user.business_id
        ).select_related("branch", "cashier").prefetch_related("movements")

        if user.role != UserRole.OWNER:
            qs = qs.filter(branch_id__in=user.assigned_branch_ids)

        params = self.request.query_params
        branch_id = params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        cashier_id = params.get("cashier")
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)

        return qs.order_by("-opened_at")

    # ---------- Open ----------
    @action(detail=False, methods=["post"], url_path="open")
    def open_session(self, request):
        if request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.CASHIER,
        ):
            raise PermissionDenied("Your role cannot open a cash session.")
        payload = OpenSessionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            branch = Branch.objects.get(pk=payload.validated_data["branch"])
        except Branch.DoesNotExist:
            raise ValidationError("Branch not found.")

        session = open_cash_session(
            branch=branch,
            cashier=request.user,
            opening_balance=payload.validated_data["opening_balance"],
        )
        return Response(CashSessionSerializer(session).data,
                        status=status.HTTP_201_CREATED)

    # ---------- Close ----------
    @action(detail=True, methods=["post"], url_path="close")
    def close_session(self, request, pk=None):
        session = self.get_object()
        if session.status == "closed":
            raise ValidationError("This session is already closed.")
        if request.user.id != session.cashier_id and request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER,
        ):
            raise PermissionDenied("Only the cashier, a manager, or the owner can close this session.")

        payload = CloseSessionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        close_cash_session(
            session=session,
            actual_cash=payload.validated_data["actual_cash"],
            explanation=payload.validated_data.get("explanation", ""),
            closed_by=request.user,
        )
        session.refresh_from_db()
        return Response(CashSessionSerializer(session).data)

    # ---------- Live reconciliation preview ----------
    @action(detail=True, methods=["get"], url_path="reconciliation")
    def reconciliation(self, request, pk=None):
        session = self.get_object()
        return Response(compute_expected_cash(session))

    # ---------- Manual movement ----------
    @action(detail=True, methods=["post"], url_path="movements")
    def add_movement(self, request, pk=None):
        session = self.get_object()
        if session.status != "open":
            raise ValidationError("Cannot add movements to a closed session.")
        if request.user.id != session.cashier_id and request.user.role not in (
            UserRole.OWNER, UserRole.BRANCH_MANAGER,
        ):
            raise PermissionDenied("Only the cashier, a manager, or the owner can add movements.")

        payload = CashMovementCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        add_cash_movement(
            session=session,
            movement_type=payload.validated_data["movement_type"],
            amount=payload.validated_data["amount"],
            reason=payload.validated_data["reason"],
            reference=payload.validated_data.get("reference", ""),
            performed_by=request.user,
        )
        session.refresh_from_db()
        return Response(CashSessionSerializer(session).data,
                        status=status.HTTP_201_CREATED)