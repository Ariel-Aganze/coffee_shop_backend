from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductPrice
from apps.sales.models import Sale, SaleItem
from apps.settings.models import BusinessSettings, Currency
from rest_framework.test import APIClient


class SaleModuleTests(TestCase):
    def setUp(self):
        currency = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        BusinessSettings.objects.create(
            business=self.business, primary_currency=currency,
        )
        self.branch_a = Branch.objects.create(business=self.business, name="Main")
        self.branch_b = Branch.objects.create(business=self.business, name="Branch B")
        self.owner = User.objects.create_user(
            username="owner", password="Passw0rd!",
            role=UserRole.OWNER, business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        UserBranch.objects.create(user=self.cashier, branch=self.branch_a)

        self.cat = Category.objects.create(business=self.business, name="Drinks")
        self.product1 = Product.objects.create(
            business=self.business, category=self.cat, name="Espresso", sku="ESP-1",
        )
        ProductPrice.objects.create(
            product=self.product1, selling_price=Decimal("1500.0000"),
            effective_from=timezone.now(),
        )
        self.product2 = Product.objects.create(
            business=self.business, category=self.cat, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(
            product=self.product2, selling_price=Decimal("2000.0000"),
            effective_from=timezone.now(),
        )
        self.client = APIClient()

    def _login(self, username="cashier", password="Passw0rd!"):
        resp = self.client.post(
            "/api/auth/login/", {"username": username, "password": password},
            format="json",
        )
        return resp.data["access"]

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_cashier_can_create_sale(self):
        self._auth(self._login())
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch_a.id,
                "items": [
                    {"product_id": self.product1.id, "quantity": "2"},
                    {"product_id": self.product2.id, "quantity": "1"},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        # 2*1500 + 1*2000 = 5000
        self.assertEqual(Decimal(resp.data["total_amount"]), Decimal("5000.0000"))
        self.assertEqual(resp.data["currency_code"], "RWF")
        self.assertEqual(len(resp.data["items"]), 2)
        self.assertTrue(resp.data["transaction_number"].startswith("TXN-"))

    def test_backend_computes_totals_even_if_client_lies(self):
        """
        BRULE-005: backend must ignore client-supplied totals.
        """
        self._auth(self._login())
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch_a.id,
                "total_amount": "1.0000",  # ← malicious
                "items": [
                    {"product_id": self.product1.id, "quantity": "2"},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Decimal(resp.data["total_amount"]), Decimal("3000.0000"))

    def test_idempotency_key_prevents_duplicate(self):
        self._auth(self._login())
        payload = {
            "branch": self.branch_a.id,
            "items": [{"product_id": self.product1.id, "quantity": "1"}],
        }
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._login()}",
            HTTP_X_IDEMPOTENCY_KEY="key-123",
        )
        r1 = self.client.post("/api/sales/", payload, format="json")
        r2 = self.client.post("/api/sales/", payload, format="json")

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)  # replay
        self.assertEqual(r1.data["id"], r2.data["id"])
        self.assertEqual(Sale.objects.count(), 1)

    def test_same_key_different_payload_is_rejected(self):
        token = self._login()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_IDEMPOTENCY_KEY="key-xyz",
        )
        r1 = self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id,
             "items": [{"product_id": self.product1.id, "quantity": "1"}]},
            format="json",
        )
        self.assertEqual(r1.status_code, 201)
        r2 = self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id,
             "items": [{"product_id": self.product1.id, "quantity": "9"}]},
            format="json",
        )
        self.assertEqual(r2.status_code, 400)

    def test_price_snapshot_preserved_on_sale(self):
        """
        BRULE-006: sale preserves price at the time of the sale.
        """
        self._auth(self._login())
        r = self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id,
             "items": [{"product_id": self.product1.id, "quantity": "1"}]},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        sale = Sale.objects.get(pk=r.data["id"])
        item = sale.items.first()
        self.assertEqual(item.unit_price, Decimal("1500.0000"))

        # Now change the price; the historical sale must not change.
        ProductPrice.objects.create(
            product=self.product1, selling_price=Decimal("9999.0000"),
            effective_from=timezone.now(),
        )
        sale.refresh_from_db()
        self.assertEqual(sale.items.first().unit_price, Decimal("1500.0000"))

    def test_cashier_cannot_sell_at_other_branch(self):
        self._auth(self._login())
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch_b.id,
                "items": [{"product_id": self.product1.id, "quantity": "1"}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_sale_without_items_rejected(self):
        self._auth(self._login())
        resp = self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id, "items": []},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cashier_cannot_list_other_branch_sales(self):
        # Create one sale at branch A and one at branch B (as owner)
        self._auth(self._login("owner"))
        self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id,
             "items": [{"product_id": self.product1.id, "quantity": "1"}]},
            format="json",
        )
        self.client.post(
            "/api/sales/",
            {"branch": self.branch_b.id,
             "items": [{"product_id": self.product1.id, "quantity": "1"}]},
            format="json",
        )

        # Cashier at branch A must only see branch A sales
        self._auth(self._login("cashier"))
        resp = self.client.get("/api/sales/")
        self.assertEqual(resp.status_code, 200)
        for row in resp.data["results"]:
            self.assertEqual(row["branch"], self.branch_a.id)

    def test_transaction_number_uniqueness(self):
        self._auth(self._login("owner"))
        nums = set()
        for _ in range(5):
            r = self.client.post(
                "/api/sales/",
                {"branch": self.branch_a.id,
                 "items": [{"product_id": self.product1.id, "quantity": "1"}]},
                format="json",
            )
            nums.add(r.data["transaction_number"])
        self.assertEqual(len(nums), 5)

    def test_sale_currency_is_snapshotted(self):
        self._auth(self._login("owner"))
        r = self.client.post(
            "/api/sales/",
            {"branch": self.branch_a.id,
             "items": [{"product_id": self.product1.id, "quantity": "1"}]},
            format="json",
        )
        self.assertEqual(r.data["currency_code"], "RWF")
        # Currency change doesn't rewrite history
        from apps.settings.models import BusinessSettings, Currency
        usd = Currency.objects.get(code="USD")
        s = BusinessSettings.objects.get(business=self.business)
        s.primary_currency = usd
        s.save()

        sale = Sale.objects.get(pk=r.data["id"])
        self.assertEqual(sale.currency_code, "RWF")