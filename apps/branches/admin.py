from django.contrib import admin

from .models import Branch


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "is_active", "created_at")
    list_filter = ("is_active", "business")
    search_fields = ("name", "business__name")