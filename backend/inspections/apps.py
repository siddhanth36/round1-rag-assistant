from django.apps import AppConfig
from django.db.backends.signals import connection_created


def exact_float_output(sender, connection, **kwargs):
    """Make PostgreSQL return float8 values exactly, so stored model scores equal the model's output.

    The Supabase pooler sets extra_float_digits=0 (15 significant digits) and ignores startup options,
    so it is set per connection here. 1 is the PostgreSQL 12+ default: shortest exact representation.
    """
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET extra_float_digits = 1")


class InspectionsConfig(AppConfig):
    name = "backend.inspections"
    label = "inspections"

    def ready(self):
        connection_created.connect(exact_float_output, dispatch_uid="inspections_exact_float_output")
