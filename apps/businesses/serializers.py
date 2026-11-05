from django.db import transaction
from rest_framework import serializers

from apps.accounts.constants import UserRole
from apps.accounts.models import User
from apps.branches.models import Branch
from apps.settings.models import BusinessSettings, Currency

from .models import Business


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = (
            "id", "name", "contact_email", "contact_phone",
            "address", "logo", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class BusinessRegistrationSerializer(serializers.Serializer):
    """
    One-shot onboarding request:
    {
      "business": { name, contact_email, contact_phone, address },
      "owner": { username, email, password, first_name, last_name },
      "settings": { currency_code, language, timezone, date_format, number_format },
      "first_branch": { name, address, phone }
    }
    """
    business = BusinessSerializer()
    owner = serializers.DictField()
    settings = serializers.DictField()
    first_branch = serializers.DictField()

    def validate_owner(self, value):
        required = ["username", "email", "password"]
        for field in required:
            if not value.get(field):
                raise serializers.ValidationError(f"'{field}' is required.")
        if User.objects.filter(username=value["username"]).exists():
            raise serializers.ValidationError("Username already in use.")
        if User.objects.filter(email=value["email"]).exists():
            raise serializers.ValidationError("Email already in use.")
        if len(value["password"]) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        return value

    def validate_settings(self, value):
        code = value.get("currency_code")
        if not code:
            raise serializers.ValidationError("'currency_code' is required.")
        if not Currency.objects.filter(code=code.upper(), is_active=True).exists():
            raise serializers.ValidationError(f"Unknown or inactive currency '{code}'.")
        return value

    def validate_first_branch(self, value):
        if not value.get("name"):
            raise serializers.ValidationError("Branch name is required.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        # 1. Business
        business_data = validated_data["business"]
        business = Business.objects.create(**business_data)

        # 2. Owner user
        owner_data = validated_data["owner"]
        owner = User.objects.create_user(
            username=owner_data["username"],
            email=owner_data["email"],
            password=owner_data["password"],
            first_name=owner_data.get("first_name", ""),
            last_name=owner_data.get("last_name", ""),
            role=UserRole.OWNER,
            business=business,
        )

        # 3. Settings
        settings_data = validated_data["settings"]
        currency = Currency.objects.get(code=settings_data["currency_code"].upper())
        business_settings = BusinessSettings.objects.create(
            business=business,
            primary_currency=currency,
            language=settings_data.get("language", "en"),
            timezone=settings_data.get("timezone", "UTC"),
            date_format=settings_data.get("date_format", "YYYY-MM-DD"),
            number_format=settings_data.get("number_format", "1,234.56"),
        )

        # 4. First branch
        branch_data = validated_data["first_branch"]
        branch = Branch.objects.create(
            business=business,
            name=branch_data["name"],
            address=branch_data.get("address", ""),
            phone=branch_data.get("phone", ""),
        )

        return {
            "business": business,
            "owner": owner,
            "settings": business_settings,
            "branch": branch,
        }