"""Core URL configuration."""

from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    # Test error logging (REMOVE IN PRODUCTION)
    path("TestErrorABCD/", views.test_error, name="test_error"),
    path("loader-gallery/", views.loader_gallery, name="loader_gallery"),
    path("login-matrix/", views.test_landing_page, name="login-matrix"),
    path("errors/send-to-supabase/", views.send_errors_to_supabase, name="send_errors_to_supabase"),
    path("errors/pull-from-supabase/", views.pull_errors_from_supabase, name="pull_errors_from_supabase",),
    # Settings
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/api/ollama-models/", views.OllamaModelsAPIView.as_view(), name="ollama_models_api"),
    path("settings/api/set-model/", views.SetModelAPIView.as_view(), name="set_model_api"),

    path("notes/create/", views.create_team_note, name="create_team_note"),
    path("notes/send-to-supabase/", views.send_notes_to_supabase, name="send_notes_to_supabase"),
    path("notes/pull-from-supabase/", views.pull_notes_from_supabase, name="pull_notes_from_supabase"),
]
