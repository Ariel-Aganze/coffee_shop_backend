from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.settings.models import BusinessSettings, Currency


class AuthFlowTests(TestCase):
    def setUp(self):
        self.currency = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=self.currency,
        )
        self.branch_a = Branch.objects.create(business=self.business, name="A")
        self.branch_b = Branch.objects.create(business=self.business, name="B")
        self.owner = User.objects.create_user(
            username="owner", password="Passw0rd!", role=UserRole.OWNER,
            business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!", role=UserRole.CASHIER,
            business=self.business,
        )
        UserBranch.objects.create(user=self.cashier, branch=self.branch_a)

        self.client = APIClient()

    def _login(self, username, password):
        resp = self.client.post("/api/auth/login/",
                                {"username": username, "password": password},
                                format="json")
        return resp

    def test_login_success(self):
        resp = self._login("owner", "Passw0rd!")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)

    def test_login_invalid(self):
        resp = self._login("owner", "wrong")
        self.assertEqual(resp.status_code, 401)

    def test_inactive_user_blocked(self):
        self.cashier.is_active = False
        self.cashier.save()
        resp = self._login("cashier", "Passw0rd!")
        self.assertEqual(resp.status_code, 403)

    def test_cashier_cannot_access_other_branch(self):
        resp = self._login("cashier", "Passw0rd!")
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        branch_b_resp = self.client.get(f"/api/branches/{self.branch_b.id}/")
        self.assertEqual(branch_b_resp.status_code, 404)

        branch_a_resp = self.client.get(f"/api/branches/{self.branch_a.id}/")
        self.assertEqual(branch_a_resp.status_code, 200)

    def test_owner_sees_all_branches(self):
        resp = self._login("owner", "Passw0rd!")
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = self.client.get("/api/branches/")
        self.assertEqual(resp.status_code, 200)
        names = {b["name"] for b in resp.data["results"]}
        self.assertEqual(names, {"A", "B"})

    def test_cashier_cannot_create_user(self):
        resp = self._login("cashier", "Passw0rd!")
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = self.client.post("/api/auth/users/", {
            "username": "newcashier",
            "email": "nc@example.com",
            "password": "Passw0rd!",
            "role": "cashier",
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_create_user(self):
        resp = self._login("owner", "Passw0rd!")
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = self.client.post("/api/auth/users/", {
            "username": "newcashier",
            "email": "nc@example.com",
            "password": "Passw0rd!",
            "role": "cashier",
            "branch_ids": [self.branch_a.id],
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        user = User.objects.get(username="newcashier")
        self.assertEqual(user.business_id, self.business.id)
        self.assertEqual(user.assigned_branch_ids, [self.branch_a.id])

    def test_logout_blacklists_refresh(self):
        resp = self._login("owner", "Passw0rd!")
        access = resp.data["access"]
        refresh = resp.data["refresh"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = self.client.post("/api/auth/logout/", {"refresh": refresh}, format="json")
        self.assertEqual(resp.status_code, 204)

        # Try to use the blacklisted refresh
        resp2 = self.client.post("/api/auth/token/refresh/",
                                 {"refresh": refresh}, format="json")
        self.assertEqual(resp2.status_code, 401)

    def test_deleted_staff_is_deactivated(self):
        resp = self._login("owner", "Passw0rd!")
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        resp = self.client.delete(f"/api/auth/users/{self.cashier.id}/")
        self.assertEqual(resp.status_code, 204)
        self.cashier.refresh_from_db()
        self.assertFalse(self.cashier.is_active)