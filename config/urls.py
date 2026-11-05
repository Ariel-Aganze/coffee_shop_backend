from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health_check(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    # Auth & users
    path("api/auth/", include("apps.accounts.urls")),

    # Business & branches
    path("api/businesses/", include("apps.businesses.urls")),
    path("api/branches/", include("apps.branches.urls")),

    # Settings & currencies
    path("api/settings/", include("apps.settings.urls")),

    path("api/", include("apps.products.urls")),

    path("api/", include("apps.inventory.urls")),

    path("api/sales/", include("apps.sales.urls")),

    path("api/", include("apps.cash_management.urls")),

    path("api/", include("apps.expenses.urls")),

    path("api/reports/", include("apps.reports.urls")),

    path("api/", include("apps.notifications.urls")),

    
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)