from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.branches.serializers import BranchSerializer
from apps.settings.models import BusinessSettings, Currency
from apps.settings.regional import (
    DATE_FORMAT_CHOICES,
    LANGUAGE_CHOICES,
    NUMBER_FORMAT_CHOICES,
    validate_timezone,
)
from apps.settings.serializers import BusinessSettingsSerializer

from config.uploads import validate_image_upload

from .models import Business



class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = (
            "id", "name", "contact_email", "contact_phone",
            "address", "logo", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_logo(self, value):
        return validate_image_upload(value, "The logo")


# ------------------------------------------------------------------------------
# Registration (SRS workflow 12.1): one request creates the business, its
# owner, settings and first branch. Each section is a typed serializer so
# errors come back per field, e.g. {"owner": {"username": ["…"]}}.
# ------------------------------------------------------------------------------
class RegistrationOwnerSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, validators=[UnicodeUsernameValidator()])
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        # Full password rules, including "too similar to your username/name/email".
        candidate = User(
            username=attrs.get("username", ""), email=attrs.get("email", ""),
            first_name=attrs.get("first_name", ""), last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs.get("password", ""), user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs


class RegistrationSettingsSerializer(serializers.Serializer):
    currency_code = serializers.CharField(max_length=3, help_text="ISO 4217 code, e.g. RWF.")
    language = serializers.ChoiceField(choices=LANGUAGE_CHOICES, default="en")
    timezone = serializers.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    date_format = serializers.ChoiceField(choices=DATE_FORMAT_CHOICES, default="YYYY-MM-DD")
    number_format = serializers.ChoiceField(choices=NUMBER_FORMAT_CHOICES, default="1,234.56")

    def validate_currency_code(self, value):
        code = value.upper()
        if not Currency.objects.filter(code=code, is_active=True).exists():
            raise serializers.ValidationError("Choose a currency from the list.")
        return code


class RegistrationBranchSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    address = serializers.CharField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class BusinessRegistrationSerializer(serializers.Serializer):
    business = BusinessSerializer()
    owner = RegistrationOwnerSerializer()
    settings = RegistrationSettingsSerializer()
    first_branch = RegistrationBranchSerializer()

    @transaction.atomic
    def create(self, validated_data):
        business_data = dict(validated_data["business"])
        business_data.pop("logo", None)  # logos are uploaded later from Settings
        business = Business.objects.create(**business_data)

        owner_data = validated_data["owner"]
        owner = User.objects.create_user(
            username=owner_data["username"],
            email=owner_data["email"],
            password=owner_data["password"],
            first_name=owner_data["first_name"],
            last_name=owner_data["last_name"],
            role=UserRole.OWNER,
            business=business,
        )

        settings_data = validated_data["settings"]
        business_settings = BusinessSettings.objects.create(
            business=business,
            primary_currency=Currency.objects.get(code=settings_data["currency_code"]),
            language=settings_data["language"],
            timezone=settings_data["timezone"],
            date_format=settings_data["date_format"],
            number_format=settings_data["number_format"],
        )

        branch_data = validated_data["first_branch"]
        branch = Branch.objects.create(business=business, **branch_data)

        return {
            "business": business,
            "owner": owner,
            "settings": business_settings,
            "branch": branch,
        }


class RegistrationResponseSerializer(serializers.Serializer):
    """Documentation of the registration response."""
    business = BusinessSerializer()
    branch = BranchSerializer()
    settings = BusinessSettingsSerializer()
