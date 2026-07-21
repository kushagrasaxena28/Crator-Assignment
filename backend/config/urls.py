"""Project URL configuration: health check, Django admin, and the API under /api/."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health),
    path("admin/", admin.site.urls),
    path("api/", include("external_services.urls")),
]


def _not_found(request, exception=None):
    return JsonResponse({"error": "not_found", "message": "No such endpoint."}, status=404)


def _server_error(request):
    return JsonResponse({"error": "internal_error", "message": "Something went wrong."}, status=500)


handler404 = _not_found
handler500 = _server_error
