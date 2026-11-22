"""Stock list filters, valuation, counts and permissions used by the inventory screens."""
from decimal import Decimal

from apps.audit.models import AuditLog
from apps.inventory.models import Inventory, StockMovement
from apps.inventory.services import record_inflow
from apps.products.test_catalog import CatalogBase


class StockScreenBase(CatalogBase):
    def setUp(self):
        super().setUp()
        # Water: 3 on hand, minimum 5 (low). Latte: 10 on hand, minimum 2 (ok).
        record_inflow(branch=self.branch, product=self.latte, quantity=Decimal("10"),
                      performed_by=self.owner)
        Inventory.objects.filter(product=self.latte).update(minimum_threshold=Decimal("2"))
        self.empty = record_inflow(branch=self.branch, product=self.inactive,
                                   quantity=Decimal("1"), performed_by=self.owner).product
        record_inflow(branch=self.other_branch, product=self.latte, quantity=Decimal("4"),
                      performed_by=self.owner)

    def stock(self, user, **params):
        self.client.force_authenticate(user)
        return self.client.get("/api/inventory/", params)


class StockListTests(StockScreenBase):
    def test_levels_search_and_value(self):
        from apps.inventory.services import record_outflow
        record_outflow(branch=self.branch, product=self.inactive, quantity=Decimal("1"),
                       movement_type="damaged", reason="Broken", performed_by=self.owner)
        names = lambda res: [r["product_name"] for r in res.data["results"]]  # noqa: E731
        self.assertEqual(names(self.stock(self.owner, branch=self.branch.id, level="low")), ["Water"])
        self.assertEqual(names(self.stock(self.owner, branch=self.branch.id, level="out")),
                         ["Discontinued"])
        self.assertEqual(names(self.stock(self.owner, branch=self.branch.id, search="lat")), ["Latte"])
        latte = self.stock(self.owner, branch=self.branch.id, search="lat").data["results"][0]
        # Latte's current cost is 900 (the price raised yesterday), 10 on hand.
        self.assertEqual(Decimal(latte["unit_cost"]), Decimal("900"))
        self.assertEqual(Decimal(latte["stock_value"]), Decimal("9000"))
        self.assertEqual(latte["product_unit"], "each")

    def test_summary_counts_and_values_what_you_can_see(self):
        self.client.force_authenticate(self.owner)
        data = self.client.get("/api/inventory/summary/", {"branch": self.branch.id}).data
        self.assertEqual(data["tracked_products"], 3)
        self.assertEqual(data["low_stock"], 1)  # water (3 ≤ 5)
        self.assertEqual(data["costing_method"], "latest_cost")
        # latte 10×900 + water 3×400 + discontinued 1×400
        self.assertEqual(Decimal(data["total_value"]), Decimal("10600"))
        self.client.force_authenticate(self.manager)  # assigned to self.branch only
        self.assertEqual(self.client.get("/api/inventory/summary/").data["tracked_products"], 3)

    def test_costs_are_hidden_from_cashiers(self):
        row = self.stock(self.cashier).data["results"][0]
        self.assertNotIn("unit_cost", row)
        self.assertNotIn("stock_value", row)
        self.client.force_authenticate(self.cashier)
        self.assertNotIn("total_value", self.client.get("/api/inventory/summary/").data)


class StockCountTests(StockScreenBase):
    def test_count_adjusts_only_lines_that_differ(self):
        self.client.force_authenticate(self.inventory)
        res = self.client.post("/api/inventory/count/", {
            "branch": self.branch.id, "reason": "Monthly count",
            "lines": [
                {"product_id": self.latte.id, "counted_quantity": "8"},
                {"product_id": self.water.id, "counted_quantity": "3"},
                {"product_id": self.unpriced.id, "counted_quantity": "2"},  # starts tracking
            ],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["adjusted"], 2)
        by_name = {line["product_name"]: line for line in res.data["lines"]}
        self.assertEqual(Decimal(by_name["Latte"]["difference"]), Decimal("-2"))
        self.assertEqual(Decimal(by_name["Water"]["difference"]), Decimal("0"))
        self.assertEqual(Inventory.objects.get(branch=self.branch, product=self.latte).quantity,
                         Decimal("8"))
        movement = StockMovement.objects.filter(product=self.latte, movement_type="adjustment").get()
        self.assertEqual(movement.reason, "Stock count: Monthly count")

    def test_count_is_all_or_nothing_and_branch_scoped(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post("/api/inventory/count/", {
            "branch": self.other_branch.id, "reason": "x",
            "lines": [{"product_id": self.latte.id, "counted_quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 403)
        res = self.client.post("/api/inventory/count/", {
            "branch": self.branch.id, "reason": "x",
            "lines": [{"product_id": self.latte.id, "counted_quantity": "1"},
                      {"product_id": 999999, "counted_quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Inventory.objects.get(branch=self.branch, product=self.latte).quantity,
                         Decimal("10"))

    def test_cashiers_cannot_count(self):
        self.client.force_authenticate(self.cashier)
        res = self.client.post("/api/inventory/count/", {
            "branch": self.branch.id, "reason": "x",
            "lines": [{"product_id": self.latte.id, "counted_quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertIn("error", res.data)  # the standard envelope


class ThresholdAndMovementTests(StockScreenBase):
    def test_threshold_change_is_audited(self):
        inv = Inventory.objects.get(branch=self.branch, product=self.water)
        self.client.force_authenticate(self.inventory)
        res = self.client.patch(f"/api/inventory/{inv.id}/threshold/",
                                {"minimum_threshold": "2"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["is_low_stock"])
        self.assertIn("unit_cost", res.data)
        self.assertTrue(AuditLog.objects.filter(action="stock_threshold_changed").exists())

    def test_movements_show_who_and_which_sale(self):
        self.client.force_authenticate(self.owner)
        row = self.client.get("/api/stock-movements/", {"product": self.latte.id}).data["results"][0]
        self.assertEqual(row["performed_by_name"], "owner")
        self.assertIsNone(row["sale_number"])
        self.assertEqual(row["product_unit"], "each")

    def test_inflow_to_another_branch_is_forbidden_not_invalid(self):
        self.client.force_authenticate(self.manager)
        res = self.client.post("/api/stock-movements/inflow/", {
            "branch": self.other_branch.id, "product_id": self.latte.id, "quantity": "1",
        }, format="json")
        self.assertEqual(res.status_code, 403)
