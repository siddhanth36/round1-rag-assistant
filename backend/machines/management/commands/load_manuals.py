"""Register the challenge-pack manuals through the same validation as POST /api/manuals/upload/. Idempotent."""

import csv
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from backend.accounts.models import User
from backend.machines.models import Manual
from backend.machines.serializers import ManualUploadSerializer


class Command(BaseCommand):
    help = "Register the PDFs in a Manuals folder, using machine_manual_index.csv from the folder's parent."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=Path, required=True, help="the challenge pack's Manuals folder")

    def handle(self, *args, **options):
        folder: Path = options["path"]
        index = folder.parent / "machine_manual_index.csv"
        if not index.is_file():
            raise CommandError(f"{index} not found (expected next to the Manuals folder).")
        admin = User.objects.filter(role=User.Role.ADMIN).order_by("id").first()
        if admin is None:
            raise CommandError("No ADMIN user exists. Run `manage.py seed_demo` first.")

        with index.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            document_id, revision = row["document_id"].strip(), row["revision"].strip()
            if Manual.objects.filter(document_id=document_id, revision=revision).exists():
                self.stdout.write(f"exists      {document_id} rev {revision}")
                continue
            pdf = folder / row["pdf_filename"].strip()
            if not pdf.is_file():
                raise CommandError(f"{pdf} not found.")
            with pdf.open("rb") as fh:
                serializer = ManualUploadSerializer(data={
                    "machine_id": row["machine_id"].strip(), "document_id": document_id,
                    "revision": revision, "file": File(fh, name=pdf.name),
                })
                if not serializer.is_valid():
                    raise CommandError(f"{pdf.name}: {serializer.errors}")
                manual = serializer.save(uploaded_by=admin)
            self.stdout.write(f"registered  {document_id} rev {revision}  {manual.page_count} pages  ({pdf.name})")
        self.stdout.write(f"manuals     {Manual.objects.count()} registered")
