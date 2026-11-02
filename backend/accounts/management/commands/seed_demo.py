"""Create the demo users and register the machines from machine_manual_index.csv. Safe to run repeatedly."""

import csv
import os
import secrets
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from backend.accounts.models import User
from backend.machines.models import Machine

DEMO_USERS = [  # username, role, password env var
    ("admin", User.Role.ADMIN, "DEMO_ADMIN_PASSWORD"),
    ("engineer", User.Role.ENGINEER, "DEMO_ENGINEER_PASSWORD"),
    ("engineer2", User.Role.ENGINEER, "DEMO_ENGINEER2_PASSWORD"),
    ("supervisor", User.Role.SUPERVISOR, "DEMO_SUPERVISOR_PASSWORD"),
]
DEFAULT_CSV = settings.BASE_DIR / "challenge-pack" / "machine_manual_index.csv"


class Command(BaseCommand):
    help = "Create demo users (passwords from DEMO_*_PASSWORD) and the machines listed in machine_manual_index.csv."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="path to machine_manual_index.csv")

    def handle(self, *args, **options):
        self.seed_users()
        self.seed_machines(options["csv"])

    def seed_users(self):
        # Existing users keep their password unless its env var is set; generated ones are printed once.
        for username, role, env_var in DEMO_USERS:
            user, created = User.objects.get_or_create(username=username, defaults={"role": role})
            env_password = os.environ.get(env_var, "").strip()
            generated = None
            if env_password:
                user.set_password(env_password)
            elif created:
                generated = secrets.token_urlsafe(12)
                user.set_password(generated)
            user.role = role
            user.is_staff = user.is_superuser = role == User.Role.ADMIN  # admin can manage users in /admin/
            user.save()

            shown = f"  password: {generated}" if generated else ""  # only on the run that created the user
            self.stdout.write(f"user     {username:<11} {role:<10} {'created' if created else 'exists'}{shown}")

    def seed_machines(self, path: Path):
        if not path.is_file():
            raise CommandError(f"{path} not found. Extract the challenge pack into challenge-pack/ or pass --csv.")
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))  # one row per manual; machines repeat
        for row in rows:
            machine, created = Machine.objects.update_or_create(
                machine_id=row["machine_id"].strip(),
                defaults={"model": row["model"].strip(), "machine_name": row["machine_name"].strip()},
            )
            if created:
                self.stdout.write(f"machine  {machine.machine_id:<11} {machine.model:<10} created")
        self.stdout.write(f"machines {Machine.objects.count()} registered")
