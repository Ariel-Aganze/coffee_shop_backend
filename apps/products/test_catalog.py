"""POS catalogue, cost-price visibility, and the page_size option."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.inventory.models import Inventory
from apps.inventory.services import record_inflow
from apps.products.models import BranchProductAvailability, Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class CatalogBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=Currency.objects.get(code="RWF"),
            cashier_max_discount_percent=Decimal("10"),
        )
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.other_branch = Branch.objects.create(business=self.business, name="Other")

        def user(name, role, *branches):
            u = User.objects.create_user(username=name, password="Passw0rd!",
                                         role=role, business=self.business)
            for b in branches:
                UserBranch.objects.create(user=u, branch=b)
            return u

        self.owner = user("owner", UserRole.OWNER)
        self.manager = user("manager", UserRole.BRANCH_MANAGER, self.branch)
        self.cashier = user("cashier", UserRole.CASHIER, self.branch)
        self.inventory = user("inventory", UserRole.INVENTORY_MANAGER, self.branch)

        self.drinks = Category.objects.create(business=self.business, name="Drinks")
        self.retired = Category.objects.create(business=self.business, name="Retired",
                                               is_active=False)
        past = timezone.now() - timedelta(days=10)

        def product(name, category=None, price="1000", cost="400", active=True, when=past):
            p = Product.objects.create(business=self.business, name=name, sku=name.upper(),
                                       category=category, is_active=active)
            if price is not None:
                ProductPrice.objects.create(product=p, selling_price=Decimal(price),
                                            cost_price=Decimal(cost), effective_from=when)
            return p

        self.latte = product("Latte", self.drinks, price="2000")
        # Price raised yesterday: the catalogue must show the new one.
        ProductPrice.objects.create(product=self.latte, selling_price=Decimal("2200"),
                                    cost_price=Decimal("900"),
                                    effective_from=timezone.now() - timedelta(days=1))
        # Price change scheduled for tomorrow: not effective yet.
        ProductPrice.objects.create(product=self.latte, selling_price=Decimal("9999"),
                                    effective_from=timezone.now() + timedelta(days=1))
        self.water = product("Water", None, price="500")
        self.inactive = product("Discontinued", self.drinks, active=False)
        self.in_retired_category = product("Old Blend", self.retired)
        self.hidden = product("Branch Hidden", self.drinks)
        BranchProductAvailability.objects.create(branch=self.branch, product=self.hidden,
                                                 is_available=False)
        self.unpriced = product("Unpriced", self.drinks, price=None)
        self.future_only = product("Coming Soon", self.drinks,
                                   when=timezone.now() + timedelta(days=3))

        record_inflow(branch=self.branch, product=self.water, quantity=Decimal("3"),
                      performed_by=self.owner)
        Inventory.objects.filter(product=self.water).update(minimum_threshold=Decimal("5"))
        self.client = APIClient()

    def _catalog(self, user, branch=None):
        self.client.force_authenticate(user)
        branch = branch or self.branch
        return self.client.get(f"/api/pos/catalog/?branch={branch.id}")


class PosCatalogTests(CatalogBase):
    def test_only_sellable_products_with_current_price(self):
        resp = self._catalog(self.cashier)
        self.assertEqual(resp.status_code, 200, resp.data)
        products = {p["name"]: p for p in resp.data["products"]}
        self.assertEqual(set(products), {"Latte", "Water"})
        self.assertEqual(Decimal(products["Latte"]["price"]), Decimal("2200"))
        self.assertEqual(resp.data["categories"], [{"id": self.drinks.id, "name": "Drinks"}])
        self.assertEqual(resp.data["currency"]["code"], "RWF")
        self.assertEqual(resp.data["currency"]["decimal_places"], 0)

    def test_stock_information(self):
        products = {p["name"]: p for p in self._catalog(self.cashier).data["products"]}
        water, latte = products["Water"], products["Latte"]
        self.assertTrue(water["stock_tracked"])
        self.assertEqual(Decimal(water["stock_quantity"]), Decimal("3"))
        self.assertTrue(water["is_low_stock"])
        self.assertTrue(water["in_stock"])
        self.assertFalse(latte["stock_tracked"])
        self.assertIsNone(latte["stock_quantity"])
        self.assertTrue(latte["in_stock"])

    def test_discount_allowance_per_role(self):
        self.assertEqual(self._catalog(self.cashier).data["discount"],
                         {"unlimited": False, "max_percent": "10.00"})
        self.assertEqual(self._catalog(self.manager).data["discount"],
                         {"unlimited": True, "max_percent": None})

    def test_never_contains_cost_prices(self):
        for user in (self.cashier, self.owner):
            body = str(self._catalog(user).data)
            self.assertNotIn("cost", body)
            self.assertNotIn("900", body)

    def test_every_catalogue_product_can_be_sold(self):
        self.client.force_authenticate(self.cashier)
        for product in self._catalog(self.cashier).data["products"]:
            resp = self.client.post("/api/sales/", {
                "branch": self.branch.id,
                "items": [{"product_id": product["id"], "quantity": "1"}],
            }, format="json")
            self.assertEqual(resp.status_code, 201, (product["name"], resp.data))
            self.assertEqual(Decimal(resp.data["total_amount"]), Decimal(product["price"]))

    def test_access_rules(self):
        self.assertEqual(self._catalog(self.cashier, self.other_branch).status_code, 403)
        self.assertEqual(self._catalog(self.inventory).status_code, 403)
        self.assertEqual(self._catalog(self.owner, self.other_branch).status_code, 200)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get("/api/pos/catalog/").status_code, 400)
        self.branch.is_active = False
        self.branch.save()
        self.assertEqual(self._catalog(self.cashier).status_code, 400)


class CostVisibilityTests(CatalogBase):
    def _product(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/products/{self.latte.id}/").data

    def test_cashier_never_receives_cost_prices(self):
        self.client.force_authenticate(self.cashier)
        for row in self.client.get("/api/products/?page_size=100").data["results"]:
            self.assertNotIn("current_cost_price", row)
        self.assertNotIn("current_cost_price", self._product(self.cashier))
        for row in self.client.get(f"/api/products/{self.latte.id}/prices/").data:
            self.assertNotIn("cost_price", row)
        current = self.client.get(f"/api/products/{self.latte.id}/current-price/").data
        self.assertNotIn("cost_price", current)

    def test_other_roles_still_see_costs(self):
        for user in (self.owner, self.manager, self.inventory):
            data = self._product(user)
            self.assertEqual(Decimal(data["current_cost_price"]), Decimal("900"), user.role)
            self.assertEqual(Decimal(data["current_price"]), Decimal("2200"))
        self.client.force_authenticate(self.owner)
        rows = self.client.get(f"/api/products/{self.latte.id}/prices/").data
        self.assertIn("cost_price", rows[0])
        resp = self.client.post(f"/api/products/{self.latte.id}/prices/",
                                {"selling_price": "2300", "cost_price": "950"}, format="json")
        self.assertEqual(Decimal(resp.data["cost_price"]), Decimal("950"))

    def test_list_prices_match_effective_price(self):
        self.client.force_authenticate(self.owner)
        rows = {r["name"]: r for r in
                self.client.get("/api/products/?page_size=100").data["results"]}
        self.assertEqual(Decimal(rows["Latte"]["current_price"]), Decimal("2200"))
        self.assertIsNone(rows["Unpriced"]["current_price"])
        self.assertIsNone(rows["Coming Soon"]["current_price"])


class PageSizeTests(CatalogBase):
    def test_page_size_param(self):
        for i in range(25):
            Product.objects.create(business=self.business, name=f"P{i:02d}", sku=f"P{i:02d}")
        self.client.force_authenticate(self.owner)
        default = self.client.get("/api/products/").data
        self.assertEqual(len(default["results"]), 20)
        big = self.client.get("/api/products/?page_size=50").data
        self.assertEqual(len(big["results"]), big["count"])

    def test_page_size_is_capped(self):
        from config.pagination import StandardPagination
        self.assertEqual(StandardPagination.max_page_size, 100)
