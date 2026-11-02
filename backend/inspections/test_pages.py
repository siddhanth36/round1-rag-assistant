"""Round 2 page routes: login required, template renders. No database needed (RequestFactory)."""

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase

from backend.accounts.models import User
from backend.inspections.pages import PAGES, page


class Round2PageTests(SimpleTestCase):
    def test_every_page_requires_login_and_renders_for_any_role(self):
        for url, template, title, script in PAGES:
            view = page(template, title, script)
            with self.subTest(url):
                request = RequestFactory().get(f"/{url}")
                request.user = AnonymousUser()
                response = view(request)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith("/login/"))
                for role in User.Role.values:
                    request.user = User(username=role.lower(), role=role)  # unsaved: no DB needed
                    response = view(request)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f"js/{script}.js")
