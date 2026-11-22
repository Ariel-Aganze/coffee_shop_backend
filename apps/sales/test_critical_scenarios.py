"""
SRS 13.3 "Critical test scenarios" — where each one is tested.

  Cashier attempts to access another branch ....... sales/tests (cashier_cannot_sell_at_other_branch,
                                                    cannot_list_other_branch_sales), e2e roles.spec
  Unauthorized modification of a completed sale ... CompletedSaleTests (here), payments tests
  Sale submitted twice (network retry) ............ sales/test_checkout (retry_with_same_key…),
                                                    frontend pos.test (same key on retry)
  Sale fails during inventory deduction ........... sales/tests (sale_fails_if_inventory_insufficient),
                                                    test_checkout (failed_payment_leaves_no_sale…)
  Stock adjustment without a reason ............... inventory/tests (movement_requires_reason…), e2e
  Branch manager changes the currency ............. accounts/test_security_fixes, e2e roles.spec
  Currency changed after historical sales ......... CurrencyHistoryTests (here)
  Report spanning a currency change ............... CurrencyHistoryTests (here), reports tests
  Refund against the original sale ................ payments tests, e2e critical.spec
  Cash session closed with a discrepancy .......... cash_management tests, e2e critical.spec
  Another business's records ...................... CrossBusinessTests (here)
  Two users changing one stock balance at once .... ConcurrentStockTests (here)
"""
import threading
from decimal import Decimal

from django.db import close_old_connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.inventory.models import Inventory, StockMovement
from apps.inventory.services import record_inflow, record_outflow
from apps.products.models import Product, ProductPrice
from apps.products.test_catalog import CatalogBase
from apps.sales.models import Sale
from apps.settings.models import BusinessSettings, Currency


class CompletedSaleTests(CatalogBase):
    def test_no_role_can_edit_or_delete_a_completed_sale(self):
        self.client.force_authenticate(self.cashier)
        sale = self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": [{"product_id": self.latte.id, "quantity": "1"}],
            "payments": [{"method": "cash", "amount": "2200"}],
        }, format="json").data
        for user in (self.cashier, self.manager, self.owner):
            self.client.force_authenticate(user)
            url = f"/api/sales/{sale['id']}/"
            self.assertEqual(self.client.patch(url, {"total_amount": "1"}, format="json").status_code, 405)
            self.assertEqual(self.client.put(url, {}, format="json").status_code, 405)
            self.assertEqual(self.client.delete(url).status_code, 405)
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.post(f"/api/sales/{sale['id']}/refund/", {}, format="json").status_code, 403)
        self.assertEqual(self.client.post(f"/api/sales/{sale['id']}/cancel/", {}, format="json").status_code, 403)
        self.assertEqual(Decimal(Sale.objects.get(pk=sale["id"]).total_amount), Decimal("2200"))


