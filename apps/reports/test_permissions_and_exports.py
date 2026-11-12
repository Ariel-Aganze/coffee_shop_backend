"""
Tests for the Phase 1 completion work:
  - Owner dashboard endpoint (SRS 5.3)
  - Report export to CSV / PDF and receipt PDF (REP-016/017, POS-016)
  - #10 discount limits for cashiers (POS-008, POS-020)
  - #11 branch managers managing their own staff + account audit trail
  - #12 role-based report access, accountant scope, cashier "own sales"
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.expenses.models import Expense, ExpenseCategory, ExpenseStatus
from apps.inventory.models import Inventory
from apps.inventory.services import record_inflow
from apps.products.models import Category, Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency


class Base(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=Currency.objects.get(code="RWF"),
        )
        self.branch_a = Branch.objects.create(business=self.business, name="Alpha")
        self.branch_b = Branch.objects.create(business=self.business, name="Beta")

        def user(name, role, *branches):
            u = User.objects.create_user(username=name, password="Passw0rd!",
                                         role=role, business=self.business)
            for b in branches:
                UserBranch.objects.create(user=u, branch=b)
            return u

        self.owner = user("owner", UserRole.OWNER)
        self.admin = user("admin", UserRole.ADMIN)
        self.manager_a = user("manager_a", UserRole.BRANCH_MANAGER, self.branch_a)
        self.manager_b = user("manager_b", UserRole.BRANCH_MANAGER, self.branch_b)
        self.cashier_a = user("cashier_a", UserRole.CASHIER, self.branch_a)
        self.cashier_a2 = user("cashier_a2", UserRole.CASHIER, self.branch_a)
        self.cashier_b = user("cashier_b", UserRole.CASHIER, self.branch_b)
        self.inventory_a = user("inventory_a", UserRole.INVENTORY_MANAGER, self.branch_a)
        self.accountant = user("accountant", UserRole.ACCOUNTANT)

        cat = Category.objects.create(business=self.business, name="Drinks")
        self.latte = Product.objects.create(business=self.business, category=cat,
                                            name="Latte", sku="LAT")
        self.muffin = Product.objects.create(business=self.business, category=cat,
                                             name="Muffin", sku="MUF")
        for p, price, cost in ((self.latte, "2000", "800"), (self.muffin, "1500", "500")):
            ProductPrice.objects.create(product=p, selling_price=Decimal(price),
                                        cost_price=Decimal(cost), effective_from=timezone.now())
        self.client = APIClient()

    def _as(self, user):
        self.client.force_authenticate(user)

    def _sale(self, user, branch, qty="1", product=None, discount=None, pay=True):
        self._as(user)
        item = {"product_id": (product or self.latte).id, "quantity": qty}
        if discount is not None:
            item["discount_amount"] = discount
        resp = self.client.post("/api/sales/", {"branch": branch.id, "items": [item]},
                                format="json")
        if resp.status_code != 201:
            return resp
        if pay:
            self.client.post(f"/api/sales/{resp.data['id']}/payments/", {
                "payments": [{"method": "cash", "amount": resp.data["total_amount"]}],
            }, format="json")
        return resp


# ==============================================================================
# Dashboard
# ==============================================================================
class DashboardTests(Base):
    def test_owner_dashboard_figures(self):
        self._sale(self.cashier_a, self.branch_a, "2")   # 4000 at Alpha
        self._sale(self.cashier_b, self.branch_b, "1")   # 2000 at Beta
        self._sale(self.cashier_a, self.branch_a, "1", pay=False)  # unpaid: ignored

        self._as(self.owner)
        resp = self.client.get("/api/reports/dashboard/")
        self.assertEqual(resp.status_code, 200, resp.data)
        d = resp.data

        self.assertEqual(d["currency"]["code"], "RWF")
        self.assertIsNone(d["scope"]["branch_ids"])
        self.assertEqual(Decimal(d["sales"]["today"]["net_sales"]), Decimal("6000"))
        self.assertEqual(d["sales"]["today"]["transactions"], 2)

        ov = d["overview"]
        self.assertEqual(Decimal(ov["revenue"]), Decimal("6000"))
        self.assertEqual(Decimal(ov["cost_of_goods_sold"]), Decimal("2400"))
        self.assertEqual(Decimal(ov["gross_profit"]), Decimal("3600"))
        self.assertEqual(Decimal(ov["average_transaction_value"]), Decimal("3000"))

        bp = d["branch_performance"]
        self.assertEqual(bp["best_branch"]["branch_name"], "Alpha")
        self.assertEqual(bp["lowest_branch"]["branch_name"], "Beta")

        self.assertEqual(d["products"]["best_selling"][0]["product_name"], "Latte")
        # Unsold active products show up as lowest sellers.
        self.assertEqual(d["products"]["lowest_selling"][0]["product_name"], "Muffin")

        today = timezone.localdate().isoformat()
        trend_today = [x for x in d["sales_trend"]["days"] if x["date"] == today][0]
        self.assertEqual(Decimal(trend_today["net_sales"]), Decimal("6000"))
        self.assertEqual(len(d["operations"]["recent_transactions"]), 3)

    def test_inventory_and_operations_sections(self):
        record_inflow(branch=self.branch_a, product=self.latte,
                      quantity=Decimal("3"), performed_by=self.owner)
        Inventory.objects.filter(product=self.latte).update(minimum_threshold=Decimal("5"))
        cat = ExpenseCategory.objects.create(business=self.business, name="Rent")
        Expense.objects.create(business=self.business, branch=self.branch_a, category=cat,
                               amount=Decimal("700"), currency_code="RWF",
                               description="x", submitted_by=self.manager_a,
                               status=ExpenseStatus.PENDING)
        self._as(self.cashier_a)
        self.client.post("/api/cash-sessions/open/",
                         {"branch": self.branch_a.id, "opening_balance": "1000"},
                         format="json")

        self._as(self.owner)
        d = self.client.get("/api/reports/dashboard/").data
        inv = d["inventory"]
        self.assertEqual(inv["low_stock_count"], 1)
        self.assertEqual(Decimal(inv["stock_valuation"]), Decimal("2400"))  # 3 × 800
        ops = d["operations"]
        self.assertEqual(len(ops["active_cash_sessions"]), 1)
        self.assertEqual(Decimal(ops["active_cash_sessions"][0]["expected_cash"]), Decimal("1000"))
        self.assertEqual(ops["pending_approvals"]["expenses"][0]["count"], 1)
        self.assertTrue(ops["recent_activity"])

    def test_branch_filter(self):
        self._sale(self.cashier_a, self.branch_a, "2")
        self._sale(self.cashier_b, self.branch_b, "1")
        self._as(self.owner)
        d = self.client.get(f"/api/reports/dashboard/?branch={self.branch_b.id}").data
        self.assertEqual(d["scope"]["branch_ids"], [self.branch_b.id])
        self.assertEqual(Decimal(d["overview"]["revenue"]), Decimal("2000"))
        self.assertIsNone(d["branch_performance"]["best_branch"])  # single branch

    def test_branch_manager_scoped_and_cannot_request_other_branch(self):
        self._sale(self.cashier_a, self.branch_a, "2")
        self._sale(self.cashier_b, self.branch_b, "1")
        self._as(self.manager_a)
        d = self.client.get("/api/reports/dashboard/").data
        self.assertEqual(Decimal(d["overview"]["revenue"]), Decimal("4000"))
        resp = self.client.get(f"/api/reports/dashboard/?branch={self.branch_b.id}")
        self.assertEqual(resp.status_code, 403)

    def test_cashier_and_inventory_manager_have_no_dashboard(self):
        for user in (self.cashier_a, self.inventory_a):
            self._as(user)
            self.assertEqual(self.client.get("/api/reports/dashboard/").status_code, 403)

    def test_other_currency_flagged_not_mixed(self):
        self._sale(self.cashier_a, self.branch_a, "1")  # RWF
        self._as(self.owner)
        self.client.post("/api/settings/me/change-currency/",
                         {"currency_code": "USD", "confirm": True}, format="json")
        d = self.client.get("/api/reports/dashboard/").data
        self.assertEqual(d["currency"]["code"], "USD")
        self.assertEqual(d["other_currencies_in_period"], ["RWF"])
        self.assertEqual(Decimal(d["overview"]["revenue"]), Decimal("0"))


# ==============================================================================
# Exports
# ==============================================================================
class ExportTests(Base):
    def test_csv_export(self):
        self._sale(self.cashier_a, self.branch_a, "2")
        self._as(self.owner)
        resp = self.client.get("/api/reports/sales/summary/?export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", resp["Content-Disposition"])
        body = resp.content.decode("utf-8-sig")
        self.assertIn("Sales summary", body)
        self.assertIn("Test Cafe", body)
        self.assertIn("All branches", body)
        self.assertIn("Currency code", body)
        self.assertIn("RWF", body)
        self.assertTrue(AuditLog.objects.filter(action="report_exported").exists())

    def test_csv_export_includes_extra_tables(self):
        cat = ExpenseCategory.objects.create(business=self.business, name="Rent")
        Expense.objects.create(business=self.business, branch=self.branch_a, category=cat,
                               amount=Decimal("700"), currency_code="RWF", description="x",
                               submitted_by=self.owner, status=ExpenseStatus.APPROVED)
        self._as(self.owner)
        body = self.client.get("/api/reports/expenses/?export=csv").content.decode("utf-8-sig")
        self.assertIn("Approved totals", body)

    def test_pdf_export(self):
        self._sale(self.cashier_a, self.branch_a, "2")
        self._as(self.owner)
        resp = self.client.get("/api/reports/financial/summary/?export=pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_pdf_export_with_no_rows(self):
        self._as(self.owner)
        resp = self.client.get("/api/reports/inventory/low-stock/?export=pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_bad_export_format_and_dashboard_export_rejected(self):
        self._as(self.owner)
        self.assertEqual(
            self.client.get("/api/reports/sales/summary/?export=xml").status_code, 400)
        self.assertEqual(
            self.client.get("/api/reports/dashboard/?export=csv").status_code, 400)

    def test_export_respects_permissions(self):
        self._as(self.cashier_a)
        resp = self.client.get("/api/reports/financial/summary/?export=csv")
        self.assertEqual(resp.status_code, 403)

    def test_receipt_pdf(self):
        sale = self._sale(self.cashier_a, self.branch_a, "2", discount="0").data
        self._as(self.cashier_a)
        resp = self.client.get(f"/api/sales/{sale['id']}/receipt/?export=pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))
        self.assertIn("inline;", resp["Content-Disposition"])
        self.assertIn(sale["transaction_number"], resp["Content-Disposition"])

        resp = self.client.get(f"/api/sales/{sale['id']}/receipt/?export=pdf&download=1")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertEqual(
            self.client.get(f"/api/sales/{sale['id']}/receipt/?export=csv").status_code, 400)


# ==============================================================================
# #10 Discounts
# ==============================================================================
class DiscountTests(Base):
    def test_cashier_cannot_discount_by_default(self):
        resp = self._sale(self.cashier_a, self.branch_a, "1", discount="100", pay=False)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("limit", str(resp.data))

    def test_cashier_discount_within_configured_limit(self):
        self.settings.cashier_max_discount_percent = Decimal("10")
        self.settings.save()
        ok = self._sale(self.cashier_a, self.branch_a, "1", discount="200", pay=False)
        self.assertEqual(ok.status_code, 201)  # exactly 10% of 2000
        too_much = self._sale(self.cashier_a, self.branch_a, "1", discount="201", pay=False)
        self.assertEqual(too_much.status_code, 400)

    def test_manager_and_owner_unlimited(self):
        for user, branch in ((self.manager_a, self.branch_a), (self.owner, self.branch_b)):
            resp = self._sale(user, branch, "1", discount="1500", pay=False)
            self.assertEqual(resp.status_code, 201, resp.data)
            self.assertEqual(Decimal(resp.data["total_amount"]), Decimal("500"))

    def test_owner_sets_limit_through_settings(self):
        self._as(self.owner)
        resp = self.client.patch(f"/api/settings/me/",
                                 {"cashier_max_discount_percent": "15"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Decimal(resp.data["cashier_max_discount_percent"]), Decimal("15"))
        resp = self.client.patch(f"/api/settings/me/",
                                 {"cashier_max_discount_percent": "150"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self._as(self.manager_a)
        resp = self.client.patch(f"/api/settings/me/",
                                 {"cashier_max_discount_percent": "50"}, format="json")
        self.assertEqual(resp.status_code, 403)


# ==============================================================================
# #11 Employee management
# ==============================================================================
class EmployeeManagementTests(Base):
    URL = "/api/auth/users/"

    def _create(self, actor, **overrides):
        self._as(actor)
        body = {"username": "newbie", "password": "Str0ng-Passw0rd!",
                "role": "cashier", "branch_ids": [self.branch_a.id], **overrides}
        return self.client.post(self.URL, body, format="json")

    def test_branch_manager_creates_cashier_in_own_branch(self):
        resp = self._create(self.manager_a)
        self.assertEqual(resp.status_code, 201, resp.data)
        user = User.objects.get(username="newbie")
        self.assertEqual(user.assigned_branch_ids, [self.branch_a.id])
        log = AuditLog.objects.get(action="user_created")
        self.assertEqual(log.new_value["role"], "cashier")

    def test_branch_manager_limits_on_create(self):
        self.assertEqual(self._create(self.manager_a, branch_ids=[self.branch_b.id]).status_code, 400)
        self.assertEqual(self._create(self.manager_a, branch_ids=[]).status_code, 400)
        self.assertEqual(self._create(self.manager_a, role="branch_manager").status_code, 400)
        self.assertEqual(self._create(self.manager_a, role="accountant").status_code, 400)

    def test_branch_manager_lists_only_own_branch_staff(self):
        self._as(self.manager_a)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        names = {u["username"] for u in resp.data["results"]}
        self.assertEqual(names, {"cashier_a", "cashier_a2", "inventory_a"})

    def test_branch_manager_updates_and_deactivates_own_cashier(self):
        self._as(self.manager_a)
        resp = self.client.patch(f"{self.URL}{self.cashier_a.id}/",
                                 {"role": "inventory_manager"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        log = AuditLog.objects.get(action="user_permissions_changed")
        self.assertEqual(log.previous_value["role"], "cashier")
        self.assertEqual(log.new_value["role"], "inventory_manager")

        resp = self.client.delete(f"{self.URL}{self.cashier_a2.id}/")
        self.assertEqual(resp.status_code, 204)
        self.cashier_a2.refresh_from_db()
        self.assertFalse(self.cashier_a2.is_active)
        self.assertTrue(AuditLog.objects.filter(action="user_deactivated").exists())

    def test_branch_manager_cannot_promote_or_touch_other_staff(self):
        self._as(self.manager_a)
        resp = self.client.patch(f"{self.URL}{self.cashier_a.id}/",
                                 {"role": "branch_manager"}, format="json")
        self.assertEqual(resp.status_code, 400)
        # Staff of another branch are invisible.
        resp = self.client.patch(f"{self.URL}{self.cashier_b.id}/",
                                 {"first_name": "X"}, format="json")
        self.assertEqual(resp.status_code, 404)
        # A fellow manager sharing the branch can't be edited.
        UserBranch.objects.create(user=self.manager_b, branch=self.branch_a)
        resp = self.client.patch(f"{self.URL}{self.manager_b.id}/",
                                 {"first_name": "X"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_branch_manager_keeps_assignments_outside_their_branches(self):
        UserBranch.objects.create(user=self.cashier_a, branch=self.branch_b)
        self._as(self.manager_a)
        resp = self.client.patch(f"{self.URL}{self.cashier_a.id}/",
                                 {"branch_ids": []}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.cashier_a.assigned_branch_ids, [self.branch_b.id])

    def test_admin_cannot_change_owner(self):
        self._as(self.admin)
        resp = self.client.delete(f"{self.URL}{self.owner.id}/")
        self.assertEqual(resp.status_code, 403)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_nobody_can_grant_owner_role(self):
        self._as(self.owner)
        resp = self.client.patch(f"{self.URL}{self.manager_a.id}/",
                                 {"role": "owner"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_cashier_cannot_manage_users(self):
        self._as(self.cashier_a)
        self.assertEqual(self.client.get(self.URL).status_code, 403)

    def test_branch_manager_cannot_edit_branch(self):
        self._as(self.manager_a)
        resp = self.client.patch(f"/api/branches/{self.branch_a.id}/",
                                 {"name": "Renamed"}, format="json")
        self.assertEqual(resp.status_code, 403)


# ==============================================================================
# #12 Report access and data scope
# ==============================================================================
class ReportAccessTests(Base):
    def setUp(self):
        super().setUp()
        self._sale(self.cashier_a, self.branch_a, "2")    # 4000
        self._sale(self.cashier_a2, self.branch_a, "1")   # 2000
        self._sale(self.cashier_b, self.branch_b, "1")    # 2000

    def _get(self, user, path):
        self._as(user)
        return self.client.get(f"/api/reports/{path}")

    def test_cashier_sees_only_own_sales_in_reports(self):
        resp = self._get(self.cashier_a, "sales/summary/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data["results"][0]
        self.assertEqual(row["transactions"], 1)
        self.assertEqual(Decimal(row["net_sales"]), Decimal("4000"))

    def test_cashier_blocked_from_financial_and_inventory_reports(self):
        for path in ("financial/summary/", "expenses/", "cash/reconciliation/",
                     "sales/by-cashier/", "branches/compare/",
                     "inventory/current-stock/"):
            self.assertEqual(self._get(self.cashier_a, path).status_code, 403, path)

    def test_inventory_manager_only_inventory_reports(self):
        self.assertEqual(self._get(self.inventory_a, "inventory/current-stock/").status_code, 200)
        self.assertEqual(self._get(self.inventory_a, "inventory/movements/").status_code, 200)
        for path in ("sales/summary/", "financial/summary/", "expenses/"):
            self.assertEqual(self._get(self.inventory_a, path).status_code, 403, path)

    def test_unassigned_accountant_sees_whole_business(self):
        resp = self._get(self.accountant, "sales/by-branch/")
        self.assertEqual(len(resp.data["results"]), 2)
        self.assertEqual(self._get(self.accountant, "branches/compare/").status_code, 200)
        fin = self._get(self.accountant, "financial/summary/").data["results"][0]
        self.assertEqual(Decimal(fin["revenue"]), Decimal("8000"))

    def test_assigned_accountant_is_limited_to_their_branches(self):
        UserBranch.objects.create(user=self.accountant, branch=self.branch_b)
        resp = self._get(self.accountant, "sales/by-branch/")
        self.assertEqual([r["branch_id"] for r in resp.data["results"]], [self.branch_b.id])
        self.assertEqual(self._get(self.accountant, "branches/compare/").status_code, 403)

    def test_admin_without_branches_sees_no_financial_data(self):
        resp = self._get(self.admin, "sales/summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["results"], [])

    def test_branch_filter_on_reports(self):
        resp = self._get(self.owner, f"sales/summary/?branch={self.branch_b.id}")
        self.assertEqual(Decimal(resp.data["results"][0]["net_sales"]), Decimal("2000"))
        resp = self._get(self.manager_a, f"sales/summary/?branch={self.branch_b.id}")
        self.assertEqual(resp.status_code, 403)
        resp = self._get(self.owner, "sales/summary/?branch=abc")
        self.assertEqual(resp.status_code, 400)

    def test_sales_list_visibility(self):
        def count(user):
            self._as(user)
            return self.client.get("/api/sales/").data["count"]

        self.assertEqual(count(self.cashier_a), 1)     # own transactions only
        self.assertEqual(count(self.manager_a), 2)     # own branch
        self.assertEqual(count(self.accountant), 3)    # whole business
        self.assertEqual(count(self.inventory_a), 0)   # no access to sales
