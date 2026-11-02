"""R2-5: inspection history (filters, pagination, visibility) and append-only supervisor reviews."""

import datetime as dt

from backend.inspections.models import Inspection, InspectionReview, Product
from backend.inspections.test_api import ORIGINAL_FIELDS, PACK, InspectionTestCase


class HistoryTestCase(InspectionTestCase):
    def setUp(self):
        super().setUp()
        self.plate = Product.objects.get(product_id="P-PLATE01")
        self.other = Product.objects.create(product_id="P-OTHER", product_name="Other plate")

    def make(self, user="ENGINEER", status="completed", predicted="Normal", product=None, created=None,
             review_status="unreviewed") -> Inspection:
        """A row as the services would leave it (prediction only when completed; the model isn't needed here)."""
        done = status == "completed"
        row = Inspection.objects.create(
            product=product or self.plate, submitted_by=self.users[user], status=status,
            original_filename="x.png", review_status=review_status,
            predicted_class=predicted if done else None, confidence=0.9 if done else None,
            defect_probability=(0.9 if predicted == "Defective" else 0.1) if done else None,
            model_version="BIT-PLATE-CV-1.0" if done else None,
            failure_reason="" if status != "failed" else "The file is empty.")
        if created:
            Inspection.objects.filter(pk=row.pk).update(created_at=created)
        return row

    def ids(self, query: str = "") -> list[int]:
        response = self.client.get(f"/api/inspections/{query}")
        self.assertEqual(response.status_code, 200, response.data)
        return [r["id"] for r in response.data["results"]]


