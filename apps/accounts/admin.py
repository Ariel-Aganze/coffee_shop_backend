from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, UserBranch


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "business", "is_active")
    list_filter = ("role", "is_active", "business")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Business & Role", {"fields": ("business", "role", "phone")}),
    )


@admin.register(UserBranch)
class UserBranchAdmin(admin.ModelAdmin):
    list_display = ("user", "branch", "assigned_at")
    list_filter = ("branch",)
    search_fields = ("user__username", "branch__name")