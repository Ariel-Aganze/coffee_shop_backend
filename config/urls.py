from django.conf import settings
from django.contrib import admin
from django.http import Http404, JsonResponse
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def health_check(_request):
    return JsonResponse({"status": "ok"})


# Uploaded files anyone may load: product photos and business logos. Expense
# receipts are private and only downloadable through the API.
PUBLIC_MEDIA_PREFIXES = ("products/", "business/")


def public_media(request, path):
    if not path.startswith(PUBLIC_MEDIA_PREFIXES) or ".." in path:
        raise Http404
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["Cache-Control"] = "public, max-age=86400"
    response["X-Content-Type-Options"] = "nosniff"
    return response


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

    path("api/audit/", include("apps.audit.urls")),
]

urlpatterns += [re_path(r"^media/(?P<path>.+)$", public_media, name="public-media")]