"""Core URL configuration."""

from django.conf import settings
from django.urls import path

from . import views, views_byok

app_name = "core"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    # Test error logging (REMOVE IN PRODUCTION)
    path("TestErrorABCD/", views.test_error, name="test_error"),
    path("loader-gallery/", views.loader_gallery, name="loader_gallery"),
    path("login-matrix/", views.test_landing_page, name="login-matrix"),
    path("errors/send-to-supabase/", views.send_errors_to_supabase, name="send_errors_to_supabase"),
    path(
        "errors/pull-from-supabase/",
        views.pull_errors_from_supabase,
        name="pull_errors_from_supabase",
    ),
    # Browser-side WebSocket error beacon. Called via navigator.sendBeacon
    # from page JS — see writeback/templates/writeback/tabs/_modify.html.
    path("log/ws-error/", views.log_ws_client_error, name="log_ws_client_error"),
    # Settings
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path(
        "settings/api/ollama-models/", views.OllamaModelsAPIView.as_view(), name="ollama_models_api"
    ),
    path("settings/api/set-model/", views.SetModelAPIView.as_view(), name="set_model_api"),
    path("settings/api/set-theme/", views.SetThemeAPIView.as_view(), name="set_theme_api"),
    # BYOK (Bring Your Own Key) — encrypted credential management.
    path(
        "settings/api/byok/save-key/<str:provider>/",
        views_byok.SaveKeyView.as_view(),
        name="byok_save_key",
    ),
    path(
        "settings/api/byok/remove-key/<str:provider>/",
        views_byok.RemoveKeyView.as_view(),
        name="byok_remove_key",
    ),
    path(
        "settings/api/byok/set-provider/<str:purpose>/",
        views_byok.SetProviderView.as_view(),
        name="byok_set_provider",
    ),
    path(
        "settings/api/byok/set-model/<str:provider>/",
        views_byok.SetModelView.as_view(),
        name="byok_set_model",
    ),
    path("notes/create/", views.create_team_note, name="create_team_note"),
    path("notes/send-to-supabase/", views.send_notes_to_supabase, name="send_notes_to_supabase"),
    path(
        "notes/pull-from-supabase/", views.pull_notes_from_supabase, name="pull_notes_from_supabase"
    ),
]

# DEBUG-only preview route for error templates. handlerXXX in config/urls.py
# only fire for real errors when DEBUG=False, so we keep this open path for
# design iteration. The view itself re-checks settings.DEBUG and 404s in prod.
if settings.DEBUG:
    urlpatterns += [
        path(
            "dev/preview-error/<int:code>/",
            views.preview_error_view,
            name="preview_error",
        ),
    ]
