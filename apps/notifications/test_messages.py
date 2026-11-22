"""Notification texts read like sentences, and lists carry the branch name."""
from decimal import Decimal

from apps.cash_management.services import close_cash_session, open_cash_session
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense
from apps.inventory.services import record_outflow
from apps.notifications.models import Notification
from apps.notifications.services import _num
from apps.products.test_catalog import CatalogBase


class MessageTests(CatalogBase):
    def test_numbers_are_tidy(self):
        self.assertEqual(_num(Decimal("4.0000")), "4")
        self.assertEqual(_num(Decimal("14500.5000")), "14,500.5")
        self.assertEqual(_num(Decimal("0.2500")), "0.25")

    def test_low_stock_message(self):
        record_outflow(branch=self.branch, product=self.water, quantity=Decimal("1"),
                       movement_type="damaged", reason="x", performed_by=self.owner)
        n = Notification.objects.filter(notification_type="low_stock", recipient=self.owner).first()
        self.assertEqual(n.message, "Water at Main: 2 left (minimum 5).")

    def test_cash_and_expense_messages_use_names(self):
        self.cashier.first_name, self.cashier.last_name = "Diane", "M"
        self.cashier.save()
        session = open_cash_session(branch=self.branch, cashier=self.cashier,
                                    opening_balance=Decimal("1000"))
        close_cash_session(session=session, actual_cash=Decimal("500"), explanation="x",
                           closed_by=self.cashier)
        cash = Notification.objects.filter(notification_type="cash_discrepancy").first()
        self.assertEqual(cash.message, "Diane M's drawer at Main closed short by 500 RWF.")

        rent = ExpenseCategory.objects.create(business=self.business, name="Rent")
        create_expense(user=self.manager, category=rent, amount=Decimal("15000"),
                       description="Milk", branch=self.branch)
        exp = Notification.objects.filter(notification_type="pending_approval").first()
        self.assertEqual(exp.message, "manager recorded 15,000 RWF (Milk) — it needs your review.")

    def test_list_includes_branch_name(self):
        record_outflow(branch=self.branch, product=self.water, quantity=Decimal("1"),
                       movement_type="damaged", reason="x", performed_by=self.owner)
        self.client.force_authenticate(self.owner)
        row = self.client.get("/api/notifications/", {"unread": "true"}).data["results"][0]
        self.assertEqual(row["branch_name"], "Main")
