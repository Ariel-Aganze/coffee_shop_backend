from django.db import models


class NotificationType(models.TextChoices):
    LOW_STOCK = "low_stock", "Low Stock"
    INVENTORY_ADJUSTMENT = "inventory_adjustment", "Inventory Adjustment"
    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    BUSINESS_EVENT = "business_event", "Business Event"
    CASH_DISCREPANCY = "cash_discrepancy", "Cash Discrepancy"
    STOCK_TRANSFER = "stock_transfer", "Stock Transfer"
    MISSING_END_OF_DAY = "missing_end_of_day", "Missing End-of-Day Records"


class Notification(models.Model):
    """
    Per-user notification (NOT-001..NOT-008).
    Only the recipient may read or mark it.
    """
    recipient = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="+",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )
    notification_type = models.CharField(max_length=40, choices=NotificationType.choices)
    title = models.CharField(max_length=200)
    message = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
            models.Index(fields=["recipient", "notification_type"]),
        ]

    def __str__(self) -> str:
        return f"[{self.notification_type}] {self.title} → {self.recipient_id}"