from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

from apps.branches.models import Branch
from drf_spectacular.utils import extend_schema_field

from .constants import UserRole
from .models import User, UserBranch


class UserSerializer(serializers.ModelSerializer):
    branch_ids = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_active",
            "branch_ids",
            "date_joined",
            "last_login",
        )
        read_only_fields = ("id", "date_joined", "last_login")

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_branch_ids(self, obj):
        # .all() uses prefetch_related("branch_assignments") when the list
        # view prefetched it, instead of one query per user.
        return sorted(a.branch_id for a in obj.branch_assignments.all())


# Roles a branch manager may create and manage (RBAC 4.2 "own branch").
BRANCH_MANAGED_ROLES = (UserRole.CASHIER, UserRole.INVENTORY_MANAGER)


def _validate_role_for(actor, value):
    """Which roles `actor` may give to a user (on create or update)."""
    if value == UserRole.OWNER:
        raise serializers.ValidationError(
            "The owner role cannot be assigned through this endpoint."
        )
    if actor.role == UserRole.BRANCH_MANAGER and value not in BRANCH_MANAGED_ROLES:
        raise serializers.ValidationError(
            "Branch managers may only assign the cashier or inventory manager role."
        )
    if actor.role != UserRole.OWNER and value == UserRole.ADMIN:
        raise serializers.ValidationError("Only the owner can assign the admin role.")
    return value


def _validate_branch_ids_for(actor, branch_ids):
    """Branches must belong to the business — and, for a branch manager,
    be among the branches they manage."""
    valid_ids = set(
        Branch.objects.filter(business_id=actor.business_id, id__in=branch_ids)
        .values_list("id", flat=True)
    )
    if len(valid_ids) != len(set(branch_ids)):
        raise serializers.ValidationError(
            "One or more branches do not belong to your business."
        )
    if actor.role == UserRole.BRANCH_MANAGER:
        outside = set(branch_ids) - set(actor.assigned_branch_ids)
        if outside:
            raise serializers.ValidationError(
                "You can only assign staff to branches you manage."
            )
    return list(dict.fromkeys(branch_ids))



def _validate_unique_email(value, instance=None):
    """Email identifies an account for password resets: unique, any case."""
    value = (value or "").strip()
    if value:
        qs = User.objects.filter(email__iexact=value)
        if instance is not None:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This email is already used by another account.")
    return value


class UserCreateSerializer(serializers.ModelSerializer):
    """
    Used by the owner, admins and branch managers to create staff users.
    """
    password = serializers.CharField(write_only=True)
    branch_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False, default=list,
    )

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "password",
            "first_name", "last_name", "phone", "role", "branch_ids",
        )
        read_only_fields = ("id",)

    def validate_role(self, value):
        return _validate_role_for(self.context["request"].user, value)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_email(self, value):
        return _validate_unique_email(value)

    def validate(self, attrs):
        # Full password rules, including similarity to the new user's details.
        candidate = User(
            username=attrs.get("username", ""), email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""), last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs.get("password", ""), user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def validate_branch_ids(self, branch_ids):
        actor = self.context["request"].user
        branch_ids = _validate_branch_ids_for(actor, branch_ids)
        if actor.role == UserRole.BRANCH_MANAGER and not branch_ids:
            raise serializers.ValidationError(
                "Assign the new employee to at least one of your branches."
            )
        return branch_ids

    def create(self, validated_data):
        branch_ids = validated_data.pop("branch_ids", [])
        creator = self.context["request"].user
        password = validated_data.pop("password")
        user = User(
            business=creator.business,
            **validated_data,
        )
        user.set_password(password)
        user.save()
        for branch_id in branch_ids:
            UserBranch.objects.create(user=user, branch_id=branch_id)
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    branch_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False,
    )

    class Meta:
        model = User
        fields = (
            "email", "first_name", "last_name", "phone",
            "role", "is_active", "branch_ids",
        )

    def validate_role(self, value):
        actor = self.context["request"].user
        if self.instance is not None and value == self.instance.role:
            return value  # unchanged
        return _validate_role_for(actor, value)

    def validate_email(self, value):
        return _validate_unique_email(value, self.instance)

    def validate_branch_ids(self, branch_ids):
        if branch_ids is None:
            return None
        return _validate_branch_ids_for(self.context["request"].user, branch_ids)

    def update(self, instance, validated_data):
        actor = self.context["request"].user
        branch_ids = validated_data.pop("branch_ids", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if branch_ids is not None:
            assignments = UserBranch.objects.filter(user=instance)
            if actor.role == UserRole.BRANCH_MANAGER:
                # A branch manager only controls assignments to their own
                # branches; the employee's other assignments are kept.
                assignments = assignments.filter(branch_id__in=actor.assigned_branch_ids)
            assignments.delete()
            for branch_id in branch_ids:
                UserBranch.objects.get_or_create(user=instance, branch_id=branch_id)
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        # Pass the user so "too similar to your username/name/email" is checked.
        validate_password(value, user=user)
        if user.check_password(value):
            raise serializers.ValidationError(
                "Choose a password different from your current one."
            )
        return value


class PasswordChangedSerializer(serializers.Serializer):
    """Response of change-password: fresh tokens for this device (others are signed out)."""
    detail = serializers.CharField()
    access = serializers.CharField()
    refresh = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    INVALID_LINK = "This reset link is invalid or has expired. Request a new one."

    def validate(self, attrs):
        try:
            user_id = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=user_id, is_active=True)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            raise serializers.ValidationError({"token": [self.INVALID_LINK]})
        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError({"token": [self.INVALID_LINK]})
        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        attrs["user"] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


# Helper used by password reset view
def build_uid_token(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token

# ------------------------------------------------------------------------------
# Documentation-only shapes (OpenAPI) for the auth endpoints
# ------------------------------------------------------------------------------
class LoginCredentialsSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()


class MeUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /api/auth/me/ — a user edits their OWN contact details. Role,
    branches and active status are deliberately not editable here (those are
    managed by the owner / managers through /api/auth/users/).
    """

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone")

    def validate_email(self, value):
        value = value.strip()
        if value and User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            # Email identifies the account for password resets: keep it unique.
            raise serializers.ValidationError("This email is already used by another account.")
        return value


class SetPasswordSerializer(serializers.Serializer):
    """POST /api/auth/users/{id}/set-password/ — owner/manager sets a staff password."""
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value, user=self.context["target"])
        return value
