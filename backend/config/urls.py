from django.contrib import admin
from django.urls import path

from backend.accounts import views as accounts

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/csrf/", accounts.csrf),
    path("api/auth/login/", accounts.login_view),
    path("api/auth/logout/", accounts.logout_view),
    path("api/auth/me/", accounts.me),
]
