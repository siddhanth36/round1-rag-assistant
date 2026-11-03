"""Admin Product Management: GET /api/products/manage/, create/edit/(de)activate, and the /products/ page."""

from django.test import TestCase
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.inspections.models import Product

MANAGE = "/api/products/manage/"


class ProductManagementTestCase(TestCase):
    """One user per role; one active product (P-PLATE01) and one inactive product (P-OLD)."""

    def setUp(self):
        self.users = {role: User.objects.create_user(username=role.lower(), password="x", role=role)
                      for role in User.Role.values}
        Product.objects.create(product_id="P-PLATE01", product_name="Demonstration metal plate")
        Product.objects.create(product_id="P-OLD", product_name="Retired plate", is_active=False)
        self.client = APIClient()

    def as_role(self, role):
        self.client.force_authenticate(self.users[role])


class ManageListingTests(ProductManagementTestCase):
    def test_admin_lists_all_products_including_inactive(self):
        self.as_role("ADMIN")
        response = self.client.get(MANAGE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(p["product_id"], p["is_active"]) for p in response.data],
                         [("P-OLD", False), ("P-PLATE01", True)])  # ordered by product ID
        self.assertEqual(set(response.data[0]), {"product_id", "product_name", "description", "is_active", "created_at"})

    def test_engineer_and_supervisor_get_403(self):
        for role in ["ENGINEER", "SUPERVISOR"]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual(self.client.get(MANAGE).status_code, 403)

    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get(MANAGE).status_code, 401)

    def test_manage_is_read_only(self):
        self.as_role("ADMIN")
        self.assertEqual(self.client.post(MANAGE, {"product_id": "MANAGE"}, format="json").status_code, 405)
        self.assertEqual(self.client.patch(MANAGE, {"is_active": False}, format="json").status_code, 405)

    def test_inspection_product_listing_still_returns_active_only(self):
        for role in User.Role.values:
            self.as_role(role)
            with self.subTest(role):
                response = self.client.get("/api/products/")
                self.assertEqual([p["product_id"] for p in response.data], ["P-PLATE01"])
                self.assertNotIn("created_at", response.data[0])  # existing response shape unchanged


class ManageWriteTests(ProductManagementTestCase):
    def setUp(self):
        super().setUp()
        self.as_role("ADMIN")

    def test_admin_creates_a_product(self):
        body = {"product_id": "P-PLATE02", "product_name": "Second plate", "description": "Synthetic plate"}
        response = self.client.post("/api/products/", body, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data, {**body, "is_active": True})
        self.assertIn("P-PLATE02", [p["product_id"] for p in self.client.get(MANAGE).data])

    def test_admin_creates_an_inactive_product(self):
        body = {"product_id": "P-DRAFT", "product_name": "Draft plate", "is_active": False}
        self.assertEqual(self.client.post("/api/products/", body, format="json").status_code, 201)
        self.assertFalse(Product.objects.get(product_id="P-DRAFT").is_active)

    def test_duplicate_product_id_is_400(self):
        response = self.client.post("/api/products/", {"product_id": "P-OLD", "product_name": "Again"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("product_id", response.data)
        self.assertEqual(Product.objects.count(), 2)

    def test_invalid_product_ids_are_400(self):
        for product_id in ["p-lower", "P PLATE", "P_PLATE", "P-PLATE!", "P" * 33, ""]:
            with self.subTest(product_id):
                response = self.client.post("/api/products/", {"product_id": product_id, "product_name": "x"},
                                            format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("product_id", response.data)
        self.assertEqual(Product.objects.count(), 2)

    def test_missing_product_name_is_400(self):
        response = self.client.post("/api/products/", {"product_id": "P-NEW"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("product_name", response.data)

    def test_admin_edits_name_and_description_but_not_product_id(self):
        response = self.client.patch("/api/products/P-PLATE01/",
                                     {"product_id": "P-HACK", "product_name": "Plate", "description": "Edited"},
                                     format="json")
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(product_name="Plate")
        self.assertEqual((product.product_id, product.description), ("P-PLATE01", "Edited"))
        self.assertFalse(Product.objects.filter(product_id="P-HACK").exists())

    def test_admin_deactivates_and_reactivates_without_deleting(self):
        url = "/api/products/P-PLATE01/"
        self.assertEqual(self.client.patch(url, {"is_active": False}, format="json").status_code, 200)
        self.assertEqual(self.client.get("/api/products/").data, [])  # gone from the inspection dropdown
        self.assertEqual(Product.objects.count(), 2)  # kept, not deleted
        self.assertEqual(self.client.patch(url, {"is_active": True}, format="json").status_code, 200)
        self.assertEqual([p["product_id"] for p in self.client.get("/api/products/").data], ["P-PLATE01"])

    def test_non_admins_cannot_create_or_edit(self):
        for role in ["ENGINEER", "SUPERVISOR"]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual(self.client.post("/api/products/", {"product_id": "P-X", "product_name": "x"},
                                                  format="json").status_code, 403)
                self.assertEqual(self.client.patch("/api/products/P-OLD/", {"is_active": True},
                                                   format="json").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch("/api/products/P-OLD/", {"is_active": True}, format="json").status_code, 401)
        self.assertFalse(Product.objects.get(product_id="P-OLD").is_active)
        self.assertFalse(Product.objects.filter(product_id="P-X").exists())


class ProductPageTests(TestCase):
    def setUp(self):
        self.users = {role: User.objects.create_user(username=role.lower(), password="x", role=role)
                      for role in User.Role.values}

    def test_admin_opens_product_management(self):
        self.client.force_login(self.users["ADMIN"])
        response = self.client.get("/products/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage registered inspection products and their availability.")
        self.assertContains(response, "js/products.js")

    def test_engineer_and_supervisor_get_403(self):
        for role in ["ENGINEER", "SUPERVISOR"]:
            self.client.force_login(self.users[role])
            with self.subTest(role):
                self.assertEqual(self.client.get("/products/").status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get("/products/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))

    def test_navigation_item_only_for_admin(self):
        for role in User.Role.values:
            self.client.force_login(self.users[role])
            with self.subTest(role):
                response = self.client.get("/inspect/")
                self.assertEqual(response.status_code, 200)
                if role == User.Role.ADMIN:
                    self.assertContains(response, 'href="/products/"')
                else:
                    self.assertNotContains(response, 'href="/products/"')
