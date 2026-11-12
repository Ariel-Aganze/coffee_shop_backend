from django.urls import path

from .views import BusinessSettingsViewSet, CurrencyListView

# The settings resource is a singleton per business ("me"), so it is routed
# explicitly instead of through a router (which would add a meaningless /<id>/).
settings_detail = BusinessSettingsViewSet.as_view({"get": "retrieve", "patch": "partial_update"})
change_currency = BusinessSettingsViewSet.as_view({"post": "change_currency"})
currency_history = BusinessSettingsViewSet.as_view({"get": "currency_history"})

urlpatterns = [
    path("currencies/", CurrencyListView.as_view(), name="currency-list"),
    path("me/", settings_detail, name="settings-detail"),
    path("me/change-currency/", change_currency, name="settings-change-currency"),
    path("me/currency-history/", currency_history, name="settings-currency-history"),
]
