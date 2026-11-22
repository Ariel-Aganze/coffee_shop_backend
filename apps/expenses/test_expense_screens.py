"""Expense screens: categories, filing rules, corrections, review and totals."""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.expenses.models import Expense, ExpenseCategory
from apps.products.test_catalog import CatalogBase

TEMP_MEDIA = tempfile.mkdtemp(prefix="caffio-test-expenses-")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ExpenseBase(CatalogBase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.rent = ExpenseCategory.objects.create(business=self.business, name="Rent")
        self.old = ExpenseCategory.objects.create(business=self.business, name="Old",
                                                  is_active=False)

    def file(self, user, **data):
        self.client.force_authenticate(user)
        body = {"category": self.rent.id, "amount": "15000", "description": "Milk",
                "branch": self.branch.id, **data}
        fmt = "multipart" if "attachment" in data else "json"
        return self.client.post("/api/expenses/", body, format=fmt)


class CategoryTests(ExpenseBase):
    def test_duplicate_name_and_soft_delete(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/expense-categories/", {"name": " rent "}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("name", res.data["details"])
        self.file(self.manager)
        self.client.force_authenticate(self.owner)
        res = self.client.delete(f"/api/expense-categories/{self.rent.id}/")
        self.assertEqual(res.status_code, 204)
        self.rent.refresh_from_db()
        self.assertFalse(self.rent.is_active)
        self.assertEqual(Expense.objects.filter(category=self.rent).count(), 1)
        listed = self.client.get("/api/expense-categories/", {"is_active": "false"}).data["results"]
        self.assertEqual({c["name"]: c["expense_count"] for c in listed}, {"Old": 0, "Rent": 1})


class FilingTests(ExpenseBase):
    def test_branch_staff_must_pick_their_branch(self):
        res = self.file(self.manager, branch=None)
        self.assertEqual(res.status_code, 400)
        self.assertIn("branch", res.data["details"])
        res = self.file(self.manager, branch=self.other_branch.id)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.file(self.owner, branch=None).status_code, 201)  # central

    def test_no_future_dates_and_shop_day_default(self):
        tomorrow = (timezone.now() + timedelta(days=2)).date().isoformat()
        res = self.file(self.manager, expense_date=tomorrow)
        self.assertEqual(res.status_code, 400)
        self.assertIn("expense_date", res.data["details"])
        res = self.file(self.manager)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["submitted_by_name"], "manager")

    def test_attachment_type_and_link(self):
        res = self.file(self.manager, attachment=SimpleUploadedFile(
            "virus.exe", b"MZ", content_type="application/octet-stream"))
        self.assertEqual(res.status_code, 400)
        self.assertIn("attachment", res.data["details"])
        res = self.file(self.manager, attachment=SimpleUploadedFile(
            "receipt.pdf", b"%PDF-1.4", content_type="application/pdf"))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data["attachment"].startswith("http"))
        self.assertTrue(res.data["attachment_name"].endswith(".pdf"))


class CorrectionAndReviewTests(ExpenseBase):
    def test_submitter_corrects_pending_then_it_is_final(self):
        exp_id = self.file(self.manager).data["id"]
        self.client.force_authenticate(self.manager)
        res = self.client.patch(f"/api/expenses/{exp_id}/", {"amount": "12000"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Decimal(res.data["amount"]), Decimal("12000"))
        log = AuditLog.objects.get(action="expense_updated")
        self.assertEqual(log.metadata["before"], {"amount": "15000.0000"})

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(f"/api/expenses/{exp_id}/approve/", {}, format="json")
                         .status_code, 200)
        self.client.force_authenticate(self.manager)
        res = self.client.patch(f"/api/expenses/{exp_id}/", {"amount": "1"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_others_cannot_correct_it(self):
        exp_id = self.file(self.manager).data["id"]
        self.client.force_authenticate(self.inventory)  # same branch, not the submitter
        res = self.client.patch(f"/api/expenses/{exp_id}/", {"amount": "1"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_rejection_needs_a_reason(self):
        exp_id = self.file(self.inventory).data["id"]
        self.client.force_authenticate(self.manager)
        res = self.client.post(f"/api/expenses/{exp_id}/reject/", {"note": ""}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(f"/api/expenses/{exp_id}/reject/", {"note": "No receipt"},
                               format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["reviewed_by_name"], "manager")


class SummaryTests(ExpenseBase):
    def test_totals_follow_the_filters(self):
        a = self.file(self.manager, amount="10000").data["id"]
        self.file(self.manager, amount="2500")
        self.file(self.owner, amount="999", branch=self.other_branch.id)
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/expenses/{a}/approve/", {}, format="json")
        data = self.client.get("/api/expenses/summary/", {"branch": self.branch.id}).data
        self.assertEqual(Decimal(data["approved_total"]), Decimal("10000"))
        self.assertEqual(data["pending_count"], 1)
        self.assertEqual(Decimal(data["pending_total"]), Decimal("2500"))
        self.client.force_authenticate(self.manager)  # can't see the other branch
        mine = self.client.get("/api/expenses/summary/").data
        self.assertEqual(Decimal(mine["pending_total"]), Decimal("2500"))


class ReceiptPrivacyTests(ExpenseBase):
    """Receipts are only downloadable by people who can see the expense."""

    def setUp(self):
        super().setUp()
        res = self.file(self.manager, attachment=SimpleUploadedFile(
            "receipt.pdf", b"%PDF-1.4 milk", content_type="application/pdf"))
        self.expense = Expense.objects.get(pk=res.data["id"])
        self.url = f"/api/expenses/{self.expense.pk}/attachment/"

    def test_link_points_at_the_api_not_public_media(self):
        self.client.force_authenticate(self.owner)
        data = self.client.get(f"/api/expenses/{self.expense.pk}/").data
        self.assertTrue(data["attachment"].endswith(self.url))
        self.assertNotIn("/media/", data["attachment"])

    def test_owner_and_submitter_download_it(self):
        for user in (self.owner, self.manager):
            self.client.force_authenticate(user)
            res = self.client.get(self.url)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(b"".join(res.streaming_content), b"%PDF-1.4 milk")
            self.assertEqual(res["Content-Type"], "application/pdf")
            self.assertIn("sandbox", res["Content-Security-Policy"])

    def test_others_get_nothing(self):
        from apps.accounts.constants import UserRole
        from apps.accounts.models import User, UserBranch
        outsider = User.objects.create_user(username="other_mgr", password="Passw0rd!",
                                            role=UserRole.BRANCH_MANAGER, business=self.business)
        UserBranch.objects.create(user=outsider, branch=self.other_branch)
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_the_file_is_not_reachable_under_media(self):
        self.client.force_authenticate(None)
        res = self.client.get(f"/media/{self.expense.attachment.name}")
        self.assertEqual(res.status_code, 404)

    def test_product_photos_are_public(self):
        import os
        os.makedirs(os.path.join(TEMP_MEDIA, "products"), exist_ok=True)
        with open(os.path.join(TEMP_MEDIA, "products", "latte.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        self.client.force_authenticate(None)
        res = self.client.get("/media/products/latte.png")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/media/products/../expenses/x.pdf").status_code, 404)
