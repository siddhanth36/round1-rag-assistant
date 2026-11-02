from django.contrib.auth import authenticate, login, logout
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from backend.accounts.authentication import SessionAuthentication401


def _user_json(user) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}


@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request):
    """Sets the csrftoken cookie so the frontend can send X-CSRFToken on unsafe requests."""
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    # DRF only enforces CSRF for already-authenticated sessions, so enforce it here explicitly
    # to block login CSRF (an attacker logging the victim into the attacker's account).
    SessionAuthentication401().enforce_csrf(request)

    username, password = request.data.get("username"), request.data.get("password")
    user = None
    if isinstance(username, str) and isinstance(password, str):
        user = authenticate(request, username=username, password=password)
    if user is None:  # same message for unknown user and wrong password: no account enumeration
        return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
    login(request, user)  # rotates the session key and the CSRF token
    # ponytail: no login throttling/lockout. Add django-axes or a DRF throttle before internet exposure.
    return Response(_user_json(user))


@api_view(["POST"])
def logout_view(request):
    logout(request)  # flushes the session
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def me(request):
    return Response(_user_json(request.user))
