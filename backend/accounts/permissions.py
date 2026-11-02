"""Role permissions. The role is read only from the authenticated ``request.user``, never from the request."""

from rest_framework.permissions import BasePermission

from backend.accounts.models import User


def HasRole(*roles: str) -> type[BasePermission]:  # noqa: N802 (used like a class: permission_classes=[HasRole(...)])
    """Permission class allowing authenticated users whose role is one of ``roles``."""

    class _HasRole(BasePermission):
        def has_permission(self, request, view) -> bool:
            user = request.user
            return bool(user and user.is_authenticated and user.role in roles)

    return _HasRole


IsAdmin = HasRole(User.Role.ADMIN)
IsEngineer = HasRole(User.Role.ENGINEER)
IsSupervisor = HasRole(User.Role.SUPERVISOR)
