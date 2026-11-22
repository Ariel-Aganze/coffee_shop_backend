"""Product & category management (PROD-001..011) as used by the products screens."""
import io
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image

from apps.audit.models import AuditLog
from apps.products.models import BranchProductAvailability, Product

from .test_catalog import CatalogBase


def png(size=(8, 8)):
    buf = io.BytesIO()
    Image.new("RGB", size, "brown").save(buf, "PNG")
    return SimpleUploadedFile("cup.png", buf.getvalue(), content_type="image/png")


class CategoryManagementTests(CatalogBase):
    def test_list_counts_active_products_and_filters(self):
        self.client.force_authenticate(self.owner)
        res = self.client.get("/api/categories/", {"is_active": "true"})
        self.assertEqual(res.status_code, 200)
        drinks = next(c for c in res.data["results"] if c["name"] == "Drinks")
        self.assertEqual(drinks["product_count"],
                         Product.objects.filter(category=self.drinks, is_active=True).count())
        self.assertNotIn("Retired", [c["name"] for c in res.data["results"]])
        res = self.client.get("/api/categories/", {"search": "ret"})
        self.assertEqual([c["name"] for c in res.data["results"]], ["Retired"])

    def test_duplicate_name_is_a_field_error_not_a_crash(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/categories/", {"name": " drinks "}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("name", res.data["details"])

    def test_delete_deactivates_and_keeps_products(self):
        self.client.force_authenticate(self.owner)
        res = self.client.delete(f"/api/categories/{self.drinks.pk}/")
        self.assertEqual(res.status_code, 204)
        self.drinks.refresh_from_db()
        self.assertFalse(self.drinks.is_active)
        self.assertTrue(Product.objects.filter(category=self.drinks).exists())
        self.assertTrue(AuditLog.objects.filter(action="category_deactivated").exists())

    def test_only_product_managers_write(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post("/api/categories/", {"name": "Teas"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.client.force_authenticate(self.inventory)
        res = self.client.post("/api/categories/", {"name": "Teas"}, format="json")
        self.assertEqual(res.status_code, 201)


TEMP_MEDIA = tempfile.mkdtemp(prefix="caffio-test-media-")


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ProductManagementTests(CatalogBase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_filters_ordering_and_hidden_branches(self):
        BranchProductAvailability.objects.create(branch=self.other_branch, product=self.latte,
                                                 is_available=False)
        self.client.force_authenticate(self.owner)
        res = self.client.get("/api/products/", {"search": "latt", "ordering": "-price"})
        self.assertEqual(res.status_code, 200)
        latte = res.data["results"][0]
        self.assertEqual(latte["name"], "Latte")
        self.assertEqual(latte["hidden_branch_ids"], [self.other_branch.pk])
        self.assertEqual(latte["category_name"], "Drinks")

        prices = [r["current_price"] for r in
                  self.client.get("/api/products/", {"ordering": "-price"}).data["results"]]
        numbers = [Decimal(p) for p in prices if p is not None]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertTrue(all(p is not None for p in prices[: len(numbers)]))  # unpriced last

    def test_product_type_filter(self):
        Product.objects.filter(pk=self.latte.pk).update(product_type="food")
        self.client.force_authenticate(self.owner)
        res = self.client.get("/api/products/", {"product_type": "food"})
        self.assertEqual([p["name"] for p in res.data["results"]], ["Latte"])

    def test_create_with_image_returns_read_shape(self):
        self.client.force_authenticate(self.inventory)
        res = self.client.post("/api/products/", {
            "name": "Mocha", "sku": "hd-moc", "category": self.drinks.pk,
            "product_type": "drink", "initial_selling_price": "2500",
            "initial_cost_price": "900", "image": png(),
        }, format="multipart")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Decimal(res.data["current_price"]), Decimal("2500"))
        self.assertEqual(res.data["category_name"], "Drinks")
        self.assertTrue(res.data["image"].endswith(".png"))
        self.assertEqual(res.data["hidden_branch_ids"], [])

    def test_rejects_bad_image_case_insensitive_sku_and_inactive_category(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/products/", {
            "name": "Mocha", "sku": "latte", "category": self.retired.pk,
            "initial_selling_price": "2500",
            "image": SimpleUploadedFile("x.gif", b"GIF89a", content_type="image/gif"),
        }, format="multipart")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.data["details"]), {"sku", "category", "image"})

    def test_image_can_be_removed(self):
        self.latte.image = png()
        self.latte.save()
        self.client.force_authenticate(self.owner)
        res = self.client.patch(f"/api/products/{self.latte.pk}/", {"image": None}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNone(res.data["image"])

    def test_keeping_an_already_inactive_category_is_allowed(self):
        p = Product.objects.create(business=self.business, name="Old", sku="OLD",
                                   category=self.retired)
        self.client.force_authenticate(self.owner)
        res = self.client.patch(f"/api/products/{p.pk}/",
                                {"name": "Old blend", "category": self.retired.pk}, format="json")
        self.assertEqual(res.status_code, 200, res.data)


class PriceTests(CatalogBase):
    def test_new_price_keeps_reason_and_author(self):
        self.client.force_authenticate(self.owner)
        res = self.client.post(f"/api/products/{self.latte.pk}/prices/",
                               {"selling_price": "2400", "reason": "Milk costs"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        history = self.client.get(f"/api/products/{self.latte.pk}/prices/").data
        newest_started = [h for h in history if h["reason"] == "Milk costs"]
        self.assertEqual(len(newest_started), 1)
        self.assertEqual(newest_started[0]["created_by_name"], "owner")

    def test_past_start_is_refused_future_is_scheduled(self):
        self.client.force_authenticate(self.owner)
        url = f"/api/products/{self.latte.pk}/prices/"
        past = (timezone.now() - timedelta(days=1)).isoformat()
        res = self.client.post(url, {"selling_price": "1", "effective_from": past}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("effective_from", res.data["details"])
        future = (timezone.now() + timedelta(days=3)).isoformat()
        res = self.client.post(url, {"selling_price": "3000", "effective_from": future},
                               format="json")
        self.assertEqual(res.status_code, 201)
        current = self.client.get(f"/api/products/{self.latte.pk}/").data["current_price"]
        self.assertEqual(Decimal(current), Decimal("2200"))  # not yet

    def test_cashier_history_has_no_costs(self):
        self.client.force_authenticate(self.cashier)
        history = self.client.get(f"/api/products/{self.latte.pk}/prices/").data
        self.assertTrue(history)
        self.assertTrue(all("cost_price" not in h for h in history))


class AvailabilityTests(CatalogBase):
    def url(self):
        return f"/api/products/{self.latte.pk}/availability/"

    def test_owner_sees_every_branch_defaulting_to_available(self):
        self.client.force_authenticate(self.owner)
        rows = self.client.get(self.url()).data
        self.assertEqual([(r["branch_name"], r["is_available"], r["can_edit"]) for r in rows],
                         [("Main", True, True), ("Other", True, True)])

    def test_switch_off_and_back_on(self):
        self.client.force_authenticate(self.owner)
        res = self.client.put(self.url(), {"branches": [
            {"branch": self.other_branch.pk, "is_available": False}]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual({r["branch_name"]: r["is_available"] for r in res.data},
                         {"Main": True, "Other": False})
        res = self.client.put(self.url(), {"branches": [
            {"branch": self.other_branch.pk, "is_available": True}]}, format="json")
        self.assertTrue(all(r["is_available"] for r in res.data))
        self.assertEqual(AuditLog.objects.filter(action="product_availability_changed").count(), 2)

    def test_branch_manager_limited_to_their_branches(self):
        self.client.force_authenticate(self.manager)
        rows = self.client.get(self.url()).data
        self.assertEqual([r["branch_name"] for r in rows], ["Main"])
        res = self.client.put(self.url(), {"branches": [
            {"branch": self.other_branch.pk, "is_available": False}]}, format="json")
        self.assertEqual(res.status_code, 403)
        res = self.client.put(self.url(), {"branches": [
            {"branch": self.branch.pk, "is_available": False}]}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_raw_rows_endpoint_also_checks_branch_access(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post("/api/branch-availability/", {
            "branch": self.other_branch.pk, "product": self.latte.pk, "is_available": False,
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(BranchProductAvailability.objects.filter(
            branch=self.other_branch).exists())

    def test_cashier_reads_but_cannot_change(self):
        self.client.force_authenticate(self.cashier)
        rows = self.client.get(self.url()).data
        self.assertEqual([(r["branch_name"], r["can_edit"]) for r in rows], [("Main", False)])
        res = self.client.put(self.url(), {"branches": [
            {"branch": self.branch.pk, "is_available": False}]}, format="json")
        self.assertEqual(res.status_code, 403)
