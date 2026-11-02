"""HTML pages. Each requires login; role checks here are UX only — the APIs enforce every permission."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.csrf import ensure_csrf_cookie

# url, template/script name, page title
PAGES = [
    ("", "dashboard", "Dashboard"),
    ("machines/", "machines", "Machines"),
    ("assistant/", "assistant", "Assistant"),
    ("history/", "history", "History"),
    ("manuals/", "manuals", "Manuals"),
    ("manage/", "manage", "Manage corpus"),
    ("analytics/", "analytics", "Analytics"),
]


def page(name: str, title: str):
    @login_required
    @ensure_csrf_cookie
    def view(request):
        return render(request, f"{name}.html", {"page": name, "title": title, "max_upload_mb": settings.MAX_UPLOAD_MB})

    return view


@ensure_csrf_cookie
def login_page(request):
    if request.user.is_authenticated:
        return redirect("/")
    return render(request, "login.html")


urlpatterns = [path("login/", login_page)] + [path(url, page(name, title)) for url, name, title in PAGES]