class CrossBusinessTests(CatalogBase):
    """Every record of business B is invisible to business A — not found, not forbidden."""

    def setUp(self):
        super().setUp()
        self.other = Business.objects.create(name="Rival Roasters")
        BusinessSettings.objects.create(business=self.other, primary_currency=Currency.objects.get(code="RWF"))
        self.other_branch = Branch.objects.create(business=self.other, name="Rival Main")
        self.other_owner = User.objects.create_user(username="rival", password="Passw0rd!",
                                                    role=UserRole.OWNER, business=self.other)
        self.other_product = Product.objects.create(business=self.other, name="Rival Latte", sku="RL")
        ProductPrice.objects.create(product=self.other_product, selling_price=Decimal("100"))
        record_inflow(branch=self.other_branch, product=self.other_product, quantity=Decimal("5"),
                      performed_by=self.other_owner)
        c = APIClient()
        c.force_authenticate(self.other_owner)
        self.other_sale = c.post("/api/sales/", {
            "branch": self.other_branch.id,
            "items": [{"product_id": self.other_product.id, "quantity": "1"}],
        }, format="json").data["id"]

    def test_reads_and_writes_on_another_business_find_nothing(self):
        self.client.force_authenticate(self.owner)
        inv = Inventory.objects.get(branch=self.other_branch)
        for url in (f"/api/sales/{self.other_sale}/", f"/api/products/{self.other_product.id}/",
                    f"/api/branches/{self.other_branch.id}/", f"/api/inventory/{inv.id}/",
                    f"/api/auth/users/{self.other_owner.id}/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.patch(f"/api/products/{self.other_product.id}/", {"name": "x"},
                                           format="json").status_code, 404)
        self.assertEqual(self.client.post(f"/api/sales/{self.other_sale}/refund/", {},
                                          format="json").status_code, 404)

    def test_lists_and_reports_never_include_another_business(self):
        self.client.force_authenticate(self.owner)
        sales = self.client.get("/api/sales/").data["results"]
        self.assertNotIn(self.other_sale, [s["id"] for s in sales])
        products = self.client.get("/api/products/", {"search": "Rival"}).data["results"]
        self.assertEqual(products, [])
        res = self.client.get("/api/reports/sales/summary/", {"branch": self.other_branch.id})
        self.assertEqual(res.status_code, 403)

    def test_cannot_sell_or_move_stock_at_another_business_branch(self):
        before = Inventory.objects.get(branch=self.other_branch).quantity
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/sales/", {
            "branch": self.other_branch.id,
            "items": [{"product_id": self.latte.id, "quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/stock-movements/inflow/", {
            "branch": self.other_branch.id, "product_id": self.other_product.id, "quantity": "1",
        }, format="json")
        self.assertIn(res.status_code, (400, 403))
        self.assertEqual(Inventory.objects.get(branch=self.other_branch).quantity, before)


class CurrencyHistoryTests(CatalogBase):
    def test_sales_keep_their_currency_and_reports_keep_them_apart(self):
        self.client.force_authenticate(self.owner)
        before = self.client.post("/api/sales/", {
            "branch": self.branch.id, "items": [{"product_id": self.latte.id, "quantity": "1"}],
            "payments": [{"method": "cash", "amount": "2200"}],
        }, format="json").data
        res = self.client.post("/api/settings/me/change-currency/",
                               {"currency_code": "USD", "confirm": True}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        after = self.client.post("/api/sales/", {
            "branch": self.branch.id, "items": [{"product_id": self.latte.id, "quantity": "1"}],
            "payments": [{"method": "cash", "amount": "2200"}],
        }, format="json").data
        self.assertEqual((before["currency_code"], after["currency_code"]), ("RWF", "USD"))
        # The old sale is unchanged (no conversion, CUR-017/BRULE-020).
        self.assertEqual(Sale.objects.get(pk=before["id"]).currency_code, "RWF")
        rows = self.client.get("/api/reports/sales/summary/").data["results"]
        self.assertEqual(sorted(r["currency_code"] for r in rows), ["RWF", "USD"])
        self.assertTrue(all(r["transactions"] == 1 for r in rows))


class ConcurrentStockTests(TransactionTestCase):
    """Two people take stock out of the same balance at the same moment."""

    def setUp(self):
        self.business = Business.objects.create(name="Race Cafe")
        BusinessSettings.objects.create(business=self.business, primary_currency=Currency.objects.get_or_create(
            code="RWF", defaults={"name": "Rwandan Franc", "decimal_places": 0})[0])
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.users = []
        for name in ("a", "b"):
            u = User.objects.create_user(username=name, password="Passw0rd!",
                                         role=UserRole.INVENTORY_MANAGER, business=self.business)
            UserBranch.objects.create(user=u, branch=self.branch)
            self.users.append(u)
        self.product = Product.objects.create(business=self.business, name="Beans", sku="B")
        record_inflow(branch=self.branch, product=self.product, quantity=Decimal("5"),
                      performed_by=self.users[0])

    def test_balance_never_goes_negative_and_matches_the_ledger(self):
        barrier = threading.Barrier(2)
        outcomes = []

        def take(user):
            try:
                barrier.wait()
                record_outflow(branch=self.branch, product=self.product, quantity=Decimal("3"),
                               movement_type="damaged", reason="race", performed_by=user)
                outcomes.append("ok")
            except Exception as exc:  # the loser sees "insufficient stock"
                outcomes.append(type(exc).__name__)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=take, args=(u,)) for u in self.users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(outcomes), ["ValidationError", "ok"])
        inv = Inventory.objects.get(branch=self.branch, product=self.product)
        self.assertEqual(inv.quantity, Decimal("2"))
        ledger = sum(m.delta for m in StockMovement.objects.filter(product=self.product))
        self.assertEqual(ledger, inv.quantity)
