from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Application user. ``role`` alone drives app permissions; is_staff/is_superuser only gate /admin/."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        ENGINEER = "ENGINEER", "Engineer"
        SUPERVISOR = "SUPERVISOR", "Supervisor"

    role = models.CharField(max_length=12, choices=Role.choices, db_index=True)

    REQUIRED_FIELDS = ["email", "role"]  # createsuperuser asks for a role too
