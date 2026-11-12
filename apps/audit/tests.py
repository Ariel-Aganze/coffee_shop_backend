"""
Audit trail API (AUD-009), immutability (AUD-010), and the /me/ routes.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.settings.models import BusinessSettings, Currency

from .models import AuditLog, AuditLogImmutableError
from .services import log_action

URL = "/api/audit/"


class AuditApiTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=Currency.objects.get(code="RWF"),
            timezone="Africa/Kigali",
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
        self.admin = user("admin", UserRole.ADMIN, self.branch_a)
        self.accountant = user("accountant", UserRole.ACCOUNTANT)
        self.manager_a = user("manager_a", UserRole.BRANCH_MANAGER, self.branch_a)
        self.inventory_a = user("inventory_a", UserRole.INVENTORY_MANAGER, self.branch_a)
        self.cashier_a = user("cashier_a", UserRole.CASHIER, self.branch_a)

        # Another business — must never leak.
        other = Business.objects.create(name="Other Cafe")
        log_action(business=other, action="sale_created", target_type="Sale", target_id="999")

        self.sale_a = log_action(business=self.business, branch=self.branch_a,
                                 user=self.cashier_a, action="sale_created",
                                 target_type="Sale", target_id="1",
                                 metadata={"transaction_number": "TXN-ALPH-0001"})
        self.refund_a = log_action(business=self.business, branch=self.branch_a,
                                   user=self.manager_a, action="sale_refunded",
                                   target_type="Sale", target_id="1",
                                   metadata={"reason": "cold coffee"})
        self.stock_a = log_action(business=self.business, branch=self.branch_a,
                                  user=self.inventory_a, action="stock_movement",
                                  target_type="Inventory", target_id="1:1")
        self.sale_b = log_action(business=self.business, branch=self.branch_b,
                                 user=self.owner, action="sale_created",
                                 target_type="Sale", target_id="2")
        self.currency = log_action(business=self.business, user=self.owner,
                                   action="currency_changed",
                                   target_type="BusinessSettings", target_id="1",
                                   previous_value={"code": "RWF"},
                                   new_value={"code": "USD"})
        self.client = APIClient()

    def _ids(self, user, query=""):
        self.client.force_authenticate(user)
        resp = self.client.get(f"{URL}{query}")
        self.assertEqual(resp.status_code, 200, resp.data)
        return {row["id"] for row in resp.data["results"]}

    # ---------- Visibility per role ----------
    def test_owner_sees_whole_business_only(self):
        self.assertEqual(
            self._ids(self.owner),
            {self.sale_a.id, self.refund_a.id, self.stock_a.id, self.sale_b.id, self.currency.id},
        )

    def test_unassigned_accountant_sees_whole_business(self):
        self.assertIn(self.currency.id, self._ids(self.accountant))
        self.assertIn(self.sale_b.id, self._ids(self.accountant))

    def test_branch_manager_and_admin_limited_to_their_branches(self):
        expected = {self.sale_a.id, self.refund_a.id, self.stock_a.id}
        self.assertEqual(self._ids(self.manager_a), expected)
        self.assertEqual(self._ids(self.admin), expected)  # no business-level records

    def test_inventory_manager_sees_only_stock_activity(self):
        self.assertEqual(self._ids(self.inventory_a), {self.stock_a.id})

    def test_cashier_has_no_access(self):
        self.client.force_authenticate(self.cashier_a)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_retrieve_respects_scope(self):
        self.client.force_authenticate(self.manager_a)
        self.assertEqual(self.client.get(f"{URL}{self.sale_b.id}/").status_code, 404)
        resp = self.client.get(f"{URL}{self.refund_a.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["user"]["username"], "manager_a")
        self.assertEqual(resp.data["branch"]["name"], "Alpha")

    # ---------- Search & filters ----------
    def test_filters(self):
        o = self.owner
        self.assertEqual(self._ids(o, "?action=sale_created"), {self.sale_a.id, self.sale_b.id})
        self.assertEqual(self._ids(o, "?action=sale_refunded,currency_changed"),
                         {self.refund_a.id, self.currency.id})
        self.assertEqual(self._ids(o, f"?user={self.manager_a.id}"), {self.refund_a.id})
        self.assertEqual(self._ids(o, f"?branch={self.branch_b.id}"), {self.sale_b.id})
        # A record's full history: target_type + target_id.
        self.assertEqual(self._ids(o, "?target_type=sale&target_id=1"),
                         {self.sale_a.id, self.refund_a.id})

    def test_free_text_search(self):
        self.assertEqual(self._ids(self.owner, "?search=cold coffee"), {self.refund_a.id})
        self.assertEqual(self._ids(self.owner, "?search=TXN-ALPH"), {self.sale_a.id})
        self.assertEqual(self._ids(self.owner, "?search=manager_a"), {self.refund_a.id})

    def test_date_range_uses_business_timezone(self):
        # Created "yesterday" in Kigali time.
        old = log_action(business=self.business, user=self.owner, action="old_event")
        # The ORM refuses to modify audit rows, so backdate it with raw SQL.
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("UPDATE audit_auditlog SET created_at = %s WHERE id = %s",
                        [timezone.now() - timedelta(days=1), old.pk])
        from zoneinfo import ZoneInfo
        today = timezone.now().astimezone(ZoneInfo("Africa/Kigali")).date().isoformat()
        ids_today = self._ids(self.owner, f"?from={today}&to={today}")
        self.assertNotIn(old.id, ids_today)
        self.assertIn(self.sale_a.id, ids_today)

    def test_branch_filter_outside_scope_is_forbidden(self):
        self.client.force_authenticate(self.manager_a)
        resp = self.client.get(f"{URL}?branch={self.branch_b.id}")
        self.assertEqual(resp.status_code, 403)

    def test_bad_filter_values(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f"{URL}?from=12-11-2026").status_code, 400)
        self.assertEqual(self.client.get(f"{URL}?user=abc").status_code, 400)

    def test_actions_listing(self):
        self.client.force_authenticate(self.manager_a)
        resp = self.client.get(f"{URL}actions/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, ["sale_created", "sale_refunded", "stock_movement"])

    def test_previous_and_new_values_exposed(self):
        self.client.force_authenticate(self.owner)
        row = self.client.get(f"{URL}{self.currency.id}/").data
        self.assertEqual(row["previous_value"], {"code": "RWF"})
        self.assertEqual(row["new_value"], {"code": "USD"})

    # ---------- Immutability (AUD-010) ----------
    def test_api_is_read_only(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(URL, {"action": "x"}, format="json").status_code, 405)
        self.assertEqual(
            self.client.patch(f"{URL}{self.sale_a.id}/", {"action": "x"}, format="json").status_code,
            405,
        )
        self.assertEqual(self.client.delete(f"{URL}{self.sale_a.id}/").status_code, 405)

    def test_model_refuses_changes(self):
        self.sale_a.action = "tampered"
        with self.assertRaises(AuditLogImmutableError):
            self.sale_a.save()
        with self.assertRaises(AuditLogImmutableError):
            self.sale_a.delete()
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.sale_a.pk).update(action="tampered")
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.all().delete()
        self.assertEqual(AuditLog.objects.get(pk=self.sale_a.pk).action, "sale_created")

    def test_admin_site_is_read_only(self):
        from django.contrib.admin.sites import site
        model_admin = site._registry[AuditLog]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))


class SingletonRouteTests(TestCase):
    """/api/settings/me/ and /api/businesses/me/ work without an id."""

    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=Currency.objects.get(code="RWF"),
        )
        self.owner = User.objects.create_user(username="owner", password="Passw0rd!",
                                              role=UserRole.OWNER, business=self.business)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_settings_me(self):
        resp = self.client.get("/api/settings/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["primary_currency"]["code"], "RWF")
        resp = self.client.patch("/api/settings/me/", {"language": "fr"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["language"], "fr")
        self.assertEqual(self.client.get("/api/settings/me/currency-history/").status_code, 200)
        resp = self.client.post("/api/settings/me/change-currency/",
                                {"currency_code": "USD", "confirm": True}, format="json")
        self.assertEqual(resp.status_code, 200)

    def test_business_me(self):
        resp = self.client.get("/api/businesses/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Test Cafe")
        resp = self.client.patch("/api/businesses/me/", {"contact_phone": "+250700000000"},
                                 format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["contact_phone"], "+250700000000")

    def test_old_id_routes_are_gone(self):
        self.assertEqual(self.client.get(f"/api/settings/me/{self.settings.id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/businesses/me/{self.business.id}/").status_code, 404)

    def test_put_and_delete_not_allowed(self):
        self.assertEqual(self.client.delete("/api/settings/me/").status_code, 405)
        self.assertEqual(self.client.put("/api/businesses/me/", {}, format="json").status_code, 405)

    def test_user_without_business_gets_404_not_500(self):
        loner = User.objects.create_user(username="loner", password="Passw0rd!",
                                         role=UserRole.OWNER)
        self.client.force_authenticate(loner)
        self.assertEqual(self.client.get("/api/settings/me/").status_code, 404)
        self.assertEqual(self.client.get("/api/businesses/me/").status_code, 404)
