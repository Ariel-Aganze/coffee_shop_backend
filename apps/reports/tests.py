from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.expenses.models import Expense, ExpenseCategory, ExpenseStatus
from apps.products.models import Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class ReportsTests(TestCase):
    def setUp(self):
        currency = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        BusinessSettings.objects.create(
            business=self.business, primary_currency=currency,
        )
        self.branch_a = Branch.objects.create(business=self.business, name="A")
        self.branch_b = Branch.objects.create(business=self.business, name="B")
        self.owner = User.objects.create_user(
            username="owner", password="Passw0rd!",
            role=UserRole.OWNER, business=self.business,
        )
        self.cashier_a = User.objects.create_user(
            username="cashier_a", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        UserBranch.objects.create(user=self.cashier_a, branch=self.branch_a)

        cat = Category.objects.create(business=self.business, name="Drinks")
        self.product = Product.objects.create(
            business=self.business, category=cat, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(
            product=self.product, selling_price=Decimal("2000.0000"),
            cost_price=Decimal("800.0000"),
            effective_from=timezone.now(),
        )

        self.client = APIClient()

    def _login(self, username):
        resp = self.client.post(
            "/api/auth/login/",
            {"username": username, "password": "Passw0rd!"},
            format="json",
        )
        return resp.data["access"]

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def _make_sale(self, branch, quantity="2"):
        self._auth(self._login("owner"))
        r = self.client.post(
            "/api/sales/",
            {"branch": branch.id,
             "items": [{"product_id": self.product.id, "quantity": quantity}]},
            format="json",
        )
        return r.data

    def test_sales_summary(self):
        self._make_sale(self.branch_a, "2")
        self._make_sale(self.branch_a, "1")
        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/sales/summary/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertEqual(row["currency_code"], "RWF")
        self.assertEqual(row["transactions"], 2)
        self.assertEqual(Decimal(row["total_sales"]), Decimal("6000.0000"))

    def test_sales_by_branch(self):
        self._make_sale(self.branch_a, "2")
        self._make_sale(self.branch_b, "1")
        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/sales/by-branch/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 2)

    def test_cashier_only_sees_own_branch(self):
        self._make_sale(self.branch_a, "2")
        self._make_sale(self.branch_b, "1")
        self._auth(self._login("cashier_a"))
        resp = self.client.get("/api/reports/sales/by-branch/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(resp.data["results"][0]["branch_id"], self.branch_a.id)

    def test_sales_by_product(self):
        self._make_sale(self.branch_a, "3")
        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/sales/by-product/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertEqual(row["sku"], "LAT-1")
        self.assertEqual(Decimal(row["quantity_sold"]), Decimal("3.0000"))
        self.assertEqual(Decimal(row["revenue"]), Decimal("6000.0000"))

    def test_financial_summary_reports_gross_profit(self):
        self._make_sale(self.branch_a, "2")  # revenue 4000, COGS 2*800=1600

        cat = ExpenseCategory.objects.create(business=self.business, name="Utilities")
        Expense.objects.create(
            business=self.business, branch=self.branch_a, category=cat,
            amount=Decimal("500"), currency_code="RWF",
            currency_decimal_places=0, description="water",
            submitted_by=self.owner, status=ExpenseStatus.APPROVED,
        )

        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/financial/summary/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertEqual(Decimal(row["revenue"]), Decimal("4000.0000"))
        self.assertEqual(Decimal(row["cost_of_goods_sold"]), Decimal("1600.0000"))
        self.assertEqual(Decimal(row["gross_profit"]), Decimal("2400.0000"))
        self.assertEqual(Decimal(row["operating_expenses"]), Decimal("500.0000"))
        self.assertEqual(Decimal(row["operating_result"]), Decimal("1900.0000"))

    def test_expenses_report(self):
        cat = ExpenseCategory.objects.create(business=self.business, name="Utilities")
        Expense.objects.create(
            business=self.business, branch=self.branch_a, category=cat,
            amount=Decimal("500"), currency_code="RWF",
            currency_decimal_places=0, description="water",
            submitted_by=self.owner, status=ExpenseStatus.APPROVED,
        )
        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/expenses/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 1)
        self.assertEqual(Decimal(resp.data["approved_totals"][0]["total"]),
                         Decimal("500.0000"))

    def test_branch_comparison_owner_only(self):
        self._make_sale(self.branch_a, "1")
        self._auth(self._login("cashier_a"))
        resp = self.client.get("/api/reports/branches/compare/")
        self.assertEqual(resp.status_code, 403)

        self._auth(self._login("owner"))
        resp = self.client.get("/api/reports/branches/compare/")
        self.assertEqual(resp.status_code, 200)