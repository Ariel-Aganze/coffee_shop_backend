from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.branches import branch_comparison
from .services.cash import cash_reconciliation_report
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
    sales_summary,
)


class BaseReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(self.run(request))

    def run(self, request):
        raise NotImplementedError


class SalesSummaryView(BaseReportView):
    def run(self, request):
        return sales_summary(request)


class SalesByBranchView(BaseReportView):
    def run(self, request):
        return sales_by_branch(request)


class SalesByProductView(BaseReportView):
    def run(self, request):
        return sales_by_product(request)


class SalesByCashierView(BaseReportView):
    def run(self, request):
        return sales_by_cashier(request)


class SalesByPaymentMethodView(BaseReportView):
    def run(self, request):
        return sales_by_payment_method(request)


class CurrentStockView(BaseReportView):
    def run(self, request):
        return current_stock(request)


class LowStockView(BaseReportView):
    def run(self, request):
        return low_stock(request)


class StockMovementReportView(BaseReportView):
    def run(self, request):
        return stock_movement_report(request)


class ExpensesReportView(BaseReportView):
    def run(self, request):
        return expenses_report(request)


class FinancialSummaryView(BaseReportView):
    def run(self, request):
        return financial_summary(request)


class CashReconciliationReportView(BaseReportView):
    def run(self, request):
        return cash_reconciliation_report(request)


class BranchComparisonView(BaseReportView):
    def run(self, request):
        return branch_comparison(request)