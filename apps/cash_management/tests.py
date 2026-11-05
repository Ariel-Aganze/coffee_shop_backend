from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.cash_management.models import CashSession, CashSessionStatus
from apps.products.models import Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class CashManagementTests(TestCase):
    def setUp(self):
        currency = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        BusinessSettings.objects.create(
            business=self.business, primary_currency=currency,
        )
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.owner = User.objects.create_user(
            username="owner", password="Passw0rd!",
            role=UserRole.OWNER, business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        self.manager = User.objects.create_user(
            username="manager", password="Passw0rd!",
            role=UserRole.BRANCH_MANAGER, business=self.business,
        )
        UserBranch.objects.create(user=self.cashier, branch=self.branch)
        UserBranch.objects.create(user=self.manager, branch=self.branch)

        cat = Category.objects.create(business=self.business, name="Drinks")
        self.product = Product.objects.create(
            business=self.business, category=cat, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(
            product=self.product, selling_price=Decimal("2000.0000"),
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

    def _open(self, opening="10000"):
        self._auth(self._login("cashier"))
        return self.client.post(
            "/api/cash-sessions/open/",
            {"branch": self.branch.id, "opening_balance": opening},
            format="json",
        )

    def _sell_and_pay_cash(self, quantity="1"):
        self._auth(self._login("cashier"))
        sale = self.client.post(
            "/api/sales/",
            {"branch": self.branch.id,
             "items": [{"product_id": self.product.id, "quantity": quantity}]},
            format="json",
        )
        sale_id = sale.data["id"]
        total = sale.data["total_amount"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": total}]},
            format="json",
        )
        return sale_id

    # ---------------- Open ----------------
    def test_open_session(self):
        resp = self._open("10000")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "open")

    def test_cannot_open_two_sessions(self):
        self._open("10000")
        resp = self.client.post(
            "/api/cash-sessions/open/",
            {"branch": self.branch.id, "opening_balance": "5000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------------- Movements ----------------
    def test_add_inflow_and_withdrawal(self):
        session_id = self._open("10000").data["id"]

        r1 = self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "inflow", "amount": "5000", "reason": "Float top-up"},
            format="json",
        )
        self.assertEqual(r1.status_code, 201, r1.data)

        r2 = self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "withdrawal", "amount": "2000", "reason": "Bank deposit"},
            format="json",
        )
        self.assertEqual(r2.status_code, 201, r2.data)

    def test_movement_requires_reason(self):
        session_id = self._open().data["id"]
        resp = self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "inflow", "amount": "1000", "reason": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------------- Reconciliation ----------------
    def test_reconciliation_includes_cash_sales(self):
        session_id = self._open("10000").data["id"]
        self._sell_and_pay_cash("2")  # 4000 cash

        self._auth(self._login("cashier"))
        resp = self.client.get(f"/api/cash-sessions/{session_id}/reconciliation/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data["opening"]), Decimal("10000.0000"))
        self.assertEqual(Decimal(resp.data["cash_sales"]), Decimal("4000.0000"))
        self.assertEqual(Decimal(resp.data["expected_cash"]), Decimal("14000.0000"))

    def test_reconciliation_with_movements(self):
        session_id = self._open("10000").data["id"]
        self._sell_and_pay_cash("1")  # +2000

        self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "inflow", "amount": "1000", "reason": "top-up"},
            format="json",
        )
        self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "withdrawal", "amount": "500", "reason": "deposit"},
            format="json",
        )
        self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "expense", "amount": "300", "reason": "cleaning"},
            format="json",
        )

        resp = self.client.get(f"/api/cash-sessions/{session_id}/reconciliation/")
        # expected = 10000 + 2000 + 1000 - 300 - 500 = 12200
        self.assertEqual(Decimal(resp.data["expected_cash"]), Decimal("12200.0000"))

    # ---------------- Close ----------------
    def test_close_without_discrepancy(self):
        session_id = self._open("10000").data["id"]
        self._sell_and_pay_cash("1")  # 2000 cash

        self._auth(self._login("cashier"))
        resp = self.client.post(
            f"/api/cash-sessions/{session_id}/close/",
            {"actual_cash": "12000.0000"},  # matches expected
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        session = CashSession.objects.get(pk=session_id)
        self.assertEqual(session.status, CashSessionStatus.CLOSED)
        self.assertEqual(session.difference, Decimal("0.0000"))

    def test_close_with_discrepancy_requires_explanation(self):
        session_id = self._open("10000").data["id"]
        self._sell_and_pay_cash("1")  # 2000 → expected 12000

        self._auth(self._login("cashier"))
        resp = self.client.post(
            f"/api/cash-sessions/{session_id}/close/",
            {"actual_cash": "11800.0000"},  # 200 short, no explanation
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

        resp2 = self.client.post(
            f"/api/cash-sessions/{session_id}/close/",
            {"actual_cash": "11800.0000", "explanation": "Missing 200"},
            format="json",
        )
        self.assertEqual(resp2.status_code, 200, resp2.data)
        session = CashSession.objects.get(pk=session_id)
        self.assertEqual(session.difference, Decimal("-200.0000"))

    def test_cannot_add_movement_to_closed_session(self):
        session_id = self._open("10000").data["id"]
        self.client.post(
            f"/api/cash-sessions/{session_id}/close/",
            {"actual_cash": "10000"},
            format="json",
        )
        resp = self.client.post(
            f"/api/cash-sessions/{session_id}/movements/",
            {"movement_type": "inflow", "amount": "100", "reason": "x"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------------- Cashier scope ----------------
    def test_cashier_only_sees_own_branch_sessions(self):
        other = Branch.objects.create(business=self.business, name="Other")
        # Cashier's session at own branch
        self._open("1000")
        # Owner opens a session at other branch
        self._auth(self._login("owner"))
        self.client.post(
            "/api/cash-sessions/open/",
            {"branch": other.id, "opening_balance": "500"},
            format="json",
        )

        self._auth(self._login("cashier"))
        resp = self.client.get("/api/cash-sessions/")
        self.assertEqual(resp.status_code, 200)
        for row in resp.data["results"]:
            self.assertEqual(row["branch"], self.branch.id)