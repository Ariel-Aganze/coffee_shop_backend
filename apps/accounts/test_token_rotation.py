"""Refresh-token rotation: a used refresh token can't be used again."""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.businesses.models import Business


class TokenRotationTests(TestCase):
    def setUp(self):
        business = Business.objects.create(name="Test Cafe")
        User.objects.create_user(username="owner", password="Passw0rd!",
                                 role=UserRole.OWNER, business=business)
        self.client = APIClient()
        resp = self.client.post("/api/auth/login/",
                                {"username": "owner", "password": "Passw0rd!"}, format="json")
        self.refresh = resp.data["refresh"]

    def _refresh(self, token):
        return self.client.post("/api/auth/token/refresh/", {"refresh": token}, format="json")

    def test_refresh_returns_new_tokens(self):
        resp = self._refresh(self.refresh)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertNotEqual(resp.data["refresh"], self.refresh)

    def test_old_refresh_token_is_rejected_after_rotation(self):
        new = self._refresh(self.refresh).data["refresh"]
        self.assertEqual(self._refresh(self.refresh).status_code, 401)
        self.assertEqual(self._refresh(new).status_code, 200)

    def test_logout_invalidates_refresh_token(self):
        access = self.client.post("/api/auth/login/", {"username": "owner", "password": "Passw0rd!"},
                                  format="json").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.client.post("/api/auth/logout/", {"refresh": self.refresh},
                                          format="json").status_code, 204)
        self.client.credentials()
        self.assertEqual(self._refresh(self.refresh).status_code, 401)
