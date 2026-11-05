from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.expenses.models import ExpenseCategory
from apps.notifications.models import Notification, NotificationType
from apps.products.models import Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class NotificationTests(TestCase):
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
        self.invmgr = User.objects.create_user(
            username="invmgr", password="Passw0rd!",
            role=UserRole.INVENTORY_MANAGER, business=self.business,
        )
        UserBranch.objects.create(user=self.manager, branch=self.branch)
        UserBranch.objects.create(user=self.invmgr, branch=self.branch)

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

    def test_low_stock_triggers_notification(self):
        # Seed 10 units
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "10"},
            format="json",
        )
        # Set threshold 20
        from apps.inventory.models import Inventory
        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        inv.minimum_threshold = Decimal("20")
        inv.save()

        # Outflow of 5 should trigger a low-stock notification
        self.client.post(
            "/api/stock-movements/outflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "5", "movement_type": "adjustment",
             "reason": "test"},
            format="json",
        )
        # Owner should have received one
        notifs = Notification.objects.filter(
            recipient=self.owner,
            notification_type=NotificationType.LOW_STOCK,
        )
        self.assertGreaterEqual(notifs.count(), 1)

    def test_cash_discrepancy_triggers_notification(self):
        # A cashier opens and closes the session with a discrepancy.
        cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        UserBranch.objects.create(user=cashier, branch=self.branch)

        self._auth(self._login("cashier"))
        session = self.client.post(
            "/api/cash-sessions/open/",
            {"branch": self.branch.id, "opening_balance": "10000"},
            format="json",
        ).data

        # Close with discrepancy → triggers notification
        self.client.post(
            f"/api/cash-sessions/{session['id']}/close/",
            {"actual_cash": "9500", "explanation": "Missing 500"},
            format="json",
        )

        notifs = Notification.objects.filter(
            recipient=self.owner,
            notification_type=NotificationType.CASH_DISCREPANCY,
        )
        self.assertGreaterEqual(notifs.count(), 1)

    def test_pending_approval_triggers_notification(self):
        cat = ExpenseCategory.objects.create(
            business=self.business, name="Utilities",
        )
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/expenses/",
            {"category": cat.id, "amount": "500",
             "description": "x", "branch": self.branch.id},
            format="json",
        )
        notifs = Notification.objects.filter(
            recipient=self.owner,
            notification_type=NotificationType.PENDING_APPROVAL,
        )
        self.assertGreaterEqual(notifs.count(), 1)

    def test_list_my_notifications(self):
        from apps.notifications.services import notify

        notify(
            recipient=self.owner,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Test",
            message="Test message",
        )
        self._auth(self._login("owner"))
        resp = self.client.get("/api/notifications/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    def test_unread_count(self):
        from apps.notifications.services import notify

        notify(
            recipient=self.owner,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Test",
            message="Test message",
        )
        notify(
            recipient=self.owner,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Test 2",
            message="Test message 2",
        )
        self._auth(self._login("owner"))
        resp = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(resp.data["unread"], 2)

    def test_mark_read(self):
        from apps.notifications.services import notify

        n = notify(
            recipient=self.owner,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Test",
            message="Test message",
        )
        self._auth(self._login("owner"))
        resp = self.client.post(f"/api/notifications/{n.id}/mark-read/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["is_read"])

    def test_mark_all_read(self):
        from apps.notifications.services import notify

        for i in range(3):
            notify(
                recipient=self.owner,
                notification_type=NotificationType.BUSINESS_EVENT,
                title=f"T{i}",
                message=f"M{i}",
            )
        self._auth(self._login("owner"))
        resp = self.client.post("/api/notifications/mark-all-read/")
        self.assertEqual(resp.data["updated"], 3)

        resp2 = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(resp2.data["unread"], 0)

    def test_user_only_sees_own_notifications(self):
        from apps.notifications.services import notify

        notify(
            recipient=self.owner,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Owner only",
            message="M",
        )
        notify(
            recipient=self.manager,
            notification_type=NotificationType.BUSINESS_EVENT,
            title="Manager only",
            message="M",
        )
        self._auth(self._login("owner"))
        resp = self.client.get("/api/notifications/")
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["results"][0]["title"], "Owner only")