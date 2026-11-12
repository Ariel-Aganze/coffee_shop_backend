"""
Regression tests for the reporting, cash and stock fixes:
  5. Sales summary no longer multiplies sales by their number of payments.
  6. Revenue excludes unpaid sales and is net of refunds.
  7. Cash refunds reduce the drawer the money actually came from.
  8. Cancelling (or fully refunding with restock) a sale restores its stock.
  9. Manual stock adjustments require a reason in both directions.
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.inventory.models import Inventory, StockMovement, StockMovementType
from apps.inventory.services import record_inflow
from apps.payments.models import Payment
from apps.products.models import Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class FixTestBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        BusinessSettings.objects.create(
            business=self.business, primary_currency=Currency.objects.get(code="RWF"),
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
        for u in (self.manager, self.cashier):
            UserBranch.objects.create(user=u, branch=self.branch)

        cat = Category.objects.create(business=self.business, name="Drinks")
        self.product = Product.objects.create(
            business=self.business, category=cat, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(
            product=self.product, selling_price=Decimal("2000"),
            cost_price=Decimal("800"), effective_from=timezone.now(),
        )
        # Track stock for the product: 10 units on hand.
        record_inflow(branch=self.branch, product=self.product,
                      quantity=Decimal("10"), performed_by=self.owner)
        self.client = APIClient()

    # ---------- helpers ----------
    def _as(self, user):
        self.client.force_authenticate(user)

    def _sale(self, user, quantity="2"):
        self._as(user)
        resp = self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": [{"product_id": self.product.id, "quantity": quantity}],
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data

    def _pay(self, user, sale_id, payments):
        self._as(user)
        resp = self.client.post(f"/api/sales/{sale_id}/payments/",
                                {"payments": payments}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def _refund(self, user, sale_id, **body):
        self._as(user)
        return self.client.post(f"/api/sales/{sale_id}/refund/", body, format="json")

    def _open_session(self, user, opening="10000"):
        self._as(user)
        resp = self.client.post("/api/cash-sessions/open/", {
            "branch": self.branch.id, "opening_balance": opening,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def _expected_cash(self, session_id):
        self._as(self.owner)
        resp = self.client.get(f"/api/cash-sessions/{session_id}/reconciliation/")
        return Decimal(resp.data["expected_cash"])

    def _report(self, path):
        self._as(self.owner)
        resp = self.client.get(f"/api/reports/{path}")
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def _stock(self):
        return Inventory.objects.get(branch=self.branch, product=self.product).quantity


class ReportingFixTests(FixTestBase):
    # ---------- #5 ----------
    def test_split_payment_sale_counted_once(self):
        sale = self._sale(self.cashier, "2")  # 4000
        self._pay(self.cashier, sale["id"], [
            {"method": "cash", "amount": "1500"},
            {"method": "mobile_money", "amount": "1500", "reference": "MM1"},
            {"method": "bank_card", "amount": "1000", "reference": "CARD1"},
        ])
        row = self._report("sales/summary/")["results"][0]
        self.assertEqual(row["transactions"], 1)
        self.assertEqual(Decimal(row["total_sales"]), Decimal("4000"))
        self.assertEqual(Decimal(row["net_sales"]), Decimal("4000"))

        branch_row = self._report("sales/by-branch/")["results"][0]
        self.assertEqual(branch_row["transactions"], 1)
        self.assertEqual(Decimal(branch_row["total_sales"]), Decimal("4000"))

    # ---------- #6 ----------
    def test_unpaid_sale_is_not_revenue(self):
        paid = self._sale(self.cashier, "1")
        self._pay(self.cashier, paid["id"], [{"method": "cash", "amount": "2000"}])
        self._sale(self.cashier, "3")  # never paid

        row = self._report("sales/summary/")["results"][0]
        self.assertEqual(row["transactions"], 1)
        self.assertEqual(Decimal(row["total_sales"]), Decimal("2000"))
        self.assertEqual(row["unpaid_sales_count"], 1)
        self.assertEqual(Decimal(row["unpaid_sales_total"]), Decimal("6000"))

        fin = self._report("financial/summary/")["results"][0]
        self.assertEqual(Decimal(fin["revenue"]), Decimal("2000"))
        self.assertEqual(Decimal(fin["cost_of_goods_sold"]), Decimal("800"))

        product = self._report("sales/by-product/")["results"][0]
        self.assertEqual(Decimal(product["quantity_sold"]), Decimal("1"))

    def test_full_refund_removes_revenue(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        self.assertEqual(self._refund(self.manager, sale["id"]).status_code, 200)

        row = self._report("sales/summary/")["results"][0]
        self.assertEqual(Decimal(row["total_sales"]), Decimal("4000"))
        self.assertEqual(Decimal(row["refunds_total"]), Decimal("4000"))
        self.assertEqual(Decimal(row["net_sales"]), Decimal("0"))

        fin = self._report("financial/summary/")["results"][0]
        self.assertEqual(Decimal(fin["revenue"]), Decimal("0"))
        self.assertEqual(Decimal(fin["cost_of_goods_sold"]), Decimal("0"))

        self.assertEqual(self._report("sales/by-product/")["results"], [])
        compare = self._report("branches/compare/")["results"][0]
        self.assertEqual(Decimal(compare["revenue"]), Decimal("0"))

    def test_partial_refund_is_netted(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        self._refund(self.manager, sale["id"], amount="1000")

        row = self._report("sales/summary/")["results"][0]
        self.assertEqual(Decimal(row["net_sales"]), Decimal("3000"))
        fin = self._report("financial/summary/")["results"][0]
        self.assertEqual(Decimal(fin["revenue"]), Decimal("3000"))
        self.assertEqual(Decimal(fin["gross_profit"]), Decimal("1400"))  # 3000 - 1600
        cashier_row = self._report("sales/by-cashier/")["results"][0]
        self.assertEqual(Decimal(cashier_row["net_sales"]), Decimal("3000"))

    def test_payment_method_report_separates_refunds(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        self._refund(self.manager, sale["id"], amount="1000")

        row = self._report("sales/by-payment-method/")["results"][0]
        self.assertEqual(Decimal(row["collected"]), Decimal("4000"))
        self.assertEqual(Decimal(row["refunded"]), Decimal("1000"))
        self.assertEqual(Decimal(row["net"]), Decimal("3000"))
        self.assertEqual(row["count"], 1)


class CashRefundFixTests(FixTestBase):
    # ---------- #7 ----------
    def test_manager_refund_reduces_cashiers_drawer(self):
        cashier_session = self._open_session(self.cashier)
        manager_session = self._open_session(self.manager, opening="5000")
        sale = self._sale(self.cashier, "1")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "2000"}])
        self.assertEqual(self._expected_cash(cashier_session), Decimal("12000"))

        self.assertEqual(self._refund(self.manager, sale["id"]).status_code, 200)

        self.assertEqual(self._expected_cash(cashier_session), Decimal("10000"))
        self.assertEqual(self._expected_cash(manager_session), Decimal("5000"))

    def test_refund_after_cashier_closed_uses_refunders_drawer(self):
        cashier_session = self._open_session(self.cashier)
        sale = self._sale(self.cashier, "1")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "2000"}])
        self._as(self.cashier)
        self.client.post(f"/api/cash-sessions/{cashier_session}/close/",
                         {"actual_cash": "12000"}, format="json")

        manager_session = self._open_session(self.manager, opening="5000")
        self.assertEqual(self._refund(self.manager, sale["id"]).status_code, 200)
        self.assertEqual(self._expected_cash(manager_session), Decimal("3000"))

    def test_cash_paid_without_session_never_enters_a_later_session(self):
        sale = self._sale(self.cashier, "1")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "2000"}])
        session = self._open_session(self.cashier)
        self.assertEqual(self._expected_cash(session), Decimal("10000"))


class StockFixTests(FixTestBase):
    # ---------- #8 ----------
    def test_cancel_restores_stock(self):
        sale = self._sale(self.cashier, "3")
        self.assertEqual(self._stock(), Decimal("7"))
        self._as(self.manager)
        resp = self.client.post(f"/api/sales/{sale['id']}/cancel/",
                                {"reason": "wrong order"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._stock(), Decimal("10"))
        ret = StockMovement.objects.get(movement_type=StockMovementType.RETURN)
        self.assertEqual(ret.delta, Decimal("3"))
        self.assertEqual(ret.sale_id, sale["id"])

    def test_cancel_of_untracked_product_is_fine(self):
        Inventory.objects.all().delete()
        StockMovement.objects.all().delete()
        sale = self._sale(self.cashier, "1")
        self._as(self.manager)
        resp = self.client.post(f"/api/sales/{sale['id']}/cancel/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

    def test_refund_without_restock_keeps_stock_out(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        self._refund(self.manager, sale["id"])
        self.assertEqual(self._stock(), Decimal("8"))

    def test_full_refund_with_restock_restores_stock(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        resp = self._refund(self.manager, sale["id"], restock=True, reason="sealed")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self._stock(), Decimal("10"))

    def test_partial_refund_cannot_restock(self):
        sale = self._sale(self.cashier, "2")
        self._pay(self.cashier, sale["id"], [{"method": "cash", "amount": "4000"}])
        resp = self._refund(self.manager, sale["id"], amount="1000", restock=True)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._stock(), Decimal("8"))
        # The refund itself was rejected too — no refund payment was recorded.
        self.assertFalse(Payment.objects.filter(sale_id=sale["id"], is_refund=True).exists())

    # ---------- #9 ----------
    def test_inflow_adjustment_requires_reason(self):
        self._as(self.manager)
        resp = self.client.post("/api/stock-movements/inflow/", {
            "branch": self.branch.id, "product_id": self.product.id,
            "quantity": "5", "movement_type": "adjustment",
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._stock(), Decimal("10"))

        resp = self.client.post("/api/stock-movements/inflow/", {
            "branch": self.branch.id, "product_id": self.product.id,
            "quantity": "5", "movement_type": "adjustment", "reason": "Found a box",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(self._stock(), Decimal("15"))

    def test_purchase_inflow_still_needs_no_reason(self):
        self._as(self.manager)
        resp = self.client.post("/api/stock-movements/inflow/", {
            "branch": self.branch.id, "product_id": self.product.id,
            "quantity": "5",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
