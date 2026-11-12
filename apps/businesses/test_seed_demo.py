"""Smoke test for `manage.py seed_demo`: data is created and internally consistent."""
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.db.models import F
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.businesses.models import Business
from apps.cash_management.models import CashSession
from apps.cash_management.services import compute_expected_cash
from apps.expenses.models import Expense
from apps.inventory.models import Inventory, StockMovement
from apps.sales.models import Sale


class SeedDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.out = StringIO()
        call_command("seed_demo", days=3, force=True, prefix="t", name="T Cafe", stdout=cls.out)
        cls.business = Business.objects.get(name="T Cafe")

    def test_creates_business_staff_and_activity(self):
        self.assertEqual(self.business.branches.count(), 3)
        self.assertEqual(User.objects.filter(business=self.business).count(), 13)
        self.assertGreater(Sale.objects.filter(business=self.business).count(), 50)
        self.assertTrue(Expense.objects.filter(business=self.business, status="pending").exists())
        self.assertTrue(Expense.objects.filter(business=self.business, status="approved").exists())
        self.assertTrue(AuditLog.objects.filter(business=self.business).exists())
        self.assertIn("Logins", self.out.getvalue())

    def test_history_spreads_over_past_days(self):
        days = {s.created_at.date() for s in Sale.objects.filter(business=self.business)}
        self.assertGreaterEqual(len(days), 3)

    def test_cash_sessions_reconcile(self):
        closed = CashSession.objects.filter(branch__business=self.business, status="closed")
        self.assertTrue(closed.exists())
        for session in closed:
            self.assertEqual(compute_expected_cash(session)["expected_cash"], session.expected_cash)
            self.assertEqual(session.actual_cash - session.expected_cash, session.difference)

    def test_stock_ledger_consistent(self):
        for inv in Inventory.objects.filter(branch__business=self.business):
            self.assertGreaterEqual(inv.quantity, 0)
            ledger = sum((m.delta for m in StockMovement.objects.filter(
                branch=inv.branch, product=inv.product)), Decimal("0"))
            self.assertEqual(ledger, inv.quantity, inv)
        self.assertTrue(Inventory.objects.filter(
            branch__business=self.business, quantity__lte=F("minimum_threshold"),
        ).exists())

    def test_seeded_users_can_use_the_api(self):
        client = APIClient()
        resp = client.post("/api/auth/login/", {"username": "t_owner", "password": "Demo1234!"},
                           format="json")
        self.assertEqual(resp.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        dash = client.get("/api/reports/dashboard/")
        self.assertEqual(dash.status_code, 200)
        self.assertGreater(Decimal(dash.data["overview"]["revenue"]), 0)

    def test_refuses_duplicates_and_production(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", days=1, force=True, prefix="t2", name="T Cafe", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("seed_demo", days=1, force=True, prefix="t", name="Other", stdout=StringIO())
        with self.assertRaises(CommandError):  # tests run with DEBUG=False
            call_command("seed_demo", days=1, prefix="t3", name="Third", stdout=StringIO())
