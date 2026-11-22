"""
Response shapes of the report endpoints, for the OpenAPI schema (and so the
frontend gets generated types). They mirror the dicts built in
apps/reports/services/*; the API test suite checks they stay in sync.

Money and quantities are decimal strings, as everywhere else in the API.
"""
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers


def Money(**kw):
    return serializers.DecimalField(max_digits=18, decimal_places=4, **kw)


def Qty(**kw):
    return serializers.DecimalField(max_digits=18, decimal_places=4, **kw)


class PeriodSerializer(serializers.Serializer):
    # Named `from` in JSON; Python can't use it as an attribute name.
    from_ = serializers.DateField()
    to = serializers.DateField()

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = fields.pop("from_")
        return fields


# ------------------------------------------------------------------------------
# Common query parameters
# ------------------------------------------------------------------------------
FROM = OpenApiParameter("from", str, description="Start date (YYYY-MM-DD), inclusive, business timezone.")
TO = OpenApiParameter("to", str, description="End date (YYYY-MM-DD), inclusive. Defaults: from=to=today.")
BRANCH = OpenApiParameter("branch", int, description="Limit to one branch (403 if not yours).")
EXPORT = OpenApiParameter("export", str, enum=["csv", "pdf"],
                          description="Download the report as CSV or PDF instead of JSON.")
STATUS = OpenApiParameter("status", str, description="Filter by status.")
REPORT_PARAMS = [FROM, TO, BRANCH, EXPORT]
SALES_GROUP = OpenApiParameter("group", str, enum=["day", "week", "month"],
                               description="Bucket size. Default: day.")
EXPENSE_GROUP = OpenApiParameter("group", str, enum=["category", "branch", "month"],
                                 description="Group rows by. Default: category.")


# ------------------------------------------------------------------------------
# Sales
# ------------------------------------------------------------------------------
class SalesSummaryRow(serializers.Serializer):
    currency_code = serializers.CharField()
    currency_decimal_places = serializers.IntegerField()
    total_sales = Money(help_text="Fully paid sales, before refunds.")
    refunds_total = Money()
    net_sales = Money(help_text="total_sales − refunds_total.")
    transactions = serializers.IntegerField()
    average_transaction_value = Money()
    unpaid_sales_count = serializers.IntegerField()
    unpaid_sales_total = Money(help_text="Not revenue: sales not (fully) paid yet.")


class SalesSummaryReport(serializers.Serializer):
    period = PeriodSerializer()
    results = SalesSummaryRow(many=True)


