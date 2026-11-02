import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory

from backend.accounts.management.commands.seed_demo import DEMO_USERS
from backend.accounts.models import User
from backend.accounts.permissions import HasRole
from backend.machines.models import Machine

PASSWORD = "Correct-Horse-42"


def make_user(username: str, role: str) -> User:
    return User.objects.create_user(username=username, password=PASSWORD, role=role)


class AuthApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        for username, role in [("admin", "ADMIN"), ("engineer", "ENGINEER"), ("supervisor", "SUPERVISOR")]:
            make_user(username, role)

    def login(self, username, password=PASSWORD):
        return self.client.post("/api/auth/login/", {"username": username, "password": password}, format="json")

    def test_each_role_logs_in_and_me_returns_its_role(self):
        for username, role in [("admin", "ADMIN"), ("engineer", "ENGINEER"), ("supervisor", "SUPERVISOR")]:
            with self.subTest(role=role):
                response = self.login(username)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["role"], role)

                me = self.client.get("/api/auth/me/")
                self.assertEqual(me.status_code, 200)
                self.assertEqual(me.json(), {"id": User.objects.get(username=username).id,
                                             "username": username, "role": role})
                self.client.logout()

    def test_wrong_password_is_rejected_with_generic_message(self):
        response = self.login("engineer", "wrong-password")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Invalid username or password."})
        self.assertEqual(self.login("nobody").json(), response.json())  # no user enumeration
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_unauthenticated_me_is_401(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_logout_ends_the_session(self):
        self.login("engineer")
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_role_in_request_body_is_ignored(self):
        response = self.client.post("/api/auth/login/", {"username": "engineer", "password": PASSWORD,
                                                         "role": "ADMIN"}, format="json")
        self.assertEqual(response.json()["role"], "ENGINEER")


class CsrfTests(TestCase):
    """A browser-like client that enforces CSRF, as a real session cookie would."""

    def setUp(self):
        make_user("engineer", "ENGINEER")
        self.client = APIClient(enforce_csrf_checks=True)
        self.credentials = {"username": "engineer", "password": PASSWORD}

    def test_login_without_csrf_token_is_rejected(self):
        response = self.client.post("/api/auth/login/", self.credentials, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["detail"])
        self.assertNotIn("sessionid", self.client.cookies)

    def test_login_with_token_from_csrf_endpoint_succeeds(self):
        self.assertEqual(self.client.get("/api/auth/csrf/").status_code, 204)
        token = self.client.cookies["csrftoken"].value

        response = self.client.post("/api/auth/login/", self.credentials, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_logout_without_csrf_token_is_rejected(self):
        self.client.get("/api/auth/csrf/")
        token = self.client.cookies["csrftoken"].value
        self.client.post("/api/auth/login/", self.credentials, format="json", HTTP_X_CSRFTOKEN=token)

        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 403)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)  # still logged in


class HasRoleTests(TestCase):
    def check(self, user, *roles):
        request = APIRequestFactory().get("/")
        request.user = user
        return HasRole(*roles)().has_permission(request, None)

    def test_allows_only_listed_roles(self):
        supervisor = User(username="s", role="SUPERVISOR")
        self.assertTrue(self.check(supervisor, "ADMIN", "SUPERVISOR"))
        self.assertFalse(self.check(supervisor, "ADMIN"))

    def test_denies_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(self.check(AnonymousUser(), "ADMIN", "ENGINEER", "SUPERVISOR"))


INDEX_CSV = (  # the exact contents of the challenge pack's machine_manual_index.csv
    "machine_id,model,machine_name,document_id,revision,pdf_filename\n"
    "M-HP200,HP-200,Hydraulic forming press,HP-OPS-01,1.0,HP200_Operations_Rev1_0.pdf\n"
    "M-HP200,HP-200,Hydraulic forming press,HP-MNT-01,1.0,HP200_Maintenance_Rev1_0.pdf\n"
    "M-AC90,AC-90,Air compressor,AC-OM-01,1.0,AC90_Operations_Maintenance_Rev1_0.pdf\n"
)


class SeedDemoTests(TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.csv = tmp / "machine_manual_index.csv"
        self.csv.write_text(INDEX_CSV, encoding="utf-8")

    def run_seed(self, **env):
        out = StringIO()
        unset = {var: "" for _, _, var in DEMO_USERS}  # ignore any DEMO_* values loaded from backend/.env
        with mock.patch.dict("os.environ", unset | env):
            call_command("seed_demo", "--csv", str(self.csv), stdout=out)
        return out.getvalue()

    def test_creates_the_four_demo_users_with_roles(self):
        self.run_seed()
        self.assertEqual(
            dict(User.objects.values_list("username", "role")),
            {"admin": "ADMIN", "engineer": "ENGINEER", "engineer2": "ENGINEER", "supervisor": "SUPERVISOR"},
        )
        self.assertTrue(User.objects.get(username="admin").is_superuser)
        self.assertFalse(User.objects.get(username="engineer").is_staff)

    def test_registers_both_machines_from_the_csv(self):
        self.run_seed()
        self.assertEqual(
            list(Machine.objects.order_by("machine_id").values_list("machine_id", "model", "machine_name")),
            [("M-AC90", "AC-90", "Air compressor"), ("M-HP200", "HP-200", "Hydraulic forming press")],
        )

    def test_is_idempotent_and_prints_generated_passwords_once(self):
        first = self.run_seed()
        hashes = dict(User.objects.values_list("username", "password"))
        second = self.run_seed()

        self.assertEqual(User.objects.count(), 4)
        self.assertEqual(Machine.objects.count(), 2)
        self.assertEqual(dict(User.objects.values_list("username", "password")), hashes)  # unchanged
        self.assertEqual(first.count("password:"), 4)
        self.assertNotIn("password:", second)

    def test_missing_csv_is_a_clear_error(self):
        with self.assertRaisesMessage(CommandError, "not found"):
            call_command("seed_demo", "--csv", str(self.csv.with_name("missing.csv")), stdout=StringIO())


    def test_uses_password_from_env(self):
        self.run_seed(DEMO_ENGINEER_PASSWORD="Env-Provided-Pass-7")
        self.assertTrue(User.objects.get(username="engineer").check_password("Env-Provided-Pass-7"))


class DatabaseTests(TestCase):
    def test_runs_on_postgresql_not_sqlite(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))  # never the real database
