"""URL configuration for Castor project."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from core.views import home_view, health_check

urlpatterns = [
    path("", home_view, name="home"),
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health_check"),
    path("projects/", include("environments.urls")),
    path("writeback/", include("writeback.urls")),
    path("core/", include("core.urls")),

    # Authentication
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)