from django.db import models


class UserRole(models.TextChoices):
    OWNER = "owner", "Business Owner"
    ADMIN = "admin", "System Administrator"
    BRANCH_MANAGER = "branch_manager", "Branch Manager"
    CASHIER = "cashier", "Cashier"
    INVENTORY_MANAGER = "inventory_manager", "Inventory Manager"
    ACCOUNTANT = "accountant", "Accountant"