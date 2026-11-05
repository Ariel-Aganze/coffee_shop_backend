from django.urls import path

from .views import (
    BranchComparisonView,
    CashReconciliationReportView,
    CurrentStockView,
    ExpensesReportView,
    FinancialSummaryView,
    LowStockView,
    SalesByBranchView,
    SalesByCashierView,
    SalesByPaymentMethodView,
    SalesByProductView,
    SalesSummaryView,
    StockMovementReportView,
)

urlpatterns = [
    path("sales/summary/", SalesSummaryView.as_view(), name="rep-sales-summary"),
    path("sales/by-branch/", SalesByBranchView.as_view(), name="rep-sales-branch"),
    path("sales/by-product/", SalesByProductView.as_view(), name="rep-sales-product"),
    path("sales/by-cashier/", SalesByCashierView.as_view(), name="rep-sales-cashier"),
    path("sales/by-payment-method/", SalesByPaymentMethodView.as_view(), name="rep-sales-payment"),
    path("inventory/current-stock/", CurrentStockView.as_view(), name="rep-inv-stock"),
    path("inventory/low-stock/", LowStockView.as_view(), name="rep-inv-low"),
    path("inventory/movements/", StockMovementReportView.as_view(), name="rep-inv-movements"),
    path("expenses/", ExpensesReportView.as_view(), name="rep-expenses"),
    path("financial/summary/", FinancialSummaryView.as_view(), name="rep-financial"),
    path("cash/reconciliation/", CashReconciliationReportView.as_view(), name="rep-cash"),
    path("branches/compare/", BranchComparisonView.as_view(), name="rep-branch-compare"),
]