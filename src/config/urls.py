"""URL configuration for Castor project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import (
    health_check,
    home_view,
    login_page_view,
    login_step1_reset_view,
    login_step1_view,
    login_step2_view,
    privacy_view,
    terms_view,
)
from core.views_staff_dashboard import (
    CostView,
    EngagementView,
    InvestorView,
    OverviewView,
    ReliabilityView,
    StaffDashboardRedirectView,
)

urlpatterns = [
    path("", home_view, name="home"),
    path("admin/", admin.site.urls),
    # /healthz/ is the canonical probe (nginx, uptime monitors, M6 pre-flight).
    # /api/health/ stays as alias for anything already wired to the old path.
    path("healthz/", health_check, name="healthz"),
    path("api/health/", health_check, name="health_check"),
    path("projects/", include("environments.urls")),
    path("writeback/", include("writeback.urls")),
    path("facilities/", include("facilities.urls")),
    path("core/", include("core.urls")),
    path("eastereggs/", include("eastereggs.urls")),
    path("beta/", include("beta.urls")),
    # 4D/5D BIM integration — atomic apps split from the former castor/ module.
    path("scheduling/", include("scheduling.urls")),
    path("takeoff/", include("takeoff.urls")),
    path("model-quality/", include("model_quality.urls")),
    path("viewer/", include("ifc_viewer.urls")),
    # Staff-only BI/health dashboard. Gated by is_staff inside each view.
    # Default lands on the Overview tab; future tabs (Cost, Reliability,
    # Engagement, Quality, Security, Investor) mount under the same prefix.
    path(
        "staff/dashboard/",
        StaffDashboardRedirectView.as_view(),
        name="staff_dashboard",
    ),
    path(
        "staff/dashboard/overview/",
        OverviewView.as_view(),
        name="staff_dashboard_overview",
    ),
    path(
        "staff/dashboard/cost/",
        CostView.as_view(),
        name="staff_dashboard_cost",
    ),
    path(
        "staff/dashboard/reliability/",
        ReliabilityView.as_view(),
        name="staff_dashboard_reliability",
    ),
    path(
        "staff/dashboard/engagement/",
        EngagementView.as_view(),
        name="staff_dashboard_engagement",
    ),
    path(
        "staff/dashboard/investor/",
        InvestorView.as_view(),
        name="staff_dashboard_investor",
    ),
    # Authentication — two-step login (username, then password). The GET
    # endpoint keeps the historical name "login" so all `{% url 'login' %}`
    # references in the codebase resolve unchanged.
    path("login/", login_page_view, name="login"),
    path("login/step1/", login_step1_view, name="login_step1"),
    path("login/step1/reset/", login_step1_reset_view, name="login_step1_reset"),
    path("login/step2/", login_step2_view, name="login_step2"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    # Public transparency pages — short, plain-language notes (not legal docs).
    path("privacy/", privacy_view, name="privacy"),
    path("terms/", terms_view, name="terms"),
    # Password reset — used both for forgotten passwords and as the "set
    # initial password" path the welcome email sends approved beta users to.
    # Templates are Django defaults until polish (M6).
    path(
        "password_reset/",
        auth_views.PasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers — only invoked when DEBUG=False. See core.views for
# the implementations and core/templates/errors/ for the rendered templates.
handler400 = "core.views.error_400_view"
handler403 = "core.views.error_403_view"
handler404 = "core.views.error_404_view"
handler500 = "core.views.error_500_view"