class SalesOverTimeRow(serializers.Serializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    currency_code = serializers.CharField()
    total_sales = Money()
    refunds_total = Money()
    net_sales = Money()
    transactions = serializers.IntegerField()
    average_transaction_value = Money()


class SalesOverTimeReport(serializers.Serializer):
    period = PeriodSerializer()
    group = serializers.ChoiceField(choices=["day", "week", "month"])
    results = SalesOverTimeRow(many=True)


class SalesByBranchRow(serializers.Serializer):
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    currency_code = serializers.CharField()
    total_sales = Money()
    refunds_total = Money()
    net_sales = Money()
    transactions = serializers.IntegerField()


class SalesByBranchReport(serializers.Serializer):
    period = PeriodSerializer()
    results = SalesByBranchRow(many=True)


class SalesByProductRow(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    currency_code = serializers.CharField()
    quantity_sold = Qty()
    revenue = Money()
    orders = serializers.IntegerField()


class SalesByProductReport(serializers.Serializer):
    period = PeriodSerializer()
    note = serializers.CharField()
    results = SalesByProductRow(many=True)


class SalesByCashierRow(serializers.Serializer):
    cashier_id = serializers.IntegerField()
    cashier_name = serializers.CharField()
    currency_code = serializers.CharField()
    total_sales = Money()
    refunds_total = Money()
    net_sales = Money()
    transactions = serializers.IntegerField()


class SalesByCashierReport(serializers.Serializer):
    period = PeriodSerializer()
    results = SalesByCashierRow(many=True)


class SalesByPaymentMethodRow(serializers.Serializer):
    method = serializers.CharField()
    currency_code = serializers.CharField()
    collected = Money()
    refunded = Money()
    net = Money()
    count = serializers.IntegerField()
    refund_count = serializers.IntegerField()


class SalesByPaymentMethodReport(serializers.Serializer):
    period = PeriodSerializer()
    results = SalesByPaymentMethodRow(many=True)


# ------------------------------------------------------------------------------
# Inventory
# ------------------------------------------------------------------------------
class StockRow(serializers.Serializer):
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    unit = serializers.CharField()
    quantity = Qty()
    minimum_threshold = Qty()


class CurrentStockRow(StockRow):
    is_low_stock = serializers.BooleanField()
    unit_cost = Money(allow_null=True, required=False,
                      help_text="Latest cost price; omitted for roles that can't see costs.")
    stock_value = Money(allow_null=True, required=False,
                        help_text="quantity × unit_cost; omitted for roles that can't see costs.")


class CurrentStockReport(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    results = CurrentStockRow(many=True)


class LowStockReport(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    results = StockRow(many=True)


class StockMovementRow(serializers.Serializer):
    id = serializers.IntegerField()
    branch_name = serializers.CharField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    unit = serializers.CharField()
    movement_type = serializers.CharField()
    delta = Qty()
    quantity_after = Qty()
    reference = serializers.CharField()
    reason = serializers.CharField()
    performed_by = serializers.CharField()
    created_at = serializers.DateTimeField()


class StockMovementReport(serializers.Serializer):
    period = PeriodSerializer()
    results = StockMovementRow(many=True)


# ------------------------------------------------------------------------------
# Financial
# ------------------------------------------------------------------------------
class ExpensesRow(serializers.Serializer):
    group = serializers.CharField(help_text="Category name, branch name or YYYY-MM, per `group`.")
    currency_code = serializers.CharField()
    status = serializers.CharField()
    total = Money()
    count = serializers.IntegerField()


class CurrencyTotal(serializers.Serializer):
    currency_code = serializers.CharField()
    total = Money()


class ExpensesReport(serializers.Serializer):
    period = PeriodSerializer()
    group = serializers.ChoiceField(choices=["category", "branch", "month"])
    results = ExpensesRow(many=True)
    approved_totals = CurrencyTotal(many=True)


class FinancialRow(serializers.Serializer):
    currency_code = serializers.CharField()
    gross_sales = Money()
    refunds = Money()
    revenue = Money(help_text="Net revenue: gross_sales − refunds.")
    cost_of_goods_sold = Money()
    gross_profit = Money()
    operating_expenses = Money()
    operating_result = Money(help_text="Gross profit − operating expenses. Not net profit.")
    note = serializers.CharField()


class FinancialSummaryReport(serializers.Serializer):
    period = PeriodSerializer()
    results = FinancialRow(many=True)


class CashReconciliationRow(serializers.Serializer):
    session_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    cashier = serializers.CharField()
    status = serializers.CharField()
    opened_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)
    opening_balance = Money()
    expected_cash = Money(allow_null=True)
    actual_cash = Money(allow_null=True)
    difference = Money(allow_null=True)
    currency_code = serializers.CharField()
    discrepancy_explanation = serializers.CharField()


class CashReconciliationReport(serializers.Serializer):
    period = PeriodSerializer()
    results = CashReconciliationRow(many=True)


class BranchComparisonRow(serializers.Serializer):
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    currency_code = serializers.CharField()
    gross_sales = Money()
    refunds = Money()
    revenue = Money()
    transactions = serializers.IntegerField()
    average_transaction_value = Money()
    expenses = Money()


class BranchComparisonReport(serializers.Serializer):
    period = PeriodSerializer()
    results = BranchComparisonRow(many=True)


# ------------------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------------------
class _DashCurrency(serializers.Serializer):
    code = serializers.CharField()
    symbol = serializers.CharField()
    decimal_places = serializers.IntegerField()


class _DashScope(serializers.Serializer):
    branch_ids = serializers.ListField(child=serializers.IntegerField(), allow_null=True,
                                       help_text="null = all branches of the business.")
    timezone = serializers.CharField()


class _DashSalesFigures(serializers.Serializer):
    gross_sales = Money()
    refunds = Money()
    net_sales = Money()
    transactions = serializers.IntegerField()
    average_transaction_value = Money()


class _DashSalesWindow(_DashSalesFigures):
    from_ = serializers.DateField()
    to = serializers.DateField()

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = fields.pop("from_")
        return fields


class _DashHeadline(serializers.Serializer):
    today = _DashSalesWindow()
    this_week = _DashSalesWindow()
    this_month = _DashSalesWindow()


class _DashOverview(_DashSalesFigures):
    revenue = Money()
    cost_of_goods_sold = Money()
    gross_profit = Money()
    expenses = Money()
    operating_result = Money()
    notes = serializers.ListField(child=serializers.CharField())


class _DashBranch(serializers.Serializer):
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    net_sales = Money()
    transactions = serializers.IntegerField()
    average_transaction_value = Money()
    expenses = Money()


class _DashBranchPerformance(serializers.Serializer):
    branches = _DashBranch(many=True)
    best_branch = _DashBranch(allow_null=True)
    lowest_branch = _DashBranch(allow_null=True)


class _DashTrendDay(serializers.Serializer):
    date = serializers.DateField()
    net_sales = Money()
    transactions = serializers.IntegerField()


class _DashTrend(serializers.Serializer):
    truncated = serializers.BooleanField()
    days = _DashTrendDay(many=True)


class _DashProduct(serializers.Serializer):
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    sku = serializers.CharField()
    quantity_sold = Qty()
    revenue = Money()


class _DashProducts(serializers.Serializer):
    best_selling = _DashProduct(many=True)
    lowest_selling = _DashProduct(many=True)


class _DashLowStock(serializers.Serializer):
    branch_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    quantity = Qty()
    minimum_threshold = Qty()


class _DashMovement(serializers.Serializer):
    branch_name = serializers.CharField()
    product_name = serializers.CharField()
    movement_type = serializers.CharField()
    delta = Qty()
    performed_by = serializers.CharField()
    created_at = serializers.DateTimeField()


class _DashInventory(serializers.Serializer):
    low_stock_count = serializers.IntegerField()
    low_stock = _DashLowStock(many=True)
    stock_valuation = Money()
    stock_valuation_method = serializers.CharField()
    items_without_cost_price = serializers.IntegerField()
    recent_movements = _DashMovement(many=True)


class _DashSession(serializers.Serializer):
    session_id = serializers.IntegerField()
    branch_name = serializers.CharField()
    cashier = serializers.CharField()
    opened_at = serializers.DateTimeField()
    currency_code = serializers.CharField()
    expected_cash = Money()


class _DashTransaction(serializers.Serializer):
    sale_id = serializers.IntegerField()
    transaction_number = serializers.CharField()
    branch_name = serializers.CharField()
    cashier = serializers.CharField()
    total_amount = Money()
    currency_code = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class _DashPendingExpenses(serializers.Serializer):
    currency_code = serializers.CharField()
    count = serializers.IntegerField()
    total = Money()


class _DashPending(serializers.Serializer):
    expenses = _DashPendingExpenses(many=True)


class _DashActivity(serializers.Serializer):
    action = serializers.CharField()
    user = serializers.CharField(allow_null=True)
    branch_name = serializers.CharField(allow_null=True)
    target_type = serializers.CharField()
    target_id = serializers.CharField()
    created_at = serializers.DateTimeField()


class _DashOperations(serializers.Serializer):
    active_cash_sessions = _DashSession(many=True)
    recent_transactions = _DashTransaction(many=True)
    pending_approvals = _DashPending()
    unread_notifications = serializers.IntegerField()
    recent_activity = _DashActivity(many=True)


class DashboardSerializer(serializers.Serializer):
    currency = _DashCurrency()
    other_currencies_in_period = serializers.ListField(child=serializers.CharField())
    period = PeriodSerializer()
    scope = _DashScope()
    sales = _DashHeadline()
    overview = _DashOverview()
    branch_performance = _DashBranchPerformance()
    sales_trend = _DashTrend()
    products = _DashProducts()
    inventory = _DashInventory()
    operations = _DashOperations()
