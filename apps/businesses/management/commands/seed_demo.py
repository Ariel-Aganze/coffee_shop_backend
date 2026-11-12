"""
Create a realistic demo business for development and UI work.

    venv/bin/python manage.py seed_demo                 # 28 days of history
    venv/bin/python manage.py seed_demo --days 7        # quicker
    venv/bin/python manage.py seed_demo --prefix demo2 --name "Second Cafe"

Everything goes through the real service layer (sales, payments, stock,
cash sessions, expenses), so the data obeys every business rule: stock
ledger, cash reconciliation, audit trail and reports all agree. Past days
are simulated by pinning `timezone.now()` to each moment while calling the
services. Runs in one transaction: it either seeds everything or nothing.

Refuses to run when DEBUG is off unless --force is given.
"""
import random
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.constants import UserRole
from apps.accounts.models import User, UserBranch
from apps.audit.services import log_action
from apps.branches.models import Branch
from apps.businesses.models import Business
from apps.cash_management.models import CashSession
from apps.cash_management.services import (
    add_cash_movement,
    close_cash_session,
    compute_expected_cash,
    open_cash_session,
)
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import approve_expense, create_expense
from apps.inventory.models import Inventory, StockMovementType
from apps.inventory.services import record_inflow, record_outflow
from apps.payments.services import add_payments_to_sale, cancel_sale, refund_sale
from apps.products.models import BranchProductAvailability, Category, Product, ProductPrice
from apps.sales.services import create_sale
from apps.settings.models import BusinessSettings, Currency

TZ_NAME = "Africa/Kigali"
CURRENCY = "RWF"

BRANCHES = [
    # name, address, phone, rent, daily sales range (weekday)
    ("Kigali Heights", "KG 7 Ave, Kigali Heights", "+250 788 100 001", 450_000, (30, 42)),
    ("Nyarutarama", "KG 9 Ave, Nyarutarama", "+250 788 100 002", 380_000, (22, 32)),
    ("Remera", "KG 11 Ave, Remera", "+250 788 100 003", 300_000, (16, 26)),
]

# category -> [(name, sku, type, price, cost, weight, tracked_target_stock)]
CATALOGUE = {
    "Hot Drinks": [
        ("Espresso", "HD-ESP", "drink", 1500, 400, 6, None),
        ("Americano", "HD-AME", "drink", 2000, 500, 8, None),
        ("Cappuccino", "HD-CAP", "drink", 2500, 800, 12, None),
        ("Café Latte", "HD-LAT", "drink", 2800, 900, 10, None),
        ("Mocha", "HD-MOC", "drink", 3200, 1100, 4, None),
        ("Rwandan Black Tea", "HD-TEA", "drink", 1200, 250, 5, None),
        ("African Spiced Tea", "HD-AFT", "drink", 1800, 450, 7, None),
    ],
    "Cold Drinks": [
        ("Iced Latte", "CD-ICL", "drink", 3000, 1000, 5, None),
        ("Fresh Passion Juice", "CD-PAS", "drink", 2500, 900, 6, None),
        ("Bottled Water 500ml", "CD-WAT", "packaged", 800, 400, 7, 80),
    ],
    "Bakery & Food": [
        ("Butter Croissant", "BF-CRO", "food", 1800, 700, 7, 50),
        ("Banana Muffin", "BF-MUF", "food", 1500, 550, 6, 50),
        ("Beef Samosa", "BF-SAM", "food", 1000, 400, 6, 60),
        ("Chicken Sandwich", "BF-SAN", "food", 4500, 2000, 4, 30),
    ],
    "Coffee to Go": [
        ("Rwandan Coffee Beans 250g", "CG-BEA", "packaged", 9000, 5500, 1, 25),
    ],
}
LOW_STOCK_THRESHOLDS = {"CD-WAT": 20, "BF-CRO": 12, "BF-MUF": 12, "BF-SAM": 15,
                        "BF-SAN": 8, "CG-BEA": 8}

