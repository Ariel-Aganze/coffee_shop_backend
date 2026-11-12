from django.db import models


class AuditLogImmutableError(Exception):
    """Raised on any attempt to change or delete an audit record (AUD-010)."""


class AuditLogQuerySet(models.QuerySet):
    """Bulk updates and deletes are refused: the audit trail is append-only."""

    def update(self, **kwargs):
        raise AuditLogImmutableError("Audit records cannot be modified.")

    def delete(self):
        raise AuditLogImmutableError("Audit records cannot be deleted.")


class AuditLog(models.Model):
    """
    Immutable record of important actions (AUD-001..AUD-010).

    Rows can only be inserted (via apps.audit.services.log_action). Saving an
    existing row, deleting one, or bulk update/delete through the ORM raises
    AuditLogImmutableError; the admin and the API are read-only too.
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "created_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} @ {self.created_at.isoformat()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise AuditLogImmutableError("Audit records cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditLogImmutableError("Audit records cannot be deleted.")
