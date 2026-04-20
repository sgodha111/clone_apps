import tempfile
import unittest
from pathlib import Path

from tinyurl.app import create_app


class TinyUrlAppTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(Path(self.temp_dir.name) / "tinyurl.sqlite3"),
                "SECRET_KEY": "test-secret",
                "BASE_URL": "http://localhost",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_shorten_and_redirect(self):
        response = self.client.post("/api/shorten", json={"long_url": "https://example.com/article?id=1234"})
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["short_key"])

        redirect_response = self.client.get(f"/{payload['short_key']}")
        self.assertEqual(redirect_response.status_code, 302)
        self.assertEqual(redirect_response.headers["Location"], "https://example.com/article?id=1234")

    def test_duplicate_public_url_returns_same_short_key(self):
        first = self.client.post("/api/shorten", json={"long_url": "https://example.com/a"}).get_json()
        second_response = self.client.post("/api/shorten", json={"long_url": "https://example.com/a"})
        second = second_response.get_json()

        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second["short_key"], first["short_key"])

    def test_register_login_list_and_delete(self):
        register = self.client.post(
            "/api/auth/register",
            json={"email": "person@example.com", "password": "password123"},
        )
        self.assertEqual(register.status_code, 201)
        token = register.get_json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = self.client.post("/api/shorten", json={"long_url": "https://example.com/private"}, headers=headers)
        self.assertEqual(created.status_code, 201)
        short_key = created.get_json()["short_key"]

        listed = self.client.get("/api/urls", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["urls"][0]["short_key"], short_key)

        deleted = self.client.delete(f"/api/url/{short_key}", headers=headers)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/url/{short_key}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
