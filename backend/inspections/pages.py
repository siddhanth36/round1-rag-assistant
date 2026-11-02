"""Round 2 HTML pages. Owned by the frontend teammate (see docs/ROUND2_API_CONTRACT.md §1).

Page views only render templates: every permission is enforced by the APIs. Each page loads
frontend/static/js/<script>.js through base.html. To add a page, add one line to PAGES.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import path
from django.views.decorators.csrf import ensure_csrf_cookie

# url, template (frontend/templates/inspections/<name>.html), page title, script (frontend/static/js/<script>.js)
PAGES = [
    ("inspect/", "inspect", "Inspect", "inspect"),
]


def page(template: str, title: str, script: str):
    @login_required
    @ensure_csrf_cookie
    def view(request, **kwargs):
        return render(request, f"inspections/{template}.html", {"page": script, "title": title, **kwargs})

    return view


urlpatterns = [path(url, page(template, title, script)) for url, template, title, script in PAGES]
