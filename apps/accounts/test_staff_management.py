"""
Staff list filters, set-password, unique emails/usernames; branch list,
duplicate names and deactivation guard.
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.cash_management.models import CashSession

PASSWORD = "Str0ng-Passw0rd!"
USERS = "/api/auth/users/"


class Base(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.alpha = Branch.objects.create(business=self.business, name="Alpha")
        self.beta = Branch.objects.create(business=self.business, name="Beta")

        def user(username, role, *branches, **extra):
            u = User.objects.create_user(username=username, password=PASSWORD, role=role,
                                         business=self.business, **extra)
            for b in branches:
                UserBranch.objects.create(user=u, branch=b)
            return u

        self.owner = user("owner", UserRole.OWNER)
        self.manager = user("manager", UserRole.BRANCH_MANAGER, self.alpha,
                            first_name="Patrick", last_name="Nkurunziza")
        self.cashier = user("cashier", UserRole.CASHIER, self.alpha,
                            first_name="Diane", last_name="Mukamana", email="diane@cafe.example")
        self.cashier_b = user("cashier_b", UserRole.CASHIER, self.beta, first_name="Kevin")
        self.inactive = user("gone", UserRole.CASHIER, self.alpha, is_active=False)
        self.client = APIClient()

    def as_(self, user):
        self.client.force_authenticate(user)


class StaffListTests(Base):
    def names(self, query=""):
        resp = self.client.get(USERS + query)
        self.assertEqual(resp.status_code, 200, resp.data)
        return [u["username"] for u in resp.data["results"]]

    def test_filters(self):
        self.as_(self.owner)
        self.assertEqual(self.names("?search=muka"), ["cashier"])
        self.assertEqual(self.names("?search=diane@"), ["cashier"])
        self.assertEqual(set(self.names("?role=cashier")), {"cashier", "cashier_b", "gone"})
        self.assertEqual(set(self.names(f"?branch={self.beta.id}")), {"cashier_b"})
        self.assertNotIn("gone", self.names("?is_active=true"))
        self.assertEqual(self.names("?is_active=false"), ["gone"])

    def test_sorted_by_name_and_includes_last_login(self):
        self.as_(self.owner)
        resp = self.client.get(USERS + "?is_active=true")
        self.assertEqual([u["first_name"] for u in resp.data["results"]], ["Diane", "Kevin", "Patrick"])
        self.assertIn("last_login", resp.data["results"][0])

    def test_branch_manager_sees_own_branch_only(self):
        self.as_(self.manager)
        self.assertEqual(set(self.names()), {"cashier", "gone"})
        self.assertEqual(self.names(f"?branch={self.beta.id}"), [])

    def test_list_query_count_does_not_grow_with_staff(self):
        self.as_(self.owner)
        with CaptureQueriesContext(connection) as small:
            self.client.get(USERS)
        for i in range(10):
            u = User.objects.create_user(username=f"extra{i}", password=PASSWORD,
                                         role=UserRole.CASHIER, business=self.business)
            UserBranch.objects.create(user=u, branch=self.alpha)
        with CaptureQueriesContext(connection) as big:
            self.client.get(USERS)
        self.assertEqual(len(small), len(big))


class StaffCreateTests(Base):
    def create(self, **overrides):
        body = {"username": "newbie", "password": PASSWORD, "role": "cashier",
                "email": "newbie@cafe.example", "branch_ids": [self.alpha.id], **overrides}
        return self.client.post(USERS, body, format="json")

    def test_unique_username_and_email_any_case(self):
        self.as_(self.owner)
        resp = self.create(username="CASHIER", email="DIANE@cafe.example")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(set(resp.data["details"]), {"username", "email"})

    def test_password_rules_apply(self):
        self.as_(self.owner)
        resp = self.create(password="12345678")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("password", resp.data["details"])
        resp = self.create(username="mutesi", first_name="Mutesi", password="mutesi2026")
        self.assertIn("similar", str(resp.data["details"]["password"]))


class SetPasswordTests(Base):
    def url(self, user):
        return f"{USERS}{user.id}/set-password/"

    def test_manager_sets_cashier_password_and_signs_them_out(self):
        login = APIClient().post("/api/auth/login/", {"username": "cashier", "password": PASSWORD},
                                 format="json").data
        self.as_(self.manager)
        resp = self.client.post(self.url(self.cashier), {"new_password": "Fresh-Beans-77"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.check_password("Fresh-Beans-77"))
        refreshed = APIClient().post("/api/auth/token/refresh/", {"refresh": login["refresh"]},
                                     format="json")
        self.assertEqual(refreshed.status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="user_password_set").exists())

    def test_limits(self):
        self.as_(self.manager)
        # other branch: invisible
        self.assertEqual(self.client.post(self.url(self.cashier_b), {"new_password": "Fresh-Beans-77"},
                                          format="json").status_code, 404)
        self.as_(self.owner)
        weak = self.client.post(self.url(self.cashier), {"new_password": "diane123"}, format="json")
        self.assertEqual(weak.status_code, 400)
        self.as_(self.cashier)
        self.assertEqual(self.client.post(self.url(self.cashier_b), {"new_password": "Fresh-Beans-77"},
                                          format="json").status_code, 403)


class BranchTests(Base):
    def test_list_with_counts_managers_and_filters(self):
        self.as_(self.owner)
        Branch.objects.create(business=self.business, name="Closed One", is_active=False)
        resp = self.client.get("/api/branches/")
        rows = {b["name"]: b for b in resp.data["results"]}
        self.assertEqual(rows["Alpha"]["staff_count"], 2)  # manager + cashier (inactive excluded)
        self.assertEqual(rows["Alpha"]["managers"], [{"id": self.manager.id, "name": "Patrick Nkurunziza"}])
        self.assertEqual(list(rows)[-1], "Closed One")  # inactive last
        names = [b["name"] for b in self.client.get("/api/branches/?is_active=true&search=al").data["results"]]
        self.assertEqual(names, ["Alpha"])

    def test_duplicate_name_is_a_clear_error(self):
        self.as_(self.owner)
        resp = self.client.post("/api/branches/", {"name": "  alpha "}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.data["details"])
        rename = self.client.patch(f"/api/branches/{self.beta.id}/", {"name": "ALPHA"}, format="json")
        self.assertEqual(rename.status_code, 400)
        same = self.client.patch(f"/api/branches/{self.alpha.id}/", {"name": "Alpha"}, format="json")
        self.assertEqual(same.status_code, 200)

    def test_cannot_deactivate_with_open_cash_sessions(self):
        CashSession.objects.create(branch=self.alpha, cashier=self.cashier,
                                   opening_balance=Decimal("0"), currency_code="RWF")
        self.as_(self.owner)
        resp = self.client.delete(f"/api/branches/{self.alpha.id}/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("still open", str(resp.data["details"]))
        ok = self.client.delete(f"/api/branches/{self.beta.id}/")
        self.assertEqual(ok.status_code, 204)
        self.beta.refresh_from_db()
        self.assertFalse(self.beta.is_active)
        reactivate = self.client.patch(f"/api/branches/{self.beta.id}/", {"is_active": True}, format="json")
        self.assertEqual(reactivate.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="branch_deactivated").exists())
