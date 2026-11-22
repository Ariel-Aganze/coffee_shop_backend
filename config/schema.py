"""
Small response shapes shared by several endpoints, used only to document
the API (drf-spectacular) so the frontend can generate accurate types.
"""
from rest_framework import serializers


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField()


class UpdatedCountSerializer(serializers.Serializer):
    updated = serializers.IntegerField()


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


# ------------------------------------------------------------------------------
# Role-dependent fields
# ------------------------------------------------------------------------------
# Fields that are left out of responses for some roles (see
# apps.accounts.access.can_see_costs). drf-spectacular marks read-only fields
# as always present, which would make generated frontend types lie, so this
# hook marks exactly these fields optional and says why.
ROLE_DEPENDENT_FIELDS = {
    "Product": {"current_cost_price": "Omitted for cashiers."},
    "ProductPrice": {"cost_price": "Omitted for cashiers."},
    "Inventory": {
        "unit_cost": "Omitted for cashiers.",
        "stock_value": "Omitted for cashiers.",
    },
    "InventorySummary": {"total_value": "Omitted for cashiers."},
    "CurrentStockRow": {
        "unit_cost": "Omitted for roles that can't see costs.",
        "stock_value": "Omitted for roles that can't see costs.",
    },
}


def mark_role_dependent_fields_optional(result, generator, request, public):
    """
    Make response types exact for generated clients:
      1. Response components (everything except the *Request ones produced by
         COMPONENT_SPLIT_REQUEST) list every property as required — this API's
         serializers always output every declared field
         (apps/reports/test_schema_sync.py checks the important ones);
      2. then the role-dependent fields below are made optional again.
    """
    schemas = result.get("components", {}).get("schemas", {})
    for name, schema in schemas.items():
        if name.endswith("Request") or "properties" not in schema:
            continue
        schema["required"] = sorted(schema["properties"])
    for component, fields in ROLE_DEPENDENT_FIELDS.items():
        schema = schemas.get(component)
        if not schema:
            continue
        for name, note in fields.items():
            if name in schema.get("required", []):
                schema["required"].remove(name)
            prop = schema.get("properties", {}).get(name)
            if prop is not None:
                prop["description"] = (prop.get("description", "") + " " + note).strip()
    return result
