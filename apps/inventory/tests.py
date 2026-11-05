from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.inventory.models import Inventory, StockMovement, StockMovementType
from apps.products.models import Category, Product, ProductPrice
from apps.sales.models import Sale
from apps.settings.models import BusinessSettings, Currency


class InventoryModuleTests(TestCase):
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
        self.invmgr = User.objects.create_user(
            username="invmgr", password="Passw0rd!",
            role=UserRole.INVENTORY_MANAGER, business=self.business,
        )
        UserBranch.objects.create(user=self.cashier, branch=self.branch)
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

    # ---------------- Inflow ----------------
    def test_inventory_manager_can_record_inflow(self):
        self._auth(self._login("invmgr"))
        resp = self.client.post(
            "/api/stock-movements/inflow/",
            {
                "branch": self.branch.id,
                "product_id": self.product.id,
                "quantity": "50",
                "movement_type": "purchase",
                "reference": "PO-001",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["delta"], "50.0000")
        self.assertEqual(resp.data["quantity_after"], "50.0000")

        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(inv.quantity, Decimal("50.0000"))

    # ---------------- Reason requirement ----------------
    def test_damaged_requires_reason(self):
        self._auth(self._login("invmgr"))
        # Seed some stock first
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "10"},
            format="json",
        )
        resp = self.client.post(
            "/api/stock-movements/outflow/",
            {
                "branch": self.branch.id,
                "product_id": self.product.id,
                "quantity": "2",
                "movement_type": "damaged",
                "reason": "",  # missing
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_damaged_with_reason_succeeds(self):
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "10"},
            format="json",
        )
        resp = self.client.post(
            "/api/stock-movements/outflow/",
            {
                "branch": self.branch.id,
                "product_id": self.product.id,
                "quantity": "2",
                "movement_type": "damaged",
                "reason": "Broken cup",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(inv.quantity, Decimal("8.0000"))

    # ---------------- Insufficient stock ----------------
    def test_outflow_beyond_stock_rejected(self):
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "5"},
            format="json",
        )
        resp = self.client.post(
            "/api/stock-movements/outflow/",
            {
                "branch": self.branch.id,
                "product_id": self.product.id,
                "quantity": "10",
                "movement_type": "adjustment",
                "reason": "test",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------------- Adjustment ----------------
    def test_manual_adjustment(self):
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "20"},
            format="json",
        )
        resp = self.client.post(
            "/api/stock-movements/adjust/",
            {
                "branch": self.branch.id,
                "product_id": self.product.id,
                "new_quantity": "18",
                "reason": "Physical count",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["delta"], "-2.0000")
        self.assertEqual(resp.data["quantity_after"], "18.0000")

    # ---------------- Low-stock ----------------
    def test_low_stock_endpoint(self):
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "3"},
            format="json",
        )
        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        inv.minimum_threshold = Decimal("5")
        inv.save()

        resp = self.client.get("/api/inventory/?low_stock=true")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)

    # ---------------- POS auto-deducts ----------------
    def test_sale_auto_deducts_inventory(self):
        # Seed inventory
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "50"},
            format="json",
        )

        # Cashier makes a sale of 2 units
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch.id,
                "items": [{"product_id": self.product.id, "quantity": "2"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(inv.quantity, Decimal("48.0000"))

        # A movement of type "sale" was recorded
        mv = StockMovement.objects.get(
            branch=self.branch, product=self.product,
            movement_type=StockMovementType.SALE,
        )
        self.assertEqual(mv.delta, Decimal("-2.0000"))
        self.assertIsNotNone(mv.sale)

    def test_sale_fails_if_inventory_insufficient(self):
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "1"},
            format="json",
        )
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch.id,
                "items": [{"product_id": self.product.id, "quantity": "5"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        # No sale was created
        self.assertEqual(Sale.objects.count(), 0)
        # Inventory unchanged
        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(inv.quantity, Decimal("1.0000"))

    # ---------------- Access control ----------------
    def test_cashier_cannot_adjust_stock(self):
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "10"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_movements_are_immutable_via_api(self):
        """No PUT/PATCH/DELETE endpoints exist on stock-movements."""
        self._auth(self._login("invmgr"))
        self.client.post(
            "/api/stock-movements/inflow/",
            {"branch": self.branch.id, "product_id": self.product.id,
             "quantity": "10"},
            format="json",
        )
        mv = StockMovement.objects.first()
        r1 = self.client.patch(f"/api/stock-movements/{mv.id}/", {},
                                format="json")
        r2 = self.client.delete(f"/api/stock-movements/{mv.id}/")
        self.assertEqual(r1.status_code, 405)
        self.assertEqual(r2.status_code, 405)