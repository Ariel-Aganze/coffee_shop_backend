"""One-call checkout, payment-method settings and drawer visibility (step 7 POS)."""
from decimal import Decimal

from apps.cash_management.models import CashSession
from apps.cash_management.services import open_cash_session
from apps.inventory.models import Inventory
from apps.payments.models import Payment
from apps.products.test_catalog import CatalogBase
from apps.sales.models import Sale


class CheckoutTests(CatalogBase):
    def checkout(self, user, payments, key=None, items=None):
        self.client.force_authenticate(user)
        headers = {"HTTP_X_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": items or [{"product_id": self.latte.id, "quantity": "2"}],
            "payments": payments,
        }, format="json", **headers)

    def test_sale_and_split_payment_in_one_request(self):
        open_cash_session(branch=self.branch, cashier=self.cashier, opening_balance=Decimal("0"))
        res = self.checkout(self.cashier, [
            {"method": "cash", "amount": "3000"},
            {"method": "mobile_money", "amount": "1400", "reference": "MP123"},
        ])
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Decimal(res.data["total_amount"]), Decimal("4400"))
        self.assertEqual(Decimal(res.data["paid_amount"]), Decimal("4400"))
        self.assertEqual(Decimal(res.data["balance_due"]), Decimal("0"))
        self.assertEqual(len(res.data["payments"]), 2)
        self.assertEqual(res.data["cashier_name"], "cashier")
        cash = Payment.objects.get(sale_id=res.data["id"], method="cash")
        self.assertIsNotNone(cash.cash_session_id)  # went into the open drawer

    def test_failed_payment_leaves_no_sale_and_no_stock_change(self):
        before = Inventory.objects.get(product=self.water).quantity
        res = self.checkout(self.cashier, [{"method": "cash", "amount": "999999"}],
                            items=[{"product_id": self.water.id, "quantity": "1"}])
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(Inventory.objects.get(product=self.water).quantity, before)

    def test_retry_with_same_key_returns_the_same_paid_sale(self):
        payments = [{"method": "cash", "amount": "4400"}]
        first = self.checkout(self.cashier, payments, key="cart-1")
        again = self.checkout(self.cashier, payments, key="cart-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], first.data["id"])
        self.assertEqual(Payment.objects.filter(sale_id=first.data["id"]).count(), 1)

    def test_disabled_methods_are_refused(self):
        self.settings.enabled_payment_methods = ["cash"]
        self.settings.save()
        res = self.checkout(self.cashier, [
            {"method": "bank_card", "amount": "4400", "reference": "AUTH1"}])
        self.assertEqual(res.status_code, 400)
        self.assertIn("turned off", str(res.data))
        self.assertFalse(Sale.objects.exists())

    def test_catalogue_lists_enabled_methods(self):
        self.settings.enabled_payment_methods = ["cash", "mobile_money"]
        self.settings.save()
        methods = self._catalog(self.cashier).data["payment_methods"]
        self.assertEqual(methods, [
            {"code": "cash", "label": "Cash", "needs_reference": False},
            {"code": "mobile_money", "label": "Mobile Money", "needs_reference": True},
        ])

    def test_unpaid_sale_reports_balance_due(self):
        self.client.force_authenticate(self.cashier)
        res = self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": [{"product_id": self.latte.id, "quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Decimal(res.data["balance_due"]), Decimal("2200"))


class PaymentMethodSettingsTests(CatalogBase):
    def test_owner_sets_methods_in_canonical_order(self):
        self.client.force_authenticate(self.owner)
        res = self.client.patch("/api/settings/me/", {
            "enabled_payment_methods": ["other", "cash", "cash"]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["enabled_payment_methods"], ["cash", "other"])

    def test_at_least_one_method_and_known_values(self):
        self.client.force_authenticate(self.owner)
        for bad in ([], ["cheque"]):
            res = self.client.patch("/api/settings/me/", {"enabled_payment_methods": bad},
                                    format="json")
            self.assertEqual(res.status_code, 400, bad)


class DrawerVisibilityTests(CatalogBase):
    def test_cashier_sees_only_their_own_sessions(self):
        mine = open_cash_session(branch=self.branch, cashier=self.cashier,
                                 opening_balance=Decimal("0"))
        open_cash_session(branch=self.branch, cashier=self.manager, opening_balance=Decimal("0"))
        self.client.force_authenticate(self.cashier)
        ids = [s["id"] for s in self.client.get("/api/cash-sessions/").data["results"]]
        self.assertEqual(ids, [mine.id])
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get("/api/cash-sessions/").data["count"],
                         CashSession.objects.count())


class ShopDayFilterTests(CatalogBase):
    def test_from_to_are_days_at_the_shop(self):
        from datetime import datetime, timezone as dt_timezone

        self.settings.timezone = "Africa/Kigali"  # UTC+2
        self.settings.save()
        self.client.force_authenticate(self.owner)
        res = self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": [{"product_id": self.latte.id, "quantity": "1"}],
        }, format="json")
        # 23:30 UTC on the 12th is 01:30 on the 13th in Kigali.
        Sale.objects.filter(pk=res.data["id"]).update(
            created_at=datetime(2026, 11, 12, 23, 30, tzinfo=dt_timezone.utc))
        on_13th = self.client.get("/api/sales/", {"from": "2026-11-13", "to": "2026-11-13"})
        on_12th = self.client.get("/api/sales/", {"from": "2026-11-12", "to": "2026-11-12"})
        self.assertEqual(on_13th.data["count"], 1)
        self.assertEqual(on_12th.data["count"], 0)
        bad = self.client.get("/api/sales/", {"from": "13/11/2026"})
        self.assertEqual(bad.status_code, 400)


class ReconciliationShapeTests(CatalogBase):
    def test_amounts_are_decimal_strings_as_documented(self):
        session = open_cash_session(branch=self.branch, cashier=self.cashier,
                                    opening_balance=Decimal("1500"))
        self.client.force_authenticate(self.cashier)
        data = self.client.get(f"/api/cash-sessions/{session.id}/reconciliation/").json()
        self.assertEqual(data["opening"], "1500.0000")
        self.assertTrue(all(isinstance(v, str) for v in data.values()), data)
