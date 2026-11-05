from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

from apps.branches.models import Branch

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
        )
        read_only_fields = ("id", "date_joined")

    def get_branch_ids(self, obj):
        return obj.assigned_branch_ids


class UserCreateSerializer(serializers.ModelSerializer):
    """
    Used by owner / branch managers to create staff users.
    """
    password = serializers.CharField(write_only=True, validators=[validate_password])
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
        creator = self.context["request"].user
        if creator.role == UserRole.BRANCH_MANAGER and value not in (
            UserRole.CASHIER, UserRole.INVENTORY_MANAGER,
        ):
            raise serializers.ValidationError(
                "Branch managers may only create cashiers or inventory managers."
            )
        if value == UserRole.OWNER:
            raise serializers.ValidationError(
                "Owners cannot be created via this endpoint."
            )
        return value

    def validate_branch_ids(self, branch_ids):
        business_id = self.context["request"].user.business_id
        valid_ids = set(
            Branch.objects.filter(business_id=business_id, id__in=branch_ids)
            .values_list("id", flat=True)
        )
        if len(valid_ids) != len(branch_ids):
            raise serializers.ValidationError(
                "One or more branches do not belong to your business."
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

    def validate_branch_ids(self, branch_ids):
        if branch_ids is None:
            return None
        business_id = self.context["request"].user.business_id
        valid_ids = set(
            Branch.objects.filter(business_id=business_id, id__in=branch_ids)
            .values_list("id", flat=True)
        )
        if len(valid_ids) != len(branch_ids):
            raise serializers.ValidationError(
                "One or more branches do not belong to your business."
            )
        return branch_ids

    def update(self, instance, validated_data):
        branch_ids = validated_data.pop("branch_ids", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if branch_ids is not None:
            UserBranch.objects.filter(user=instance).delete()
            for branch_id in branch_ids:
                UserBranch.objects.create(user=instance, branch_id=branch_id)
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])

    def validate(self, attrs):
        try:
            user_id = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            raise serializers.ValidationError("Invalid reset link.")
        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError("Invalid or expired token.")
        attrs["user"] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


# Helper used by password reset view
def build_uid_token(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token