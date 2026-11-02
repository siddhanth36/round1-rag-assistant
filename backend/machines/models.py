from django.core.validators import RegexValidator
from django.db import models


class Machine(models.Model):
    """A registered machine. Field names follow machine_manual_index.csv.

    ``machine_id`` is copied into every vector record by rag/, so it must never change after creation.
    """

    machine_id = models.CharField(max_length=32, unique=True, validators=[RegexValidator(r"^[A-Z0-9-]+$")])
    model = models.CharField(max_length=32, unique=True)
    machine_name = models.CharField(max_length=120)
