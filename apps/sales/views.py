from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied

from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.branches.models import Branch
from apps.payments.serializers import (
    CancelSerializer,
    PaymentsBulkCreateSerializer,
    RefundCreateSerializer,
)
from apps.payments.services import (
    add_payments_to_sale,
    cancel_sale,
    refund_sale,
)

from .models import Sale
from .serializers import SaleCreateSerializer, SaleReadSerializer
from .services import create_sale


class SaleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "create":
            return SaleCreateSerializer
        return SaleReadSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "receipt"):
            return [IsAuthenticated()]
        self.allowed_roles = (
            UserRole.OWNER, UserRole.BRANCH_MANAGER, UserRole.CASHIER,
        )
        return [HasAnyRole()]

    def get_queryset(self):
        user = self.request.user
        qs = Sale.objects.filter(
            business_id=user.business_id
        ).select_related("branch", "cashier").prefetch_related(
            "items", "payments"
        )

        if user.role != UserRole.OWNER:
            qs = qs.filter(branch_id__in=user.assigned_branch_ids)

        params = self.request.query_params
        branch_id = params.get("branch")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        date_from = params.get("from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = params.get("to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        cashier_id = params.get("cashier")
        if cashier_id:
            qs = qs.filter(cashier_id=cashier_id)
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(transaction_number__icontains=search)

        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            branch = Branch.objects.get(pk=data["branch"])
        except Branch.DoesNotExist:
            raise ValidationError("Branch not found.")

        items_payload = [
            {
                "product_id": item["product_id"],
                "quantity": str(item["quantity"]),
                "discount_amount": str(item.get("discount_amount", "0")),
            }
            for item in data["items"]
        ]

        idempotency_key = request.headers.get("X-Idempotency-Key")

        sale, replayed = create_sale(
            user=request.user,
            branch=branch,
            items_payload=items_payload,
            idempotency_key=idempotency_key,
            request_payload_for_hash=request.data,
            note=data.get("note", ""),
        )

        response_data = SaleReadSerializer(sale).data
        http_status = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
        return Response(response_data, status=http_status)

    # ---------------- Payments ----------------
    @action(detail=True, methods=["post"], url_path="payments", url_name="sale-payments")
    def payments(self, request, pk=None):
        sale = self.get_object()
        payload = PaymentsBulkCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        add_payments_to_sale(
            sale=sale,
            payments_payload=payload.validated_data["payments"],
            user=request.user,
        )

        sale.refresh_from_db()
        return Response(
            SaleReadSerializer(sale).data,
            status=status.HTTP_201_CREATED,
        )

    # ---------------- Refund ----------------
    @action(detail=True, methods=["post"], url_path="refund", url_name="sale-refund")
    def refund(self, request, pk=None):
        if request.user.role not in (UserRole.OWNER, UserRole.BRANCH_MANAGER):
            raise PermissionDenied("Only owners and branch managers can refund sales.")
        sale = self.get_object()
        payload = RefundCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        refund_sale(
            sale=sale,
            user=request.user,
            amount=payload.validated_data.get("amount"),
            reason=payload.validated_data.get("reason", ""),
        )
        sale.refresh_from_db()
        return Response(SaleReadSerializer(sale).data)

    # ---------------- Cancel ----------------
    @action(detail=True, methods=["post"], url_path="cancel", url_name="sale-cancel")
    def cancel(self, request, pk=None):
        if request.user.role not in (UserRole.OWNER, UserRole.BRANCH_MANAGER):
            raise PermissionDenied("Only owners and branch managers can cancel sales.")
        sale = self.get_object()
        payload = CancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        cancel_sale(
            sale=sale,
            user=request.user,
            reason=payload.validated_data.get("reason", ""),
        )
        sale.refresh_from_db()
        return Response(SaleReadSerializer(sale).data)

    # ---------------- Receipt ----------------
    @action(detail=True, methods=["get"], url_path="receipt", url_name="sale-receipt")
    def receipt(self, request, pk=None):
        sale = self.get_object()
        return Response(self._build_receipt(sale))

    @staticmethod
    def _build_receipt(sale: Sale) -> dict:
        return {
            "business": sale.business.name,
            "branch": sale.branch.name,
            "transaction_number": sale.transaction_number,
            "datetime": sale.completed_at.isoformat() if sale.completed_at else None,
            "cashier": sale.cashier.get_full_name() or sale.cashier.username,
            "items": [
                {
                    "name": item.product_name,
                    "sku": item.sku,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "discount": str(item.discount_amount),
                    "line_total": str(item.line_total),
                }
                for item in sale.items.all()
            ],
            "subtotal": str(sale.subtotal),
            "discount_total": str(sale.discount_total),
            "total_amount": str(sale.total_amount),
            "currency_code": sale.currency_code,
            "payments": [
                {
                    "method": p.get_method_display(),
                    "amount": str(p.amount),
                    "reference": p.reference,
                    "is_refund": p.is_refund,
                }
                for p in sale.payments.all()
            ],
            "status": sale.get_status_display(),
            "note": sale.note,
        }