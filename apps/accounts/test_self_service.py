"""
Errors use the API's envelope: {"error": "...", "details": {field: [messages]}}.

Self-service account endpoints: own profile, change password, reset password.
Changing or resetting a password signs out every other device.
"""
from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.businesses.models import Business

PASSWORD = "Str0ng-Passw0rd!"


class SelfServiceBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Cafe")
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.user = User.objects.create_user(
            username="cashier", email="cashier@example.com", password=PASSWORD,
            first_name="Diane", last_name="Mukamana",
            role=UserRole.CASHIER, business=self.business,
        )
        UserBranch.objects.create(user=self.user, branch=self.branch)
        User.objects.create_user(username="other", email="taken@example.com",
                                 password=PASSWORD, business=self.business)
        self.client = APIClient()

    def login(self, client=None):
        client = client or APIClient()
        tokens = client.post("/api/auth/login/", {"username": "cashier", "password": PASSWORD},
                             format="json").data
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        return client, tokens

    def refresh_ok(self, refresh):
        return APIClient().post("/api/auth/token/refresh/", {"refresh": refresh},
                                format="json").status_code == 200


class ProfileTests(SelfServiceBase):
    def test_update_own_contact_details(self):
        client, _ = self.login()
        resp = client.patch("/api/auth/me/", {"first_name": "Dina", "phone": "+250 788 000 111"},
                            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["first_name"], "Dina")
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+250 788 000 111")
        log = AuditLog.objects.get(action="profile_updated")
        self.assertEqual(log.previous_value, {"first_name": "Diane", "phone": ""})

    def test_cannot_change_own_role_or_activation(self):
        client, _ = self.login()
        resp = client.patch("/api/auth/me/", {"role": "owner", "is_active": False,
                                              "first_name": "Dina"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, UserRole.CASHIER)  # ignored
        self.assertTrue(self.user.is_active)

    def test_email_must_be_unique(self):
        client, _ = self.login()
        resp = client.patch("/api/auth/me/", {"email": "TAKEN@example.com"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("email", resp.data["details"])

    def test_requires_authentication(self):
        self.assertEqual(APIClient().patch("/api/auth/me/", {}, format="json").status_code, 401)


class ChangePasswordTests(SelfServiceBase):
    def test_change_password_keeps_this_device_and_signs_out_others(self):
        other_device = APIClient()
        _, other_tokens = self.login(other_device)
        client, _ = self.login()

        resp = client.post("/api/auth/change-password/",
                           {"old_password": PASSWORD, "new_password": "N3w-Sturdy-Pass!"},
                           format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(self.refresh_ok(resp.data["refresh"]))       # this device: new tokens
        self.assertFalse(self.refresh_ok(other_tokens["refresh"]))   # other device: revoked
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3w-Sturdy-Pass!"))

    def test_wrong_current_password(self):
        client, _ = self.login()
        resp = client.post("/api/auth/change-password/",
                           {"old_password": "nope", "new_password": "N3w-Sturdy-Pass!"},
                           format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("old_password", resp.data["details"])

    def test_rejects_password_similar_to_the_user(self):
        client, _ = self.login()
        resp = client.post("/api/auth/change-password/",
                           {"old_password": PASSWORD, "new_password": "diane.mukamana"},
                           format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("similar", str(resp.data["details"]["new_password"]))

    def test_rejects_reusing_the_same_password(self):
        client, _ = self.login()
        resp = client.post("/api/auth/change-password/",
                           {"old_password": PASSWORD, "new_password": PASSWORD}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("different", str(resp.data["details"]["new_password"]))


class ResetPasswordTests(SelfServiceBase):
    def _reset_link_params(self):
        APIClient().post("/api/auth/password-reset/", {"email": "cashier@example.com"},
                         format="json")
        query = mail.outbox[-1].body.split("reset-password?", 1)[1].split()[0]
        return dict(part.split("=", 1) for part in query.split("&"))

    def test_reset_signs_out_every_device(self):
        _, tokens = self.login()
        params = self._reset_link_params()
        resp = APIClient().post("/api/auth/password-reset/confirm/",
                                {**params, "new_password": "N3w-Sturdy-Pass!"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(self.refresh_ok(tokens["refresh"]))

    def test_invalid_link_is_reported_on_token(self):
        resp = APIClient().post("/api/auth/password-reset/confirm/",
                                {"uid": "MQ", "token": "bad", "new_password": "N3w-Sturdy-Pass!"},
                                format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("token", resp.data["details"])

    def test_link_works_once(self):
        params = self._reset_link_params()
        first = APIClient().post("/api/auth/password-reset/confirm/",
                                 {**params, "new_password": "N3w-Sturdy-Pass!"}, format="json")
        second = APIClient().post("/api/auth/password-reset/confirm/",
                                  {**params, "new_password": "Another-Pass-99"}, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)  # token changes with the password hash

    def test_weak_password_reported_on_field(self):
        params = self._reset_link_params()
        resp = APIClient().post("/api/auth/password-reset/confirm/",
                                {**params, "new_password": "12345678"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("new_password", resp.data["details"])

    def test_deactivated_user_cannot_reset(self):
        params = self._reset_link_params()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        resp = APIClient().post("/api/auth/password-reset/confirm/",
                                {**params, "new_password": "N3w-Sturdy-Pass!"}, format="json")
        self.assertEqual(resp.status_code, 400)


class UnexpectedErrorTests(TestCase):
    """Crashes are logged and never leak internals to the client."""

    def test_unhandled_exception_is_logged_and_hidden(self):
        from apps.accounts.exceptions import custom_exception_handler

        with self.assertLogs("apps.accounts.exceptions", level="ERROR") as logs:
            resp = custom_exception_handler(RuntimeError("db password is hunter2"), {"view": None})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.data, {"error": "Internal server error", "details": {}})
        self.assertIn("hunter2", "\n".join(logs.output))  # in our logs, not the response
