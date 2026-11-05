from django.db import migrations

from apps.settings.constants import ALL_CURRENCIES


def seed_currencies(apps, schema_editor):
    Currency = apps.get_model("settings", "Currency")
    for code, name, symbol, decimal_places, country_hint in ALL_CURRENCIES:
        Currency.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "symbol": symbol,
                "decimal_places": decimal_places,
                "country_hint": country_hint,
                "is_active": True,
            },
        )


def unseed_currencies(apps, schema_editor):
    Currency = apps.get_model("settings", "Currency")
    Currency.objects.filter(
        code__in=[c[0] for c in ALL_CURRENCIES]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_currencies, unseed_currencies),
    ]