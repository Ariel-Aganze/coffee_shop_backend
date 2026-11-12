"""
Regression tests for the permission / security fixes:
  1. Business settings & currency cannot be changed by non-owners via PATCH.
  2. Password reset never returns the uid/token in the API response.
  3. Only authorized roles can create product prices.
  4. Only owner/admin can edit the business profile.
"""
from decimal import Decimal

from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.products.models import Product, ProductPrice
from apps.settings.models import BusinessSettings, Currency, CurrencyChangeLog


class SecurityFixTests(TestCase):
    def setUp(self):
        self.rwf = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        self.settings = BusinessSettings.objects.create(
            business=self.business, primary_currency=self.rwf,
        )
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="Passw0rd!",
            role=UserRole.OWNER, business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", email="cashier@example.com", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        self.manager = User.objects.create_user(
            username="manager", password="Passw0rd!",
            role=UserRole.BRANCH_MANAGER, business=self.business,
        )
        for u in (self.cashier, self.manager):
            UserBranch.objects.create(user=u, branch=self.branch)
        self.product = Product.objects.create(
            business=self.business, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(product=self.product, selling_price=Decimal("2000"))
        self.client = APIClient()

    # ---------------- #1 Business settings / currency ----------------
    def test_cashier_cannot_patch_settings(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.patch(f"/api/settings/me/{self.settings.id}/", {"timezone": "Africa/Kigali"},
                                 format="json")
        self.assertEqual(resp.status_code, 403)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.timezone, "UTC")

    def test_patch_cannot_change_currency_even_for_owner(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(f"/api/settings/me/{self.settings.id}/",
                                 {"primary_currency_code": "USD"}, format="json")
        self.assertEqual(resp.status_code, 200)  # field is ignored, not applied
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.primary_currency.code, "RWF")
        self.assertFalse(CurrencyChangeLog.objects.exists())

    def test_owner_can_patch_regional_settings_and_it_is_audited(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(f"/api/settings/me/{self.settings.id}/", {"timezone": "Africa/Kigali"},
                                 format="json")
        self.assertEqual(resp.status_code, 200)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.timezone, "Africa/Kigali")
        self.assertTrue(
            AuditLog.objects.filter(action="business_settings_updated").exists()
        )

    def test_branch_manager_cannot_change_currency(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post("/api/settings/me/change-currency/",
                                {"currency_code": "USD", "confirm": True},
                                format="json")
        self.assertEqual(resp.status_code, 403)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.primary_currency.code, "RWF")

    def test_owner_currency_change_flow_still_works(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post("/api/settings/me/change-currency/",
                                {"currency_code": "USD", "confirm": True},
                                format="json")
        self.assertEqual(resp.status_code, 200)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.primary_currency.code, "USD")
        self.assertEqual(CurrencyChangeLog.objects.count(), 1)

    # ---------------- #2 Password reset ----------------
    def test_password_reset_does_not_leak_token(self):
        resp = self.client.post("/api/auth/password-reset/",
                                {"email": "cashier@example.com"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("uid", resp.data)
        self.assertNotIn("token", resp.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cashier@example.com"])
        self.assertIn("reset-password?uid=", mail.outbox[0].body)

    def test_password_reset_same_response_for_unknown_email(self):
        known = self.client.post("/api/auth/password-reset/",
                                 {"email": "cashier@example.com"}, format="json")
        unknown = self.client.post("/api/auth/password-reset/",
                                   {"email": "nobody@example.com"}, format="json")
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data, unknown.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_emailed_link_resets_password(self):
        self.client.post("/api/auth/password-reset/",
                         {"email": "cashier@example.com"}, format="json")
        body = mail.outbox[0].body
        query = body.split("reset-password?", 1)[1].split()[0]
        params = dict(p.split("=", 1) for p in query.split("&"))
        resp = self.client.post("/api/auth/password-reset/confirm/", {
            "uid": params["uid"], "token": params["token"],
            "new_password": "N3w-Secure-Pass!",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.check_password("N3w-Secure-Pass!"))

    # ---------------- #3 Product prices ----------------
    def test_cashier_cannot_create_price(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.post(f"/api/products/{self.product.id}/prices/",
                                {"selling_price": "1"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.product.prices.count(), 1)

    def test_cashier_can_still_read_prices(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(f"/api/products/{self.product.id}/prices/")
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_create_price(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/products/{self.product.id}/prices/",
                                {"selling_price": "2500"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.product.prices.count(), 2)

    # ---------------- #4 Business profile ----------------
    def test_cashier_cannot_edit_business(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.patch(f"/api/businesses/me/{self.business.id}/",
                                 {"name": "Hijacked"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.business.refresh_from_db()
        self.assertEqual(self.business.name, "Test Cafe")

    def test_cashier_can_view_business(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get(f"/api/businesses/me/{self.business.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_owner_can_edit_business(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(f"/api/businesses/me/{self.business.id}/",
                                 {"name": "Renamed Cafe"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.business.refresh_from_db()
        self.assertEqual(self.business.name, "Renamed Cafe")
