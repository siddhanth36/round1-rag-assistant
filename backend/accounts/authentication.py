from rest_framework.authentication import SessionAuthentication


class SessionAuthentication401(SessionAuthentication):
    """Session auth that answers anonymous requests with 401 instead of DRF's default 403.

    DRF sends 403 when the first authenticator has no WWW-Authenticate header; providing one makes
    "not logged in" (401) distinguishable from "wrong role" (403).
    """

    def authenticate_header(self, request):
        return "Session"