class HistoryFilterTests(HistoryTestCase):
    def test_each_filter(self):
        self.as_role("SUPERVISOR")
        normal = self.make(predicted="Normal")
        defective = self.make(predicted="Defective", review_status="corrected")
        confirmed = self.make(predicted="Defective", review_status="confirmed")
        queued = self.make(status="queued")
        processing = self.make(status="processing")
        failed = self.make(status="failed")
        other = self.make(product=self.other)
        cases = {
            "?prediction=Normal": [other.id, normal.id],
            "?prediction=Defective": [confirmed.id, defective.id],
            "?review_status=corrected": [defective.id],
            "?review_status=confirmed": [confirmed.id],
            "?review_status=unreviewed": [other.id, failed.id, processing.id, queued.id, normal.id],
            "?status=queued": [queued.id],
            "?status=processing": [processing.id],
            "?status=failed": [failed.id],
            "?status=completed": [other.id, confirmed.id, defective.id, normal.id],
            "?product=P-OTHER": [other.id],
        }
        for query, expected in cases.items():
            with self.subTest(query):
                self.assertEqual(self.ids(query), expected)

    def test_date_range_is_inclusive_on_utc_created_at(self):
        self.as_role("ADMIN")
        utc = dt.timezone.utc
        day1 = self.make(created=dt.datetime(2026, 11, 1, 0, 0, tzinfo=utc))
        day2 = self.make(created=dt.datetime(2026, 11, 2, 23, 59, 59, tzinfo=utc))
        day3 = self.make(created=dt.datetime(2026, 11, 3, 12, 0, tzinfo=utc))
        self.assertEqual(self.ids("?from=2026-11-02"), [day3.id, day2.id])
        self.assertEqual(self.ids("?to=2026-11-02"), [day2.id, day1.id])
        self.assertEqual(self.ids("?from=2026-11-01&to=2026-11-01"), [day1.id])
        self.assertEqual(self.ids("?from=2026-11-02&to=2026-11-03"), [day3.id, day2.id])

    def test_filters_combine_with_and(self):
        self.as_role("SUPERVISOR")
        hit = self.make(predicted="Defective", review_status="confirmed", product=self.other,
                        created=dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc))
        self.make(predicted="Defective", review_status="confirmed", product=self.plate)
        self.make(predicted="Normal", product=self.other)
        self.make(predicted="Defective", review_status="confirmed", product=self.other,
                  created=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))
        query = "?prediction=Defective&review_status=confirmed&status=completed&product=P-OTHER&from=2026-11-01&to=2026-11-30"
        self.assertEqual(self.ids(query), [hit.id])

    def test_invalid_values_are_400_never_ignored(self):
        self.as_role("ENGINEER")
        self.make()
        cases = {
            "?prediction=Scratched": {"prediction": ["Select a valid choice. Scratched is not one of the available choices."]},
            "?status=done": {"status": ["Select a valid choice. done is not one of the available choices."]},
            "?review_status=approved": {"review_status": ["Select a valid choice. approved is not one of the available choices."]},
            "?from=yesterday": {"from": ["Enter a valid date."]},
            "?to=03/11/2026": {"to": ["Enter a valid date."]},
            "?from=2026-02-30": {"from": ["Enter a valid date."]},
            "?from=2026-11-05&to=2026-11-01": {"to": ["The end date is before the start date."]},
            "?product=P-NOPE": {"product": ["Select a valid choice. That choice is not one of the available choices."]},
            "?page=0": {"page": ["Ensure this value is greater than or equal to 1."]},
            "?page=two": {"page": ["Enter a whole number."]},
            "?predction=Normal": {"predction": ["Unknown filter."]},
        }
        for query, error in cases.items():
            with self.subTest(query):
                response = self.client.get(f"/api/inspections/{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, error)

    def test_empty_values_mean_no_filter(self):
        self.as_role("ENGINEER")
        row = self.make()
        self.assertEqual(self.ids("?prediction=&status=&from="), [row.id])

    def test_pagination_25_per_page_newest_first_with_relative_links(self):
        self.as_role("ENGINEER")
        rows = [self.make() for _ in range(27)]
        newest_first = [r.id for r in reversed(rows)]
        page1 = self.client.get("/api/inspections/?status=completed").data
        self.assertEqual((page1["count"], len(page1["results"]), page1["previous"]), (27, 25, None))
        self.assertEqual([r["id"] for r in page1["results"]], newest_first[:25])
        self.assertEqual(page1["next"], "/api/inspections/?page=2&status=completed")
        page2 = self.client.get(page1["next"]).data
        self.assertEqual([r["id"] for r in page2["results"]], newest_first[25:])
        self.assertIsNone(page2["next"])
        self.assertEqual(page2["previous"], "/api/inspections/?status=completed")
        self.assertEqual(self.client.get("/api/inspections/?page=9").status_code, 404)

    def test_rows_have_current_review_but_not_reviews(self):
        self.as_role("ENGINEER")
        row = self.make(predicted="Defective")
        InspectionReview.objects.create(inspection=row, reviewer=self.users["SUPERVISOR"], decision="corrected",
                                        reviewed_class="Normal", comment="Glare")
        Inspection.objects.filter(pk=row.pk).update(review_status="corrected")
        [result] = self.client.get("/api/inspections/").data["results"]
        self.assertNotIn("reviews", result)
        self.assertEqual(result["current_review"]["reviewed_class"], "Normal")
        self.assertEqual(result["current_review"]["reviewer"], "supervisor")
        self.assertEqual(result["original"]["predicted_class"], "Defective")
        self.assertEqual(result["review_status"], "corrected")

    def test_query_count_does_not_grow_with_rows(self):
        self.as_role("SUPERVISOR")
        for _ in range(5):
            row = self.make()
            InspectionReview.objects.create(inspection=row, reviewer=self.users["SUPERVISOR"],
                                            decision="confirmed", reviewed_class="Normal")
        with self.assertNumQueries(4):  # count, page, reviews, reviewers
            self.client.get("/api/inspections/")


class HistoryVisibilityTests(HistoryTestCase):
    def test_engineer_sees_only_own_supervisor_and_admin_see_all(self):
        mine, theirs = self.make("ENGINEER"), self.make("ENGINEER2")
        admin_row = self.make("ADMIN")
        self.as_role("ENGINEER")
        self.assertEqual(self.ids(), [mine.id])
        self.as_role("ENGINEER2")
        self.assertEqual(self.ids(), [theirs.id])
        for role in ("SUPERVISOR", "ADMIN"):
            with self.subTest(role):
                self.as_role(role)
                self.assertEqual(self.ids(), [admin_row.id, theirs.id, mine.id])

    def test_filters_cannot_widen_an_engineers_view(self):
        self.make("ENGINEER2", product=self.other)
        self.as_role("ENGINEER")
        self.assertEqual(self.ids("?product=P-OTHER"), [])

    def test_anonymous_is_401(self):
        self.assertEqual(self.client.get("/api/inspections/").status_code, 401)


class ReviewTests(HistoryTestCase):
    def review(self, pk: int, **body):
        return self.client.post(f"/api/inspections/{pk}/review/", body, format="json")

    def test_engineer_and_admin_get_403_and_nothing_changes(self):
        row = self.make(predicted="Defective")
        before = Inspection.objects.values(*ORIGINAL_FIELDS, "review_status").get(pk=row.pk)
        for role in ("ENGINEER", "ADMIN"):
            with self.subTest(role):
                self.as_role(role)
                response = self.review(row.pk, decision="confirmed", reviewed_class="Defective")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.data, {"detail": "You do not have permission to perform this action."})
        self.assertFalse(InspectionReview.objects.exists())
        self.assertEqual(Inspection.objects.values(*ORIGINAL_FIELDS, "review_status").get(pk=row.pk), before)

    def test_role_is_checked_before_the_body(self):
        self.as_role("ENGINEER")
        response = self.client.post("/api/inspections/1/review/", "{not json", content_type="application/json")
        self.assertEqual(response.status_code, 403)  # not 400 (body) or 404 (id)

    def test_anonymous_is_401(self):
        self.assertEqual(self.review(self.make().pk, decision="confirmed", reviewed_class="Normal").status_code, 401)

    def test_supervisor_confirms(self):
        self.as_role("SUPERVISOR")
        row = self.make(predicted="Defective")
        response = self.review(row.pk, decision="confirmed", reviewed_class="Defective", comment="Clear scratch")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["review_status"], "confirmed")
        self.assertEqual(response.data["current_review"], response.data["reviews"][0])
        review = response.data["current_review"]
        self.assertEqual((review["decision"], review["reviewed_class"], review["comment"], review["reviewer"]),
                         ("confirmed", "Defective", "Clear scratch", "supervisor"))
        self.assertEqual(response.data["original"]["predicted_class"], "Defective")
        self.assertEqual(response.data["id"], row.pk)

    def test_supervisor_corrects_and_comment_is_optional(self):
        self.as_role("SUPERVISOR")
        row = self.make(predicted="Defective")
        response = self.review(row.pk, decision="corrected", reviewed_class="Normal")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["review_status"], "corrected")
        self.assertEqual(response.data["current_review"]["comment"], "")
        self.assertEqual(response.data["original"]["predicted_class"], "Defective")  # never replaced

    def test_invalid_bodies_are_400_and_create_nothing(self):
        self.as_role("SUPERVISOR")
        row = self.make(predicted="Defective")
        cases = [
            ({"decision": "approve", "reviewed_class": "Defective"}, {"decision": ['"approve" is not a valid choice.']}),
            ({"decision": "confirmed", "reviewed_class": "Scratched"},
             {"reviewed_class": ['"Scratched" is not a valid choice.']}),
            ({"decision": "confirmed", "reviewed_class": "Normal"},
             {"reviewed_class": ["A confirmation must keep the predicted class (Defective)."]}),
            ({"decision": "corrected", "reviewed_class": "Defective"},
             {"reviewed_class": ["A correction must change the predicted class (Defective)."]}),
            ({"decision": "corrected", "reviewed_class": "Normal", "comment": "x" * 1001},
             {"comment": ["Ensure this field has no more than 1000 characters."]}),
            ({"reviewed_class": "Normal"}, {"decision": ["This field is required."]}),
        ]
        for body, error in cases:
            with self.subTest(body=body):
                response = self.review(row.pk, **body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, error)
        self.assertFalse(InspectionReview.objects.exists())
        self.assertEqual(Inspection.objects.get(pk=row.pk).review_status, "unreviewed")

    def test_comment_of_exactly_1000_characters_is_accepted(self):
        self.as_role("SUPERVISOR")
        row = self.make()
        response = self.review(row.pk, decision="confirmed", reviewed_class="Normal", comment="x" * 1000)
        self.assertEqual(response.status_code, 201)

    def test_incomplete_inspections_are_409(self):
        self.as_role("SUPERVISOR")
        for status in ("queued", "processing", "failed"):
            with self.subTest(status):
                row = self.make(status=status)
                response = self.review(row.pk, decision="confirmed", reviewed_class="Normal")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data, {"detail": "Only completed inspections can be reviewed."})
        self.assertFalse(InspectionReview.objects.exists())

    def test_unknown_inspection_is_404(self):
        self.as_role("SUPERVISOR")
        response = self.review(999999, decision="confirmed", reviewed_class="Normal")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {"detail": "No Inspection matches the given query."})

    def test_reviews_append_and_the_latest_is_current(self):
        self.as_role("SUPERVISOR")
        row = self.make(predicted="Defective")
        first = self.review(row.pk, decision="corrected", reviewed_class="Normal", comment="Glare").data
        second = self.review(row.pk, decision="confirmed", reviewed_class="Defective", comment="Rechecked").data
        self.assertEqual(InspectionReview.objects.filter(inspection=row).count(), 2)
        self.assertEqual(second["review_status"], "confirmed")
        self.assertEqual([r["comment"] for r in second["reviews"]], ["Rechecked", "Glare"])  # newest first
        self.assertEqual(second["reviews"][1], first["reviews"][0])  # the earlier review is unchanged
        self.assertEqual(self.client.get(f"/api/inspections/{row.pk}/").data["reviews"], second["reviews"])

    def test_original_prediction_never_changes(self):
        self.as_role("ENGINEER")
        pk = self.inspect().data["id"]  # a real model prediction
        before = Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk)
        predicted = before["predicted_class"]
        other = "Normal" if predicted == "Defective" else "Defective"
        self.as_role("SUPERVISOR")
        self.assertEqual(self.review(pk, decision="corrected", reviewed_class=other).status_code, 201)
        self.assertEqual(self.review(pk, decision="confirmed", reviewed_class=predicted).status_code, 201)
        self.assertEqual(Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk), before)

    def test_reviewer_comes_from_the_session(self):
        self.as_role("SUPERVISOR")
        row = self.make()
        response = self.review(row.pk, decision="confirmed", reviewed_class="Normal",
                               reviewer=self.users["ADMIN"].id)
        self.assertEqual(response.data["current_review"]["reviewer"], "supervisor")
        self.assertEqual(InspectionReview.objects.get().reviewer, self.users["SUPERVISOR"])

    def test_supervisor_can_review_any_engineers_inspection(self):
        self.as_role("SUPERVISOR")
        row = self.make("ENGINEER2")
        self.assertEqual(self.review(row.pk, decision="confirmed", reviewed_class="Normal").status_code, 201)

    def test_reviews_cannot_be_edited(self):
        row = self.make()
        review = InspectionReview.objects.create(inspection=row, reviewer=self.users["SUPERVISOR"],
                                                 decision="confirmed", reviewed_class="Normal")
        review.comment = "changed"
        with self.assertRaises(ValueError):
            review.save()

    def test_failed_validation_upload_is_not_reviewable(self):
        self.as_role("ENGINEER")
        pk = self.inspect(PACK / "Validation" / "too_small.png").data["id"]
        self.as_role("SUPERVISOR")
        self.assertEqual(self.review(pk, decision="confirmed", reviewed_class="Normal").status_code, 409)
