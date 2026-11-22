"""Sales over time, expense grouping, stock valuation and movement filters."""
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from apps.expenses.models import Expense, ExpenseCategory
from apps.inventory.services import record_outflow
from apps.products.test_catalog import CatalogBase
from apps.sales.models import Sale


class ReportBase(CatalogBase):
    def setUp(self):
        super().setUp()
        self.settings.timezone = "Africa/Kigali"  # UTC+2
        self.settings.save()
        self.client.force_authenticate(self.owner)

    def sell(self, when_utc, quantity="1"):
        res = self.client.post("/api/sales/", {
            "branch": self.branch.id,
            "items": [{"product_id": self.latte.id, "quantity": quantity}],
            "payments": [{"method": "cash", "amount": str(2200 * int(quantity))}],
        }, format="json")
        assert res.status_code == 201, res.data
        Sale.objects.filter(pk=res.data["id"]).update(created_at=when_utc)


class SalesOverTimeTests(ReportBase):
    def test_days_weeks_and_months_in_shop_time(self):
        # Sun 8 Nov 23:30 UTC = Mon 9 Nov 01:30 in Kigali (a new day AND a new week).
        self.sell(datetime(2026, 11, 8, 23, 30, tzinfo=dt_timezone.utc))
        self.sell(datetime(2026, 11, 8, 10, 0, tzinfo=dt_timezone.utc), quantity="2")
        self.sell(datetime(2026, 10, 30, 10, 0, tzinfo=dt_timezone.utc))
        url = "/api/reports/sales/over-time/"
        q = {"from": "2026-10-01", "to": "2026-11-13"}

        days = self.client.get(url, {**q, "group": "day"}).data["results"]
        self.assertEqual([(d["period_start"], d["transactions"]) for d in days],
                         [("2026-10-30", 1), ("2026-11-08", 1), ("2026-11-09", 1)])
        weeks = self.client.get(url, {**q, "group": "week"}).data["results"]
        self.assertEqual([(w["period_start"], w["period_end"], Decimal(w["net_sales"])) for w in weeks],
                         [("2026-10-26", "2026-11-01", Decimal("2200")),
                          ("2026-11-02", "2026-11-08", Decimal("4400")),
                          ("2026-11-09", "2026-11-13", Decimal("2200"))])  # clipped to `to`
        months = self.client.get(url, {**q, "group": "month"}).data["results"]
        self.assertEqual([(m["period_start"], m["period_end"]) for m in months],
                         [("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-11-13")])
        self.assertEqual(self.client.get(url, {**q, "group": "year"}).status_code, 400)

    def test_csv_export(self):
        self.sell(datetime(2026, 11, 10, 10, 0, tzinfo=dt_timezone.utc))
        res = self.client.get("/api/reports/sales/over-time/",
                              {"from": "2026-11-01", "to": "2026-11-13", "export": "csv"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res["Content-Type"])


class ExpenseGroupingTests(ReportBase):
    def test_group_by_branch_and_month(self):
        rent = ExpenseCategory.objects.create(business=self.business, name="Rent")
        for branch, day, amount in ((self.branch, "2026-10-05", "100"), (None, "2026-11-02", "50")):
            Expense.objects.create(business=self.business, branch=branch, category=rent,
                                   amount=Decimal(amount), currency_code="RWF",
                                   description="x", expense_date=day, submitted_by=self.owner)
        q = {"from": "2026-10-01", "to": "2026-11-13"}
        by_branch = self.client.get("/api/reports/expenses/", {**q, "group": "branch"}).data
        self.assertEqual(by_branch["group"], "branch")
        self.assertEqual(sorted(r["group"] for r in by_branch["results"]), ["Main", "Whole business"])
        by_month = self.client.get("/api/reports/expenses/", {**q, "group": "month"}).data
        self.assertEqual([r["group"] for r in by_month["results"]], ["2026-10", "2026-11"])
        default = self.client.get("/api/reports/expenses/", q).data
        self.assertEqual({r["group"] for r in default["results"]}, {"Rent"})


class InventoryReportTests(ReportBase):
    def test_stock_value_and_units(self):
        rows = self.client.get("/api/reports/inventory/current-stock/").data["results"]
        water = next(r for r in rows if r["product_name"] == "Water")
        self.assertEqual(water["unit"], "each")
        self.assertEqual(Decimal(water["unit_cost"]), Decimal("400"))
        self.assertEqual(Decimal(water["stock_value"]), Decimal("1200"))  # 3 on hand × 400

    def test_losses_filter_takes_several_types_and_shows_names(self):
        record_outflow(branch=self.branch, product=self.water, quantity=Decimal("1"),
                       movement_type="damaged", reason="Broken", performed_by=self.manager)
        record_outflow(branch=self.branch, product=self.water, quantity=Decimal("1"),
                       movement_type="expired", reason="Old", performed_by=self.manager)
        rows = self.client.get("/api/reports/inventory/movements/",
                               {"type": "damaged,expired"}).data["results"]
        self.assertEqual(sorted(r["movement_type"] for r in rows), ["damaged", "expired"])
        self.assertEqual(rows[0]["performed_by"], "manager")