EXPENSE_CATEGORIES = ["Rent", "Utilities", "Supplies", "Staff", "Maintenance", "Marketing"]


class Command(BaseCommand):
    help = "Seed a realistic demo business (branches, staff, products, weeks of activity)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=28, help="Days of history (default 28).")
        parser.add_argument("--prefix", default="demo", help="Username prefix (default 'demo').")
        parser.add_argument("--name", default="Demo Coffee Co.", help="Business name.")
        parser.add_argument("--password", default="Demo1234!", help="Password for every demo user.")
        parser.add_argument("--seed", type=int, default=42, help="Random seed (repeatable data).")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    # ------------------------------------------------------------------ entry
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("Refusing to seed demo data with DEBUG off (use --force).")
        if Business.objects.filter(name=opts["name"]).exists():
            raise CommandError(f"A business named '{opts['name']}' already exists. "
                               f"Use --name and --prefix to create another one.")
        if User.objects.filter(username__startswith=f"{opts['prefix']}_").exists():
            raise CommandError(f"Users with prefix '{opts['prefix']}_' already exist. Use --prefix.")
        if opts["days"] < 1:
            raise CommandError("--days must be at least 1.")

        self.rng = random.Random(opts["seed"])
        self.tz = ZoneInfo(TZ_NAME)
        self.today = timezone.now().astimezone(self.tz).date()
        self.start = self.today - timedelta(days=opts["days"])
        self.stats = {"sales": 0, "refunds": 0, "cancelled": 0, "unpaid": 0, "expenses": 0}

        with transaction.atomic():
            self._setup(opts)
            for offset in range(opts["days"] + 1):  # history + today
                self._simulate_day(self.start + timedelta(days=offset))
            self._finish()
        self._report(opts)

    # ------------------------------------------------------------- clock
    @contextmanager
    def at(self, day, hour, minute=0):
        moment = datetime.combine(day, time(hour, minute), tzinfo=self.tz)
        with mock.patch("django.utils.timezone.now", return_value=moment):
            yield moment

    def _past(self, day, hour, minute=0):
        """True if this moment has already happened (only matters for today)."""
        moment = datetime.combine(day, time(hour, minute), tzinfo=self.tz)
        return moment <= timezone.now()

    # ------------------------------------------------------------- setup
    def _setup(self, opts):
        p = opts["prefix"]
        with self.at(self.start, 6):
            currency = Currency.objects.get(code=CURRENCY)
            self.business = Business.objects.create(
                name=opts["name"], contact_email=f"hello@{p}.example",
                contact_phone="+250 788 100 000", address="Kigali, Rwanda",
            )
            BusinessSettings.objects.create(
                business=self.business, primary_currency=currency, timezone=TZ_NAME,
                cashier_max_discount_percent=Decimal("10"),
            )
            self.branches = [
                Branch.objects.create(business=self.business, name=n, address=a, phone=ph)
                for n, a, ph, _, _ in BRANCHES
            ]

            def user(suffix, role, first, last, *branches):
                u = User.objects.create_user(
                    username=f"{p}_{suffix}", password=opts["password"], role=role,
                    business=self.business, first_name=first, last_name=last,
                    email=f"{suffix}@{p}.example",
                )
                for b in branches:
                    UserBranch.objects.create(user=u, branch=b)
                return u

            self.owner = user("owner", UserRole.OWNER, "Aline", "Uwase")
            log_action(business=self.business, user=self.owner, action="business_registered",
                       target_type="Business", target_id=str(self.business.pk),
                       metadata={"currency": CURRENCY})
            self.users = [self.owner]
            self.users.append(user("admin", UserRole.ADMIN, "Eric", "Mugisha", self.branches[0]))
            self.users.append(user("accountant", UserRole.ACCOUNTANT, "Grace", "Ingabire"))
            self.users.append(user("inventory", UserRole.INVENTORY_MANAGER, "Jean", "Habimana",
                                   self.branches[0], self.branches[1]))
            names = [("Patrick", "Nkurunziza"), ("Diane", "Mukamana"), ("Olivier", "Niyonzima"),
                     ("Claudine", "Uwimana"), ("Kevin", "Ishimwe"), ("Sandrine", "Umutoni"),
                     ("Fabrice", "Ndayisaba"), ("Josiane", "Iradukunda"), ("Yves", "Bizimana")]
            self.managers, self.cashiers = {}, {}
            for i, branch in enumerate(self.branches, start=1):
                first, last = names[(i - 1) * 3]
                self.managers[branch.id] = user(f"manager{i}", UserRole.BRANCH_MANAGER,
                                                first, last, branch)
                self.cashiers[branch.id] = [
                    user(f"cashier{i}{tag}", UserRole.CASHIER, *names[(i - 1) * 3 + k], branch)
                    for k, tag in ((1, "a"), (2, "b"))
                ]
                self.users += [self.managers[branch.id], *self.cashiers[branch.id]]

            # Catalogue
            self.products, self.weights, self.targets = [], [], {}
            price_start = datetime.combine(self.start - timedelta(days=1), time(0), tzinfo=self.tz)
            for cat_name, items in CATALOGUE.items():
                cat = Category.objects.create(business=self.business, name=cat_name)
                for name, sku, ptype, price, cost, weight, target in items:
                    prod = Product.objects.create(business=self.business, category=cat, name=name,
                                                  sku=sku, product_type=ptype,
                                                  description=f"{name} — demo product.")
                    ProductPrice.objects.create(product=prod, selling_price=Decimal(price),
                                                cost_price=Decimal(cost), effective_from=price_start,
                                                created_by=self.owner)
                    self.products.append(prod)
                    self.weights.append(weight)
                    if target:
                        self.targets[prod.id] = target
            # Some history worth showing: a price rise and a retired product.
            cap = Product.objects.get(business=self.business, sku="HD-CAP")
            rise = max(self.start, self.today - timedelta(days=10))
            ProductPrice.objects.create(
                product=cap, selling_price=Decimal("2800"), cost_price=Decimal("850"),
                effective_from=datetime.combine(rise, time(6), tzinfo=self.tz),
                created_by=self.owner,
            )
            Product.objects.create(business=self.business, category=cat, name="Seasonal Gift Box",
                                   sku="CG-GFT", product_type="packaged", is_active=False)
            # Remera has no kitchen: no sandwiches there.
            sandwich = Product.objects.get(business=self.business, sku="BF-SAN")
            BranchProductAvailability.objects.create(branch=self.branches[2], product=sandwich,
                                                     is_available=False)
            self.unavailable = {(self.branches[2].id, sandwich.id)}

            self.expense_cats = {
                n: ExpenseCategory.objects.create(business=self.business, name=n)
                for n in EXPENSE_CATEGORIES
            }

    # ------------------------------------------------------------- one day
    def _simulate_day(self, day):
        is_today = day == self.today
        weekend = day.weekday() >= 5
        for b_index, branch in enumerate(self.branches):
            manager = self.managers[branch.id]
            cashier = self.cashiers[branch.id][day.toordinal() % 2]
            rent = BRANCHES[b_index][3]
            lo, hi = BRANCHES[b_index][4]

            if not self._past(day, 6, 30):
                continue
            with self.at(day, 6, 30):
                self._restock(branch, manager)
            with self.at(day, 7, 0):
                session = open_cash_session(branch=branch, cashier=cashier,
                                            opening_balance=Decimal("20000"))
            CashSession.objects.filter(pk=session.pk).update(
                opened_at=datetime.combine(day, time(7), tzinfo=self.tz))

            # Expenses
            if day == self.start:
                self._expense(day, branch, manager, "Rent", rent, "Monthly rent", approve=True)
            if day.weekday() == 0:
                self._expense(day, branch, manager, "Utilities",
                              self.rng.randrange(25_000, 45_000, 500),
                              "Electricity & water (weekly)", approve=day < self.today - timedelta(days=2))
            if self.rng.random() < 0.35:
                self._expense(day, branch, manager, "Supplies",
                              self.rng.randrange(5_000, 30_000, 500),
                              self.rng.choice(["Milk & sugar", "Cups & lids", "Cleaning supplies",
                                               "Napkins & straws"]),
                              approve=day < self.today - timedelta(days=2))
            if self.rng.random() < 0.05:
                self._expense(day, branch, manager, "Maintenance",
                              self.rng.randrange(20_000, 90_000, 1000),
                              "Espresso machine service", approve=day < self.today - timedelta(days=2))

            # Sales through the day
            count = self.rng.randint(lo, hi)
            if weekend:
                count = int(count * 1.25)
            minutes = sorted(self.rng.randrange(7 * 60 + 5, 19 * 60) for _ in range(count))
            deposited = False
            for m in minutes:
                h, mi = divmod(m, 60)
                if not self._past(day, h, mi):
                    break
                with self.at(day, h, mi):
                    self._one_sale(branch, cashier, manager)
                if not deposited and h >= 15:
                    with self.at(day, 15, 0):
                        drawer = compute_expected_cash(session)["expected_cash"]
                        if drawer > Decimal("100000"):
                            add_cash_movement(session=session, movement_type="withdrawal",
                                              amount=Decimal(int(drawer * Decimal("0.6")) // 1000 * 1000),
                                              reason="Bank deposit", reference="DEP-" + day.strftime("%m%d"),
                                              performed_by=manager)
                    deposited = True

            # Close the drawer (today's sessions stay open)
            if is_today:
                continue
            with self.at(day, 19, 30):
                expected = compute_expected_cash(session)["expected_cash"]
                delta, note = Decimal("0"), ""
                if self.rng.random() < 0.12:
                    delta = Decimal(self.rng.choice([-2000, -1000, -500, 500, 1000, 3000]))
                    note = ("Change given incorrectly during rush" if delta < 0
                            else "Customer left change as tip")
                close_cash_session(session=session, actual_cash=expected + delta,
                                   explanation=note, closed_by=cashier)

    def _one_sale(self, branch, cashier, manager):
        lines, used = [], set()
        for _ in range(self.rng.choices([1, 2, 3], weights=[55, 33, 12])[0]):
            product = self.rng.choices(self.products, weights=self.weights)[0]
            if product.id in used or (branch.id, product.id) in self.unavailable:
                continue
            if product.id in self.targets:
                inv = Inventory.objects.filter(branch=branch, product=product).first()
                if not inv or inv.quantity < 2:
                    continue
            used.add(product.id)
            lines.append({"product_id": product.id,
                          "quantity": str(self.rng.choices([1, 2], weights=[85, 15])[0]),
                          "discount_amount": "0"})
        if not lines:
            return
        seller = cashier
        if self.rng.random() < 0.03:  # occasional manager discount for a regular
            seller = manager
            lines[0]["discount_amount"] = "500"
        sale, _ = create_sale(user=seller, branch=branch, items_payload=lines)

        roll = self.rng.random()
        if roll < 0.006:  # walked away before paying -> cancelled
            cancel_sale(sale=sale, user=manager, reason="Customer left before paying")
            self.stats["cancelled"] += 1
            return
        if roll < 0.010:  # still unpaid (shows up as "unpaid" in reports)
            self.stats["unpaid"] += 1
            return

        total = sale.total_amount
        method = self.rng.choices(["cash", "mobile_money", "bank_card", "split"],
                                  weights=[48, 34, 13, 5])[0]
        if method == "split" and total >= 2000:
            cash = (total / 2).quantize(Decimal("1")) // 500 * 500
            payments = [{"method": "cash", "amount": str(cash)},
                        {"method": "mobile_money", "amount": str(total - cash),
                         "reference": self._ref("MP")}]
        else:
            method = "cash" if method == "split" else method
            ref = {"cash": "", "mobile_money": self._ref("MP"), "bank_card": self._ref("AUTH")}[method]
            payments = [{"method": method, "amount": str(total), "reference": ref}]
        add_payments_to_sale(sale=sale, payments_payload=payments, user=seller)
        self.stats["sales"] += 1

        if self.rng.random() < 0.008:
            refund_sale(sale=sale, user=manager, reason=self.rng.choice(
                ["Drink made incorrectly", "Customer complaint", "Wrong order"]))
            self.stats["refunds"] += 1

    def _ref(self, prefix):
        return f"{prefix}{self.rng.randrange(10_000_000, 99_999_999)}"

    def _restock(self, branch, manager):
        for product in self.products:
            target = self.targets.get(product.id)
            if not target or (branch.id, product.id) in self.unavailable:
                continue
            inv = Inventory.objects.filter(branch=branch, product=product).first()
            current = inv.quantity if inv else Decimal("0")
            if current < target * Decimal("0.6"):
                record_inflow(branch=branch, product=product, quantity=Decimal(target) - current,
                              performed_by=manager, reason="Morning delivery",
                              reference=f"PO-{branch.id}-{self.rng.randrange(1000, 9999)}")

    def _expense(self, day, branch, manager, category, amount, description, approve):
        with self.at(day, 9, self.rng.randrange(0, 59)):
            expense = create_expense(user=manager, category=self.expense_cats[category],
                                     amount=Decimal(amount), description=description,
                                     branch=branch, expense_date=day)
        if approve:
            with self.at(day, 18, 0):
                approve_expense(expense=expense, user=self.owner, note="OK")
        self.stats["expenses"] += 1

    # ------------------------------------------------------------- final touches
    def _finish(self):
        # Low-stock thresholds, then a little wastage today so alerts exist.
        for inv in Inventory.objects.filter(branch__business=self.business).select_related("product"):
            threshold = LOW_STOCK_THRESHOLDS.get(inv.product.sku)
            if threshold:
                inv.minimum_threshold = Decimal(threshold)
                inv.save(update_fields=["minimum_threshold"])
        wastage = [(self.branches[2], "BF-CRO", "expired", "Unsold yesterday — past freshness date"),
                   (self.branches[1], "CG-BEA", "damaged", "Bags torn during delivery")]
        for branch, sku, kind, reason in wastage:
            inv = Inventory.objects.filter(branch=branch, product__sku=sku,
                                           product__business=self.business).first()
            if inv and inv.quantity > 0:
                keep = Decimal(min(LOW_STOCK_THRESHOLDS[sku] // 2, int(inv.quantity)))
                if inv.quantity - keep > 0:
                    record_outflow(branch=branch, product=inv.product, quantity=inv.quantity - keep,
                                   movement_type=kind, reason=reason,
                                   performed_by=self.managers[branch.id])

    # ------------------------------------------------------------- summary
    def _report(self, opts):
        out = self.stdout
        out.write(self.style.SUCCESS(f"\nSeeded '{self.business.name}' ({CURRENCY}, {TZ_NAME}) "
                                     f"from {self.start} to {self.today}."))
        st = self.stats
        out.write(f"  {st['sales']} paid sales, {st['refunds']} refunds, {st['cancelled']} cancelled, "
                  f"{st['unpaid']} left unpaid, {st['expenses']} expenses.")
        out.write(f"\nLogins (password for all: {opts['password']}):")
        for u in self.users:
            branches = ", ".join(b.name for b in u.assigned_branches) or "all branches"
            out.write(f"  {u.username:<22} {u.get_role_display():<20} {branches}")
