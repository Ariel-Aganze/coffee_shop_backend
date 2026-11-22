"""
Business onboarding (SRS 12.1), the public currency catalogue, settings
validation and logo upload. Errors use the API envelope:
{"error": "...", "details": {section: {field: [messages]}}}.
"""
import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.settings.models import BusinessSettings, Currency

URL = "/api/businesses/register/"


def payload(**overrides):
    data = {
        "business": {"name": "Inzozi Coffee", "contact_email": "hello@inzozi.example",
                     "contact_phone": "+250 788 000 000", "address": "Kigali"},
        "owner": {"username": "jeanne", "email": "jeanne@inzozi.example",
                  "password": "Espresso-Shot-42", "first_name": "Jeanne", "last_name": "Uwera"},
        "settings": {"currency_code": "rwf", "language": "en", "timezone": "Africa/Kigali",
                     "date_format": "DD/MM/YYYY", "number_format": "1 234,56"},
        "first_branch": {"name": "Kimihurura", "address": "KG 5 Ave", "phone": ""},
    }
    for section, values in overrides.items():
        data[section] = {**data[section], **values}
    return data


class RegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def register(self, **overrides):
        return self.client.post(URL, payload(**overrides), format="json")

    def errors(self, resp, section):
        self.assertEqual(resp.status_code, 400, resp.data)
        return resp.data["details"][section]

    def test_creates_business_owner_settings_and_branch(self):
        resp = self.register()
        self.assertEqual(resp.status_code, 201, resp.data)
        business = Business.objects.get(name="Inzozi Coffee")
        owner = User.objects.get(username="jeanne")
        self.assertEqual(owner.role, UserRole.OWNER)
        self.assertEqual(owner.business, business)
        settings = BusinessSettings.objects.get(business=business)
        self.assertEqual(settings.primary_currency.code, "RWF")  # code normalised
        self.assertEqual((settings.timezone, settings.date_format, settings.number_format),
                         ("Africa/Kigali", "DD/MM/YYYY", "1 234,56"))
        self.assertTrue(Branch.objects.filter(business=business, name="Kimihurura").exists())
        self.assertTrue(AuditLog.objects.filter(action="business_registered").exists())
        # The owner can sign in straight away.
        login = self.client.post("/api/auth/login/", {"username": "jeanne",
                                                      "password": "Espresso-Shot-42"}, format="json")
        self.assertEqual(login.status_code, 200)

    def test_nothing_is_created_when_any_section_is_invalid(self):
        self.register(first_branch={"name": ""})
        self.assertFalse(Business.objects.filter(name="Inzozi Coffee").exists())
        self.assertFalse(User.objects.filter(username="jeanne").exists())

    def test_errors_are_reported_per_field(self):
        resp = self.register(owner={"email": "not-an-email"}, first_branch={"name": ""})
        self.assertIn("email", self.errors(resp, "owner"))
        self.assertIn("name", resp.data["details"]["first_branch"])

    def test_username_and_email_are_unique_ignoring_case(self):
        User.objects.create_user(username="Jeanne", email="JEANNE@inzozi.example", password="x")
        owner = self.errors(self.register(), "owner")
        self.assertIn("already taken", str(owner["username"]))
        self.assertIn("already exists", str(owner["email"]))

    def test_owner_password_follows_all_password_rules(self):
        weak = self.errors(self.register(owner={"password": "12345678"}), "owner")
        self.assertIn("password", weak)
        similar = self.errors(self.register(owner={"password": "Jeanne2026"}), "owner")
        self.assertIn("similar", str(similar["password"]))

    def test_settings_choices_are_validated(self):
        errors = self.errors(self.register(settings={
            "currency_code": "XYZ", "timezone": "Mars/Olympus",
            "date_format": "YY.MM.DD", "language": "xx",
        }), "settings")
        self.assertEqual(set(errors), {"currency_code", "timezone", "date_format", "language"})

    def test_business_name_must_be_unique(self):
        Business.objects.create(name="Inzozi Coffee")
        self.assertIn("name", self.errors(self.register(), "business"))


class CurrencyCatalogueTests(TestCase):
    def test_public_and_searchable(self):
        client = APIClient()  # not signed in: the wizard needs it before sign-up
        resp = client.get("/api/settings/currencies/", {"search": "rwanda"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c["code"] for c in resp.data], ["RWF"])
        codes = [c["code"] for c in client.get("/api/settings/currencies/").data]
        self.assertEqual(codes.count("XAF"), 1)  # shared currencies listed once (SRS 5.14.3)
        self.assertGreaterEqual(len(codes), 50)


TEST_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class SettingsAndLogoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    def setUp(self):
        self.client = APIClient()
        self.client.post(URL, payload(), format="json")
        self.owner = User.objects.get(username="jeanne")
        self.client.force_authenticate(self.owner)

    def png(self, size=(64, 64), name="logo.png"):
        buffer = io.BytesIO()
        Image.new("RGB", size, (100, 73, 66)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_settings_reject_unknown_choices(self):
        resp = self.client.patch("/api/settings/me/",
                                 {"date_format": "YY.MM.DD", "timezone": "Nowhere/City"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(set(resp.data["details"]), {"date_format", "timezone"})
        ok = self.client.patch("/api/settings/me/", {"timezone": "Africa/Lagos"}, format="json")
        self.assertEqual(ok.status_code, 200)

    def test_logo_upload_and_removal(self):
        resp = self.client.patch("/api/businesses/me/", {"logo": self.png()}, format="multipart")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("/media/business/logos/", resp.data["logo"])
        removed = self.client.patch("/api/businesses/me/", {"logo": None}, format="json")
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertIsNone(removed.data["logo"])

    def test_logo_limits(self):
        big = SimpleUploadedFile("big.png", b"0" * (2 * 1024 * 1024 + 1), content_type="image/png")
        resp = self.client.patch("/api/businesses/me/", {"logo": big}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        not_image = SimpleUploadedFile("logo.png", b"not an image", content_type="image/png")
        resp = self.client.patch("/api/businesses/me/", {"logo": not_image}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    def test_currency_history_is_a_plain_list(self):
        # Documented as an unpaginated list in the API schema; keep them in sync.
        self.client.post("/api/settings/me/change-currency/",
                         {"currency_code": "USD", "confirm": True, "reason": "Expansion"}, format="json")
        resp = self.client.get("/api/settings/me/currency-history/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(resp.data[0]["reason"], "Expansion")
