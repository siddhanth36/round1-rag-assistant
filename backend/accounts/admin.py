from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from backend.accounts.models import User


@admin.register(User)
class RoleUserAdmin(UserAdmin):
    list_display = ("username", "role", "is_active", "is_staff", "last_login")
    fieldsets = UserAdmin.fieldsets + (("Application role", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Application role", {"fields": ("role",)}),)
