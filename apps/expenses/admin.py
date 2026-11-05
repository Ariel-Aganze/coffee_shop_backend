from django.contrib import admin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "is_active", "created_at")
    list_filter = ("is_active", "business")
    search_fields = ("name",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "id", "branch", "category", "amount", "currency_code",
        "status", "expense_date", "submitted_by", "reviewed_by",
    )
    list_filter = ("status", "business", "branch", "category", "currency_code")
    search_fields = ("description", "submitted_by__username")
    readonly_fields = (
        "business", "currency_code", "currency_decimal_places",
        "submitted_by", "reviewed_by", "reviewed_at",
        "created_at", "updated_at",
    )