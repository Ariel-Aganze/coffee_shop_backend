from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.expenses.models import Expense, ExpenseCategory, ExpenseStatus
from apps.settings.models import BusinessSettings, Currency


class ExpenseModuleTests(TestCase):
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
        self.manager = User.objects.create_user(
            username="manager", password="Passw0rd!",
            role=UserRole.BRANCH_MANAGER, business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        UserBranch.objects.create(user=self.manager, branch=self.branch)
        UserBranch.objects.create(user=self.cashier, branch=self.branch)

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

    def test_owner_creates_category(self):
        self._auth(self._login("owner"))
        resp = self.client.post(
            "/api/expense-categories/",
            {"name": "Utilities", "description": "Water, electricity"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(ExpenseCategory.objects.count(), 1)

    def test_cashier_cannot_create_category(self):
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/expense-categories/", {"name": "Utilities"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_manager_creates_expense_pending(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("manager"))
        resp = self.client.post(
            "/api/expenses/",
            {
                "category": cat.id,
                "amount": "5000.0000",
                "description": "Cleaning supplies",
                "branch": self.branch.id,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "pending")
        self.assertEqual(resp.data["currency_code"], "RWF")
        self.assertEqual(Decimal(resp.data["amount"]), Decimal("5000.0000"))

    def test_cashier_cannot_create_expense(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/expenses/",
            {
                "category": cat.id,
                "amount": "1000",
                "description": "x",
                "branch": self.branch.id,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_owner_approves_expense(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("manager"))
        e = self.client.post(
            "/api/expenses/",
            {"category": cat.id, "amount": "1000",
             "description": "x", "branch": self.branch.id},
            format="json",
        ).data

        self._auth(self._login("owner"))
        resp = self.client.post(
            f"/api/expenses/{e['id']}/approve/",
            {"note": "OK"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "approved")

    def test_reject_requires_reason(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("manager"))
        e = self.client.post(
            "/api/expenses/",
            {"category": cat.id, "amount": "1000",
             "description": "x", "branch": self.branch.id},
            format="json",
        ).data

        self._auth(self._login("owner"))
        resp = self.client.post(
            f"/api/expenses/{e['id']}/reject/", {"note": ""}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

        resp2 = self.client.post(
            f"/api/expenses/{e['id']}/reject/", {"note": "Invalid"}, format="json",
        )
        self.assertEqual(resp2.status_code, 200)

    def test_approve_idempotency_guard(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("manager"))
        e = self.client.post(
            "/api/expenses/",
            {"category": cat.id, "amount": "1000",
             "description": "x", "branch": self.branch.id},
            format="json",
        ).data

        self._auth(self._login("owner"))
        self.client.post(f"/api/expenses/{e['id']}/approve/", {}, format="json")
        resp = self.client.post(f"/api/expenses/{e['id']}/approve/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_manager_cannot_approve_own_expense(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        self._auth(self._login("manager"))
        e = self.client.post(
            "/api/expenses/",
            {"category": cat.id, "amount": "1000",
             "description": "x", "branch": self.branch.id},
            format="json",
        ).data
        resp = self.client.post(
            f"/api/expenses/{e['id']}/approve/", {}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cashier_cannot_see_other_branch_expenses(self):
        other = Branch.objects.create(business=self.business, name="Other")
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Cleaning",
        )
        Expense.objects.create(
            business=self.business, branch=other, category=cat,
            amount=Decimal("1000"), currency_code="RWF",
            currency_decimal_places=0, description="x",
            submitted_by=self.owner, status=ExpenseStatus.PENDING,
        )
        self._auth(self._login("cashier"))
        resp = self.client.get("/api/expenses/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)