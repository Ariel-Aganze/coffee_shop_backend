from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.products.models import (
    BranchProductAvailability,
    Category,
    Product,
    ProductPrice,
)
from apps.settings.models import BusinessSettings, Currency
from rest_framework.test import APIClient


class ProductModuleTests(TestCase):
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
        UserBranch.objects.create(user=self.cashier, branch=self.branch)
        self.client = APIClient()

    def _login(self, username="owner", password="Passw0rd!"):
        resp = self.client.post(
            "/api/auth/login/",
            {"username": username, "password": password},
            format="json",
        )
        return resp.data["access"]

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    # ---------------- Category ----------------
    def test_owner_can_create_category(self):
        self._auth(self._login())
        resp = self.client.post(
            "/api/categories/",
            {"name": "Hot Drinks", "description": "Coffee, tea"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["name"], "Hot Drinks")

    def test_cashier_cannot_create_category(self):
        self._auth(self._login("cashier"))
        resp = self.client.post(
            "/api/categories/", {"name": "Hot Drinks"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    # ---------------- Product ----------------
    def test_create_product_with_initial_price(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")

        resp = self.client.post(
            "/api/products/",
            {
                "name": "Espresso",
                "sku": "ESP-001",
                "category": cat.id,
                "product_type": "drink",
                "initial_selling_price": "1500.0000",
                "initial_cost_price": "400.0000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

        product = Product.objects.get(sku="ESP-001")
        self.assertEqual(product.business_id, self.business.id)
        self.assertEqual(product.prices.count(), 1)
        self.assertEqual(
            product.prices.first().selling_price, Decimal("1500.0000")
        )

    def test_duplicate_sku_is_rejected(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")
        Product.objects.create(
            business=self.business, category=cat, name="X", sku="ESP-001",
        )
        resp = self.client.post(
            "/api/products/",
            {
                "name": "Espresso",
                "sku": "ESP-001",
                "category": cat.id,
                "initial_selling_price": "1500",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------------- Prices ----------------
    def test_add_new_price_preserves_history(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")
        prod = Product.objects.create(
            business=self.business, category=cat, name="Latte", sku="LAT-001",
        )
        ProductPrice.objects.create(
            product=prod, selling_price=Decimal("2000"), effective_from=timezone.now(),
        )

        resp = self.client.post(
            f"/api/products/{prod.id}/prices/",
            {"selling_price": "2500.0000", "reason": "inflation"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)

        # Historical rows preserved
        self.assertEqual(prod.prices.count(), 2)

        # Current price is the latest
        current = prod.current_price()
        self.assertEqual(current.selling_price, Decimal("2500.0000"))

    def test_past_price_row_is_preserved_and_accessible(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")
        prod = Product.objects.create(
            business=self.business, category=cat, name="Mocha", sku="MOC-001",
        )
        p1 = ProductPrice.objects.create(
            product=prod, selling_price=Decimal("3000"), effective_from=timezone.now(),
        )
        self.client.post(
            f"/api/products/{prod.id}/prices/",
            {"selling_price": "3500.0000"},
            format="json",
        )
        # Ensure the old price row is still in the DB
        self.assertTrue(ProductPrice.objects.filter(pk=p1.pk).exists())

    # ---------------- Branch availability ----------------
    def test_set_branch_availability(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")
        prod = Product.objects.create(
            business=self.business, category=cat, name="Tea", sku="TEA-001",
        )
        resp = self.client.post(
            "/api/branch-availability/",
            {"branch": self.branch.id, "product": prod.id, "is_available": False},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertFalse(
            BranchProductAvailability.objects.get(branch=self.branch, product=prod).is_available
        )

    def test_cashier_only_sees_own_branch_availability(self):
        self._auth(self._login())
        other_branch = Branch.objects.create(business=self.business, name="Other")
        cat = Category.objects.create(business=self.business, name="Drinks")
        p1 = Product.objects.create(
            business=self.business, category=cat, name="A", sku="A-1",
        )
        p2 = Product.objects.create(
            business=self.business, category=cat, name="B", sku="B-1",
        )
        BranchProductAvailability.objects.create(
            branch=self.branch, product=p1, is_available=True,
        )
        BranchProductAvailability.objects.create(
            branch=other_branch, product=p2, is_available=True,
        )

        self._auth(self._login("cashier"))
        resp = self.client.get("/api/branch-availability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(
            resp.data["results"][0]["branch"], self.branch.id
        )

    # ---------------- Soft delete ----------------
    def test_delete_product_deactivates(self):
        self._auth(self._login())
        cat = Category.objects.create(business=self.business, name="Drinks")
        prod = Product.objects.create(
            business=self.business, category=cat, name="X", sku="X-1",
        )
        resp = self.client.delete(f"/api/products/{prod.id}/")
        self.assertEqual(resp.status_code, 204)
        prod.refresh_from_db()
        self.assertFalse(prod.is_active)