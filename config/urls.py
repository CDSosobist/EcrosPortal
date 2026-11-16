"""Корневые маршруты портала."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.portal.urls")),
    path("vacations/", include("apps.vacations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = "apps.portal.views.error_403"
handler404 = "apps.portal.views.error_404"

admin.site.site_header = f"{settings.PORTAL_COMPANY_NAME} — администрирование"
admin.site.site_title = "Портал ЭКРОСХИМ"
admin.site.index_title = "Управление порталом"
