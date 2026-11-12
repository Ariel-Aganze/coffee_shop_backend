"""
Keep the API documentation honest:
  - every report / dashboard / catalogue response has exactly the fields its
    documented serializer declares (recursively), so generated frontend types
    match reality;
  - schema generation stays free of warnings and passes OpenAPI validation.
"""
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from rest_framework import serializers

from apps.expenses.models import Expense, ExpenseCategory, ExpenseStatus
from apps.inventory.models import Inventory
from apps.inventory.services import record_inflow
from apps.products.catalog import PosCatalogSerializer
from apps.sales.serializers import ReceiptSerializer

from . import schemas as s
from .test_permissions_and_exports import Base


def assert_shape(test, serializer, data, path="$"):
    """Fail if `data` has missing or extra keys compared with `serializer`."""
    if data is None:
        return
    if isinstance(serializer, serializers.ListSerializer):
        test.assertIsInstance(data, list, path)
        for i, item in enumerate(data):
            assert_shape(test, serializer.child, item, f"{path}[{i}]")
        return
    if isinstance(serializer, serializers.Serializer):
        test.assertIsInstance(data, dict, path)
        fields = serializer.fields
        test.assertEqual(set(data), set(fields), f"keys differ at {path}")
        for name, field in fields.items():
            assert_shape(test, field, data[name], f"{path}.{name}")


class ResponseShapeTests(Base):
    def setUp(self):
        super().setUp()
        # Enough activity that every list in every response is non-empty.
        record_inflow(branch=self.branch_a, product=self.muffin, quantity=Decimal("4"),
                      performed_by=self.owner)
        Inventory.objects.update(minimum_threshold=Decimal("10"))
        self._as(self.cashier_a)
        self.client.post("/api/cash-sessions/open/",
                         {"branch": self.branch_a.id, "opening_balance": "1000"}, format="json")
        sale = self._sale(self.cashier_a, self.branch_a, "2").data
        self._sale(self.cashier_b, self.branch_b, "1", product=self.muffin)
        self._sale(self.cashier_a, self.branch_a, "1", pay=False)
        self._as(self.manager_a)
        self.client.post(f"/api/sales/{sale['id']}/refund/", {"amount": "500"}, format="json")
        self.sale_id = sale["id"]
        cat = ExpenseCategory.objects.create(business=self.business, name="Rent")
        for status in (ExpenseStatus.APPROVED, ExpenseStatus.PENDING):
            Expense.objects.create(business=self.business, branch=self.branch_a, category=cat,
                                   amount=Decimal("700"), currency_code="RWF", description="x",
                                   submitted_by=self.manager_a, status=status)

    def _check(self, url, serializer):
        self._as(self.owner)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, (url, resp.data))
        assert_shape(self, serializer, resp.data, url)
        return resp.data

    def test_reports_match_documented_shapes(self):
        cases = [
            ("sales/summary/", s.SalesSummaryReport),
            ("sales/by-branch/", s.SalesByBranchReport),
            ("sales/by-product/", s.SalesByProductReport),
            ("sales/by-cashier/", s.SalesByCashierReport),
            ("sales/by-payment-method/", s.SalesByPaymentMethodReport),
            ("inventory/current-stock/", s.CurrentStockReport),
            ("inventory/low-stock/", s.LowStockReport),
            ("inventory/movements/", s.StockMovementReport),
            ("expenses/", s.ExpensesReport),
            ("financial/summary/", s.FinancialSummaryReport),
            ("cash/reconciliation/", s.CashReconciliationReport),
            ("branches/compare/", s.BranchComparisonReport),
        ]
        for path, serializer in cases:
            with self.subTest(path):
                data = self._check(f"/api/reports/{path}", serializer())
                self.assertTrue(data["results"], f"{path}: no rows, shape not exercised")

    def test_unpaid_only_currency_row_has_same_shape(self):
        # A currency with only unpaid sales takes a separate code path.
        self._as(self.owner)
        self.client.post("/api/settings/me/change-currency/",
                         {"currency_code": "USD", "confirm": True}, format="json")
        self._sale(self.owner, self.branch_a, "1", pay=False)
        data = self._check("/api/reports/sales/summary/", s.SalesSummaryReport())
        self.assertIn("USD", {r["currency_code"] for r in data["results"]})

    def test_dashboard_matches_documented_shape(self):
        data = self._check("/api/reports/dashboard/", s.DashboardSerializer())
        for key in ("recent_transactions", "active_cash_sessions", "recent_activity"):
            self.assertTrue(data["operations"][key], key)
        self.assertTrue(data["inventory"]["low_stock"])
        self.assertIsNotNone(data["branch_performance"]["best_branch"])

    def test_catalog_and_receipt_match_documented_shapes(self):
        self._check(f"/api/pos/catalog/?branch={self.branch_a.id}", PosCatalogSerializer())
        self._check(f"/api/sales/{self.sale_id}/receipt/", ReceiptSerializer())


class SchemaGenerationTests(TestCase):
    def test_schema_generates_without_warnings_and_validates(self):
        # Raises SchemaGenerationError on any warning; validates against OpenAPI 3.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "schema.yml"
            call_command("spectacular", "--validate", "--fail-on-warn",
                         "--file", str(out), stdout=StringIO(), stderr=StringIO())
            self.assertGreater(out.stat().st_size, 0)
