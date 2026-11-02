import csv
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.machines.models import Machine, Manual


def make_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page().insert_text((72, 72), f"Test page {n + 1}")
    return doc.tobytes()


class ManualTestCase(TestCase):
    """Isolated MEDIA_ROOT per test; users for every role; the two challenge machines."""

    def setUp(self):
        self.media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=self.media))
        # rag indexing is stubbed here (these tests cover Django validation/storage, not the engine)
        self.index = self.enterContext(mock.patch("backend.assistant.rag_adapter.index_manual",
                                                  return_value=(3, SimpleNamespace(chunks_indexed=6))))
        self.users = {role: User.objects.create_user(username=role.lower(), password="x", role=role)
                      for role in User.Role.values}
        self.hp = Machine.objects.create(machine_id="M-HP200", model="HP-200", machine_name="Hydraulic forming press")
        Machine.objects.create(machine_id="M-AC90", model="AC-90", machine_name="Air compressor")
        self.client = APIClient()

    def as_role(self, role):
        self.client.force_authenticate(self.users[role])

    def upload(self, *, name="HP200_Operations_Rev1_0.pdf", content=None, document_id="HP-OPS-01", revision="1.0"):
        file = SimpleUploadedFile(name, make_pdf(3) if content is None else content, "application/pdf")
        return self.client.post("/api/manuals/upload/", {"machine_id": "M-HP200", "document_id": document_id,
                                                         "revision": revision, "file": file}, format="multipart")

    def stored_files(self):
        return [p for p in self.media.rglob("*") if p.is_file()]


class UploadPermissionTests(ManualTestCase):
    def test_engineer_cannot_upload_a_manual(self):  # mandatory check from the challenge brief
        self.as_role("ENGINEER")
        response = self.upload()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Manual.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_supervisor_cannot_upload_a_manual(self):
        self.as_role("SUPERVISOR")
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(Manual.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_anonymous_upload_is_401(self):
        self.assertEqual(self.upload().status_code, 401)

    def test_admin_upload_is_stored_under_a_generated_name_and_indexed(self):
        self.as_role("ADMIN")
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["document_id"], body["revision"], body["page_count"], body["indexing_status"], body["chunk_count"]),
                         ("HP-OPS-01", "1.0", 3, "indexed", 6))
        self.assertEqual(body["original_filename"], "HP200_Operations_Rev1_0.pdf")
        manual = Manual.objects.get()
        self.assertRegex(manual.file.name, r"^manuals/[0-9a-f]{32}\.pdf$")
        self.assertEqual(manual.uploaded_by, self.users["ADMIN"])
        kwargs = self.index.call_args.kwargs  # rag gets the DB machine_id and the stored file, never the user's path
        self.assertEqual((kwargs["machine_id"], kwargs["document_id"], kwargs["source_filename"]),
                         ("M-HP200", "HP-OPS-01", "HP200_Operations_Rev1_0.pdf"))
        self.assertEqual(self.index.call_args.args[0], manual.file.path)

    def test_indexing_failure_is_recorded_not_hidden(self):
        self.index.side_effect = RuntimeError("embedding model offline")
        self.as_role("ADMIN")
        body = self.upload().json()
        self.assertEqual((body["indexing_status"], body["chunk_count"]), ("failed", None))
        self.assertIn("embedding model offline", body["indexing_error"])


