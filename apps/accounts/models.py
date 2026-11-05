from django.contrib.auth.models import AbstractUser
from django.db import models

from .constants import UserRole


class User(AbstractUser):
    """
    Custom user model. A user belongs to a business.
    Only the owner is required to be linked to a business at creation time;
    staff users are also linked to a business and can be assigned to branches.
    """
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        help_text="Business this user belongs to.",
    )
    role = models.CharField(
        max_length=30,
        choices=UserRole.choices,
        default=UserRole.CASHIER,
        help_text="Primary role of the user.",
    )
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Deactivate the account instead of deleting it.",
    )

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def assigned_branch_ids(self):
        return list(self.branch_assignments.values_list("branch_id", flat=True))

    @property
    def assigned_branches(self):
        from apps.branches.models import Branch
        return Branch.objects.filter(user_assignments__user=self)

class UserBranch(models.Model):
    """
    Assignment of a user to a branch.
    A user may be assigned to one or more branches (AUTH-006).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="branch_assignments",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="user_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "branch")
        indexes = [models.Index(fields=["user", "branch"])]

    def __str__(self) -> str:
        return f"{self.user.username} @ {self.branch.name}"