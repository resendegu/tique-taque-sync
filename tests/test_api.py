"""Integration tests for FastAPI endpoints using Starlette TestClient."""

import unittest

from tests import _bootstrap  # noqa: F401  (isolates config/data before imports)

from starlette.testclient import TestClient

from tiquetaque_sync import store
from tiquetaque_sync.config import settings
from tiquetaque_sync.main import app
from tiquetaque_sync.runtime import runtime


class TestFastAPIApp(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")

    def test_dashboard_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("TiqueTaque", resp.text)
        self.assertIn("Horas Trabalhadas Hoje", resp.text)

    def test_settings_html(self):
        resp = self.client.get("/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Quando quero ser avisado", resp.text)

    def test_api_status(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("stage", data)
        self.assertIn("worked_formatted", data)
        self.assertIn("target_seconds", data)

    def test_api_config(self):
        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("timezone", data)
        self.assertIn("work_hours", data)


class TestSettingsAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_get_settings_masks_secrets(self):
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        for key in store.SECRET_KEYS:
            self.assertNotIn(key, body["settings"])
            self.assertIn(f"{key}_is_set", body["settings"])
        self.assertIn("autostart", body)
        self.assertIn("config_file", body)

    def test_update_applies_to_running_engine(self):
        resp = self.client.put(
            "/api/settings",
            json={"lunch_warning_advance_minutes": 7, "work_hours_per_day": 7.5},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["settings"]["lunch_warning_advance_minutes"], 7)

        # Persisted...
        self.assertEqual(store.load()["lunch_warning_advance_minutes"], 7)
        # ...and hot-applied to the shared settings object and rebuilt engine.
        self.assertEqual(settings.lunch_warning_advance_minutes, 7)
        self.assertEqual(settings.work_hours_per_day, 7.5)
        self.assertEqual(runtime.engine.target_seconds, int(7.5 * 3600))

    def test_blank_secret_keeps_stored_value(self):
        self.client.put("/api/settings", json={"telegram_bot_token": "123:ABC"})
        self.client.put("/api/settings", json={"telegram_bot_token": "   "})
        self.assertEqual(store.load()["telegram_bot_token"], "123:ABC")

    def test_credentials_flip_is_configured(self):
        resp = self.client.put(
            "/api/settings",
            json={"tiquetaque_email": "tester@example.com", "tiquetaque_code": "4321"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["is_configured"])

    def test_rejects_out_of_range_values(self):
        resp = self.client.put("/api/settings", json={"poll_interval_seconds": 1})
        self.assertEqual(resp.status_code, 422)

    def test_rejects_unknown_keys(self):
        resp = self.client.put("/api/settings", json={"api_secret_key": "leak"})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
