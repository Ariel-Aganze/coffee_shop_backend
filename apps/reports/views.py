"""
Report endpoints.

Every report:
  - is restricted to the roles allowed to see it (RBAC 4.2, below);
  - is scoped to the user's branches, optionally narrowed with `?branch=`;
  - uses the business timezone for day boundaries (NFR-040);
  - can be exported with `?export=csv` or `?export=pdf` (REP-016/017).
    (`format` is reserved by DRF for content negotiation, hence `export`.)
"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.constants import UserRole
from apps.accounts.permissions import HasAnyRole
from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.settings.models import BusinessSettings
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from . import schemas as s
from .exporters import EXPORT_FORMATS, to_csv, to_pdf
from .services.branches import branch_comparison
from .services.cash import cash_reconciliation_report
from .services.common import branch_scope
from .services.dashboard import dashboard
from .services.expenses import expenses_report
from .services.financial import financial_summary
from .services.inventory import (
    current_stock,
    low_stock,
    stock_movement_report,
)
from .services.sales import (
    sales_by_branch,
    sales_by_cashier,
    sales_by_payment_method,
    sales_by_product,
    sales_over_time,
    sales_summary,
)

# ------------------------------------------------------------------------------
# Who may see what (RBAC 4.2). Branch scoping is applied on top of this.
# ------------------------------------------------------------------------------
MANAGEMENT = (UserRole.OWNER, UserRole.ADMIN, UserRole.BRANCH_MANAGER, UserRole.ACCOUNTANT)
# Cashiers get a "limited" view: sales reports restricted to their own sales.
SALES_ROLES = MANAGEMENT + (UserRole.CASHIER,)
# Inventory managers get a "limited" view: inventory reports only.
INVENTORY_ROLES = MANAGEMENT + (UserRole.INVENTORY_MANAGER,)
FINANCIAL_ROLES = MANAGEMENT


class BaseReportView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles: tuple = FINANCIAL_ROLES
    title = "Report"
    slug = "report"
    exportable = True

    def get(self, request):
        settings = BusinessSettings.objects.filter(
            business_id=request.user.business_id,
        ).select_related("business").first()
        tz = _zone(settings.timezone if settings else "UTC")

        timezone.activate(tz)
        try:
            data = self.run(request)
            export = request.query_params.get("export")
            if not export:
                return Response(data)
            return self._export(request, data, export, settings)
        finally:
            timezone.deactivate()

    def _export(self, request, data, fmt, settings):
        if not self.exportable:
            raise ValidationError("This endpoint cannot be exported.")
        if fmt not in EXPORT_FORMATS:
            raise ValidationError(f"`export` must be one of: {', '.join(EXPORT_FORMATS)}.")

        log_action(
            business=request.user.business, user=request.user,
            action="report_exported", target_type="Report", target_id=self.slug,
            metadata={"format": fmt, "params": dict(request.query_params.items())},
        )
        exporter = to_csv if fmt == "csv" else to_pdf
        return exporter(
            title=self.title, slug=self.slug,
            business_name=settings.business.name if settings else "",
            scope_label=_scope_label(request), data=data,
        )

    def run(self, request):
        raise NotImplementedError


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _scope_label(request) -> str:
    scope = branch_scope(request)
    if scope is None:
        return "All branches"
    names = Branch.objects.filter(id__in=scope).order_by("name").values_list("name", flat=True)
    return ", ".join(names) or "None"


# ------------------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------------------
@extend_schema_view(get=extend_schema(
    parameters=[
        OpenApiParameter("from", str, description="Start date (YYYY-MM-DD). Default: first day of the current month."),
        OpenApiParameter("to", str, description="End date (YYYY-MM-DD). Default: today."),
        s.BRANCH,
    ],
    responses=s.DashboardSerializer,
))
class DashboardView(BaseReportView):
    allowed_roles = FINANCIAL_ROLES
    title = "Dashboard"
    slug = "dashboard"
    exportable = False  # nested, screen-oriented payload; export the reports instead

    def run(self, request):
        return dashboard(request)


# ------------------------------------------------------------------------------
# Sales
# ------------------------------------------------------------------------------
@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.SalesSummaryReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesSummaryView(BaseReportView):
    allowed_roles = SALES_ROLES
    title, slug = "Sales summary", "sales-summary"

    def run(self, request):
        return sales_summary(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS + [s.SALES_GROUP],
    responses={200: OpenApiResponse(s.SalesOverTimeReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesOverTimeView(BaseReportView):
    allowed_roles = SALES_ROLES
    title, slug = "Sales over time", "sales-over-time"

    def run(self, request):
        return sales_over_time(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.SalesByBranchReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesByBranchView(BaseReportView):
    allowed_roles = SALES_ROLES
    title, slug = "Sales by branch", "sales-by-branch"

    def run(self, request):
        return sales_by_branch(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.SalesByProductReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesByProductView(BaseReportView):
    allowed_roles = SALES_ROLES
    title, slug = "Sales by product", "sales-by-product"

    def run(self, request):
        return sales_by_product(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.SalesByCashierReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesByCashierView(BaseReportView):
    allowed_roles = FINANCIAL_ROLES  # compares employees — not for cashiers
    title, slug = "Sales by cashier", "sales-by-cashier"

    def run(self, request):
        return sales_by_cashier(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.SalesByPaymentMethodReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class SalesByPaymentMethodView(BaseReportView):
    allowed_roles = SALES_ROLES
    title, slug = "Sales by payment method", "sales-by-payment-method"

    def run(self, request):
        return sales_by_payment_method(request)


# ------------------------------------------------------------------------------
# Inventory
# ------------------------------------------------------------------------------
@extend_schema_view(get=extend_schema(
    parameters=[s.BRANCH, s.EXPORT],
    responses={200: OpenApiResponse(s.CurrentStockReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class CurrentStockView(BaseReportView):
    allowed_roles = INVENTORY_ROLES
    title, slug = "Current stock", "current-stock"

    def run(self, request):
        return current_stock(request)


@extend_schema_view(get=extend_schema(
    parameters=[s.BRANCH, s.EXPORT],
    responses={200: OpenApiResponse(s.LowStockReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class LowStockView(BaseReportView):
    allowed_roles = INVENTORY_ROLES
    title, slug = "Low stock", "low-stock"

    def run(self, request):
        return low_stock(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS + [OpenApiParameter("product", int), OpenApiParameter("type", str, description="Movement type(s), comma-separated, e.g. damaged,expired.")],
    responses={200: OpenApiResponse(s.StockMovementReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class StockMovementReportView(BaseReportView):
    allowed_roles = INVENTORY_ROLES
    title, slug = "Stock movements", "stock-movements"

    def run(self, request):
        return stock_movement_report(request)


# ------------------------------------------------------------------------------
# Financial
# ------------------------------------------------------------------------------
@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS + [s.STATUS, s.EXPENSE_GROUP],
    responses={200: OpenApiResponse(s.ExpensesReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class ExpensesReportView(BaseReportView):
    title, slug = "Expenses", "expenses"

    def run(self, request):
        return expenses_report(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS,
    responses={200: OpenApiResponse(s.FinancialSummaryReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class FinancialSummaryView(BaseReportView):
    title, slug = "Financial summary", "financial-summary"

    def run(self, request):
        return financial_summary(request)


@extend_schema_view(get=extend_schema(
    parameters=s.REPORT_PARAMS + [s.STATUS],
    responses={200: OpenApiResponse(s.CashReconciliationReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class CashReconciliationReportView(BaseReportView):
    title, slug = "Cash reconciliation", "cash-reconciliation"

    def run(self, request):
        return cash_reconciliation_report(request)


@extend_schema_view(get=extend_schema(
    parameters=[s.FROM, s.TO, s.EXPORT],
    responses={200: OpenApiResponse(s.BranchComparisonReport, description="JSON, or a CSV/PDF file when ?export= is set.")},
))
class BranchComparisonView(BaseReportView):
    title, slug = "Branch comparison", "branch-comparison"

    def run(self, request):
        return branch_comparison(request)
