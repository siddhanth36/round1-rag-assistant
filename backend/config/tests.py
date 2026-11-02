from django.test import TestCase

from backend.accounts.models import User
from backend.config.pages import PAGES

URLS = [f"/{url}" for url, _, _ in PAGES]


class PageTests(TestCase):
    def test_every_page_requires_login(self):
        for url in URLS:
            with self.subTest(url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"/login/?next={url}")

    def test_logged_in_users_get_every_page_and_role_specific_nav(self):
        # Page access is UX only (the APIs enforce roles), so every role gets 200; the nav differs by role.
        for role, visible, hidden in [("ENGINEER", "/assistant/", "/manage/"), ("ADMIN", "/manage/", "/assistant/"),
                                      ("SUPERVISOR", "/analytics/", "/manage/")]:
            self.client.force_login(User.objects.create_user(username=role.lower(), password="x", role=role))
            for url in URLS:
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
            nav = self.client.get("/").content.decode()
            self.assertIn(f'href="{visible}"', nav)
            self.assertNotIn(f'href="{hidden}"', nav)
            self.assertIn("csrftoken", self.client.cookies)

    def test_login_page_renders_and_redirects_authenticated_users(self):
        self.assertContains(self.client.get("/login/"), 'id="login-form"')
        self.client.force_login(User.objects.create_user(username="e", password="x", role="ENGINEER"))
        self.assertRedirects(self.client.get("/login/"), "/", fetch_redirect_response=False)