class UploadValidationTests(ManualTestCase):
    def setUp(self):
        super().setUp()
        self.as_role("ADMIN")

    def assert_rejected(self, response, field, fragment):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(fragment, str(response.json()[field]))
        self.assertEqual(Manual.objects.count(), 0)
        self.assertEqual(self.stored_files(), [])

    def test_non_pdf_content_is_rejected(self):
        cases = [("text renamed to .pdf", b"just some text", "not a PDF"),
                 ("PDF header, broken body", b"%PDF-1.7\n garbage that is not a pdf", "could not be opened")]
        for label, content, fragment in cases:
            with self.subTest(label):
                self.assert_rejected(self.upload(content=content), "file", fragment)

    def test_wrong_extension_is_rejected(self):
        self.assert_rejected(self.upload(name="manual.txt"), "file", ".pdf")

    @override_settings(MAX_UPLOAD_MB=1)
    def test_oversized_file_is_rejected(self):
        self.assert_rejected(self.upload(content=b"%PDF-1.7\n" + b"0" * (1024 * 1024)), "file", "1 MB limit")

    def test_duplicate_document_revision_is_rejected(self):
        self.assertEqual(self.upload().status_code, 201)
        response = self.upload()
        self.assertEqual(response.status_code, 400)
        self.assertIn("already registered", str(response.json()))
        self.assertEqual(Manual.objects.count(), 1)

    def test_unknown_machine_is_rejected(self):
        file = SimpleUploadedFile("m.pdf", make_pdf(), "application/pdf")
        response = self.client.post("/api/manuals/upload/", {"machine_id": "M-NOPE", "document_id": "X-1",
                                                             "revision": "1.0", "file": file}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("machine_id", response.json())

    def test_path_traversal_in_filename_stays_inside_media_root(self):
        for name in ["../../../evil.pdf", "..\\..\\..\\evil.pdf", "/etc/evil.pdf"]:
            with self.subTest(name):
                Manual.objects.all().delete()
                response = self.upload(name=name, document_id="TRAV-1")
                self.assertEqual(response.status_code, 201, response.content)
                self.assertEqual(response.json()["original_filename"], "evil.pdf")
                path = Path(Manual.objects.get().file.path).resolve()
                self.assertTrue(path.is_relative_to((self.media / "manuals").resolve()))
        self.assertEqual(list(self.media.rglob("evil.pdf")), [])  # the user's name is never used on disk


class ManualReadTests(ManualTestCase):
    def setUp(self):
        super().setUp()
        self.as_role("ADMIN")
        self.manual_id = self.upload().json()["id"]
        self.client.force_authenticate(None)

    def test_machines_and_manuals_are_listed_for_any_logged_in_role(self):
        self.as_role("ENGINEER")
        machines = self.client.get("/api/machines/").json()
        self.assertEqual([m["machine_id"] for m in machines], ["M-AC90", "M-HP200"])
        self.assertEqual(len(self.client.get("/api/manuals/?machine=M-HP200").json()), 1)
        self.assertEqual(self.client.get("/api/manuals/?machine=M-AC90").json(), [])
        self.assertEqual(self.client.get(f"/api/manuals/{self.manual_id}/").json()["document_id"], "HP-OPS-01")

    def test_only_admin_registers_machines(self):
        payload = {"machine_id": "M-XY1", "model": "XY-1", "machine_name": "Test press"}
        for role in ("ENGINEER", "SUPERVISOR"):
            self.as_role(role)
            self.assertEqual(self.client.post("/api/machines/", payload, format="json").status_code, 403)
        self.as_role("ADMIN")
        self.assertEqual(self.client.post("/api/machines/", payload, format="json").status_code, 201)

    def test_page_endpoint_bounds(self):
        self.as_role("ENGINEER")
        ok = self.client.get(f"/api/manuals/{self.manual_id}/page/3/")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["file_url"], f"/api/manuals/{self.manual_id}/file/#page=3")
        for page in (0, 4):
            self.assertEqual(self.client.get(f"/api/manuals/{self.manual_id}/page/{page}/").status_code, 404)

    def test_file_stream_requires_login_and_is_inline_pdf(self):
        self.assertEqual(self.client.get(f"/api/manuals/{self.manual_id}/file/").status_code, 401)
        self.as_role("SUPERVISOR")
        response = self.client.get(f"/api/manuals/{self.manual_id}/file/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF-"))


class LoadManualsTests(ManualTestCase):
    MANUALS = [("M-HP200", "HP-200", "Hydraulic forming press", "HP-OPS-01", "HP200_Operations_Rev1_0.pdf"),
               ("M-HP200", "HP-200", "Hydraulic forming press", "HP-MNT-01", "HP200_Maintenance_Rev1_0.pdf"),
               ("M-AC90", "AC-90", "Air compressor", "AC-OM-01", "AC90_Operations_Maintenance_Rev1_0.pdf")]

    def setUp(self):
        super().setUp()
        pack = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.folder = pack / "Manuals"
        self.folder.mkdir()
        with (pack / "machine_manual_index.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["machine_id", "model", "machine_name", "document_id", "revision", "pdf_filename"])
            for machine_id, model, name, document_id, filename in self.MANUALS:
                writer.writerow([machine_id, model, name, document_id, "1.0", filename])
                (self.folder / filename).write_bytes(make_pdf(3))

    def test_registers_exactly_three_manuals_and_is_idempotent(self):
        call_command("load_manuals", "--path", str(self.folder), stdout=StringIO())
        call_command("load_manuals", "--path", str(self.folder), stdout=StringIO())
        self.assertEqual(
            sorted(Manual.objects.values_list("machine__machine_id", "document_id", "revision", "page_count")),
            [("M-AC90", "AC-OM-01", "1.0", 3), ("M-HP200", "HP-MNT-01", "1.0", 3), ("M-HP200", "HP-OPS-01", "1.0", 3)],
        )
        self.assertEqual(len(self.stored_files()), 3)
