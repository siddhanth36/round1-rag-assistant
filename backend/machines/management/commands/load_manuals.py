"""Register the challenge-pack manuals through the same validation as POST /api/manuals/upload/. Idempotent."""

import csv
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from backend.accounts.models import User
from backend.machines.models import Manual
from backend.machines.serializers import ManualUploadSerializer, request_indexing


class Command(BaseCommand):
    help = "Register the PDFs in a Manuals folder, using machine_manual_index.csv from the folder's parent."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=Path, required=True, help="the challenge pack's Manuals folder")
        parser.add_argument("--reindex", action="store_true", help="re-run rag indexing for manuals already registered")

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
            existing = Manual.objects.filter(document_id=document_id, revision=revision).first()
            if existing:
                if options["reindex"]:
                    existing = request_indexing(existing)  # idempotent: deterministic chunk ids, stale ids removed
                self.stdout.write(f"exists      {document_id} rev {revision}  {self.state(existing)}")
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
            self.stdout.write(f"registered  {document_id} rev {revision}  {manual.page_count} pages  {self.state(manual)}  ({pdf.name})")
        self.stdout.write(f"manuals     {Manual.objects.count()} registered")

    @staticmethod
    def state(manual: Manual) -> str:
        if manual.indexing_status == Manual.Status.INDEXED:
            return f"indexed, {manual.chunk_count} chunks"
        return f"{manual.indexing_status} {manual.indexing_error}".strip()
