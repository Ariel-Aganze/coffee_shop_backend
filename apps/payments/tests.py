from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductPrice
from apps.sales.models import Sale, SaleStatus
from apps.settings.models import BusinessSettings, Currency


class PaymentModuleTests(TestCase):
    def setUp(self):
        currency = Currency.objects.get(code="RWF")
        self.business = Business.objects.create(name="Test Cafe")
        BusinessSettings.objects.create(
            business=self.business, primary_currency=currency,
        )
        self.branch = Branch.objects.create(business=self.business, name="Main")
        self.owner = User.objects.create_user(
            username="owner", password="Passw0rd!",
            role=UserRole.OWNER, business=self.business,
        )
        self.cashier = User.objects.create_user(
            username="cashier", password="Passw0rd!",
            role=UserRole.CASHIER, business=self.business,
        )
        self.manager = User.objects.create_user(
            username="manager", password="Passw0rd!",
            role=UserRole.BRANCH_MANAGER, business=self.business,
        )
        UserBranch.objects.create(user=self.cashier, branch=self.branch)
        UserBranch.objects.create(user=self.manager, branch=self.branch)

        cat = Category.objects.create(business=self.business, name="Drinks")
        self.product = Product.objects.create(
            business=self.business, category=cat, name="Latte", sku="LAT-1",
        )
        ProductPrice.objects.create(
            product=self.product, selling_price=Decimal("2000.0000"),
            effective_from=timezone.now(),
        )

        self.client = APIClient()

    def _login(self, username="cashier", password="Passw0rd!"):
        resp = self.client.post(
            "/api/auth/login/", {"username": username, "password": password},
            format="json",
        )
        return resp.data["access"]

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def _create_sale(self, quantity="2"):
        self._auth(self._login())
        resp = self.client.post(
            "/api/sales/",
            {
                "branch": self.branch.id,
                "items": [{"product_id": self.product.id, "quantity": quantity}],
            },
            format="json",
        )
        return resp

    # ---------- Payments ----------
    def test_full_cash_payment(self):
        sale_resp = self._create_sale("2")  # total = 4000
        self.assertEqual(sale_resp.status_code, 201)
        sale_id = sale_resp.data["id"]

        resp = self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "4000.0000"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual(sale.paid_amount(), Decimal("4000.0000"))

    def test_split_payment(self):
        sale_resp = self._create_sale("2")
        sale_id = sale_resp.data["id"]

        resp = self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {
                "payments": [
                    {"method": "cash", "amount": "2500.0000"},
                    {"method": "mobile_money", "amount": "1500.0000",
                     "reference": "MP-999"},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual(sale.paid_amount(), Decimal("4000.0000"))

    def test_overpayment_rejected(self):
        sale_resp = self._create_sale("2")
        sale_id = sale_resp.data["id"]

        resp = self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "5000.0000"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_cash_requires_reference(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]

        resp = self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "mobile_money", "amount": "2000"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_method_rejected(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]

        resp = self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "bitcoin", "amount": "2000"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------- Refund ----------
    def test_full_refund(self):
        sale_resp = self._create_sale("2")
        sale_id = sale_resp.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "4000"}]},
            format="json",
        )

        self._auth(self._login("manager"))
        resp = self.client.post(
            f"/api/sales/{sale_id}/refund/",
            {"reason": "customer complaint"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual(sale.status, SaleStatus.REFUNDED)
        self.assertEqual(sale.refunded_amount(), Decimal("4000.0000"))

    def test_partial_refund(self):
        sale_resp = self._create_sale("2")
        sale_id = sale_resp.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "4000"}]},
            format="json",
        )
        self._auth(self._login("manager"))
        resp = self.client.post(
            f"/api/sales/{sale_id}/refund/",
            {"amount": "1000", "reason": "partial"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual(sale.refunded_amount(), Decimal("1000.0000"))
        self.assertEqual(sale.status, SaleStatus.COMPLETED)

    def test_refund_cannot_exceed_paid(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "2000"}]},
            format="json",
        )
        self._auth(self._login("manager"))
        resp = self.client.post(
            f"/api/sales/{sale_id}/refund/",
            {"amount": "5000"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cashier_cannot_refund(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]
        resp = self.client.post(
            f"/api/sales/{sale_id}/refund/", {}, format="json",
        )
        self.assertEqual(resp.status_code, 403)

    # ---------- Cancel ----------
    def test_cancel_unpaid_sale(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]
        self._auth(self._login("manager"))
        resp = self.client.post(
            f"/api/sales/{sale_id}/cancel/", {"reason": "wrong order"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        sale = Sale.objects.get(pk=sale_id)
        self.assertEqual(sale.status, SaleStatus.CANCELLED)

    def test_cannot_cancel_paid_sale(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "2000"}]},
            format="json",
        )
        self._auth(self._login("manager"))
        resp = self.client.post(
            f"/api/sales/{sale_id}/cancel/", {"reason": "x"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    # ---------- Receipt ----------
    def test_receipt_endpoint(self):
        sale_resp = self._create_sale("1")
        sale_id = sale_resp.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/payments/",
            {"payments": [{"method": "cash", "amount": "2000"}]},
            format="json",
        )
        resp = self.client.get(f"/api/sales/{sale_id}/receipt/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("transaction_number", resp.data)
        self.assertIn("items", resp.data)
        self.assertIn("payments", resp.data)
        self.assertEqual(resp.data["currency_code"], "RWF")