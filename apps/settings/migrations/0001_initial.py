from django.db import migrations, models
import django.db.models.deletion
from django.core.validators import MinValueValidator


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("businesses", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Currency",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=3, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("symbol", models.CharField(blank=True, max_length=10)),
                ("decimal_places", models.PositiveSmallIntegerField(default=2, validators=[MinValueValidator(0)])),
                ("country_hint", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name_plural": "Currencies",
                "ordering": ["code"],
            },
        ),
        migrations.CreateModel(
            name="BusinessSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("language", models.CharField(default="en", max_length=10)),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("date_format", models.CharField(default="YYYY-MM-DD", max_length=20)),
                ("number_format", models.CharField(default="1,234.56", max_length=20)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("business", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="settings",
                    to="businesses.business",
                )),
                ("primary_currency", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="businesses_using_as_primary",
                    to="settings.currency",
                )),
            ],
        ),
        migrations.CreateModel(
            name="CurrencyChangeLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("effective_date", models.DateTimeField()),
                ("reason", models.TextField(blank=True)),
                ("business", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="currency_changes",
                    to="businesses.business",
                )),
                ("changed_by", models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="accounts.user",
                )),
                ("new_currency", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="settings.currency",
                )),
                ("old_currency", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="settings.currency",
                )),
            ],
            options={
                "ordering": ["-changed_at"],
            },
        ),
    ]