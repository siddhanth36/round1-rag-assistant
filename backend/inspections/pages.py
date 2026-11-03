"""Round 2 HTML pages. Owned by the frontend teammate (see docs/ROUND2_API_CONTRACT.md §1).

Page views only render templates: every permission is enforced by the APIs. Each page loads
frontend/static/js/<script>.js through base.html. To add a page, add one line to PAGES.
ADMIN_PAGES are additionally refused (403) to every other role on the server, not only hidden in the nav.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import path
from django.views.decorators.csrf import ensure_csrf_cookie

from backend.accounts.models import User

# url, template (frontend/templates/inspections/<name>.html), page title, script (frontend/static/js/<script>.js)
PAGES = [
    ("inspect/", "inspect", "Inspect", "inspect"),
    ("inspect/batch/", "batch", "Batch inspection", "inspect-batch"),
    ("inspections/", "history", "Inspection history", "inspect-history"),
    ("inspections/<int:pk>/", "detail", "Inspection detail", "inspect-detail"),
    ("inspections/metrics/", "metrics", "Model metrics", "inspect-metrics"),
]

ADMIN_PAGES = [
    ("products/", "products", "Product Management", "products"),
]


def page(template: str, title: str, script: str, roles: tuple[str, ...] | None = None):
    """Login-required template view; with ``roles``, any other role gets 403 from the server."""

    @login_required
    @ensure_csrf_cookie
    def view(request, **kwargs):
        if roles is not None and request.user.role not in roles:
            raise PermissionDenied
        return render(request, f"inspections/{template}.html", {"page": script, "title": title, **kwargs})

    return view


urlpatterns = [path(url, page(template, title, script)) for url, template, title, script in PAGES] + [
    path(url, page(template, title, script, roles=(User.Role.ADMIN,))) for url, template, title, script in ADMIN_PAGES]
