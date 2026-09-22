"""Unit tests for the installable-app layer: paths, config store and autostart."""

import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests import _bootstrap  # noqa: F401  (isolates config/data before imports)

from tiquetaque_sync import autostart, paths, shortcut, store
from tiquetaque_sync.cli import build_parser
from tiquetaque_sync.config import Settings


class TestPaths(unittest.TestCase):
    def test_home_override_wins(self):
        self.assertEqual(paths.config_dir(), _bootstrap.SANDBOX)
        self.assertEqual(paths.data_dir(), _bootstrap.SANDBOX / "data")
        self.assertEqual(paths.config_file().name, "config.json")


class TestStore(unittest.TestCase):
    def setUp(self):
        if paths.config_file().exists():
            paths.config_file().unlink()

    def test_save_merges_and_filters_unknown_keys(self):
        store.save({"port": 8123, "api_secret_key": "should-not-persist"})
        store.save({"timezone": "UTC"})

        stored = json.loads(paths.config_file().read_text(encoding="utf-8"))
        self.assertEqual(stored["port"], 8123)
        self.assertEqual(stored["timezone"], "UTC")
        self.assertNotIn("api_secret_key", stored)

    def test_corrupted_file_is_ignored(self):
        paths.config_file().write_text("{not json", encoding="utf-8")
        self.assertEqual(store.load(), {})

    def test_mask_hides_secrets(self):
        masked = store.mask({"telegram_bot_token": "123:ABC", "port": 8000})
        self.assertNotIn("telegram_bot_token", masked)
        self.assertTrue(masked["telegram_bot_token_is_set"])
        self.assertFalse(masked["slack_webhook_url_is_set"])
        self.assertEqual(masked["port"], 8000)


class TestSettingsSources(unittest.TestCase):
    def setUp(self):
        if paths.config_file().exists():
            paths.config_file().unlink()

    def tearDown(self):
        os.environ.pop("LUNCH_DURATION_MINUTES", None)

    def test_config_file_overrides_defaults(self):
        store.save({"lunch_duration_minutes": 45})
        self.assertEqual(Settings().lunch_duration_minutes, 45)

    def test_environment_overrides_config_file(self):
        store.save({"lunch_duration_minutes": 45})
        os.environ["LUNCH_DURATION_MINUTES"] = "30"
        self.assertEqual(Settings().lunch_duration_minutes, 30)

    def test_is_configured_requires_credentials(self):
        self.assertFalse(Settings().is_configured)
        store.save({"tiquetaque_email": "a@b.com", "tiquetaque_code": "1234"})
        self.assertTrue(Settings().is_configured)

    def test_dashboard_url_uses_loopback(self):
        self.assertEqual(Settings(host="0.0.0.0", port=9000).dashboard_url, "http://127.0.0.1:9000")


class TestAutostart(unittest.TestCase):
    def test_status_reports_a_mechanism(self):
        state = autostart.status()
        self.assertIsInstance(state.enabled, bool)
        self.assertTrue(state.mechanism)
        self.assertIn("mechanism", state.to_dict())

    def test_launch_command_is_resolvable(self):
        command = autostart._launch_command()
        self.assertIn("start", command)
        self.assertIn("--no-browser", command)
        self.assertTrue(Path(command[0]).name)


class TestShortcut(unittest.TestCase):
    def test_gui_command_is_resolvable(self):
        command = shortcut._gui_command()
        self.assertTrue(Path(command[0]).name)
        # Either the installed gui script, or the `python -m ... gui` fallback.
        self.assertTrue(command[-1].endswith("gui") or "tiquetaque-sync-gui" in command[0])

    def test_targets_are_user_scoped(self):
        home = str(Path.home()).lower()
        for target in shortcut.targets():
            with self.subTest(target=target):
                self.assertTrue(
                    str(target).lower().startswith(home)
                    or str(target).lower().startswith(str(Path(os.environ.get("APPDATA", home))).lower())
                )


class TestFrozenMode(unittest.TestCase):
    """Dentro do .exe não há interpretador Python para re-invocar."""

    def test_service_command_reinvokes_the_executable(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            command = autostart._launch_command(windowless=True)
            self.assertEqual(command, [sys.executable, "start", "--no-browser"])
            # Nada de cwd: o pacote viaja dentro do próprio executável.
            self.assertIsNone(autostart._launch_workdir())

    def test_shortcut_points_at_the_executable(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(shortcut._gui_command(), [sys.executable])


class TestGUI(unittest.TestCase):
    def test_probe_reports_stopped_when_nothing_listens(self):
        from tiquetaque_sync.config import settings
        from tiquetaque_sync.gui import probe_service

        # A porta padrão não serve para este teste: quem desenvolve costuma ter
        # o próprio app rodando nela, e o teste falharia por isso. Pegamos uma
        # porta efêmera livre e a liberamos antes de sondar.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free_port = probe.getsockname()[1]

        with mock.patch.object(settings, "port", free_port):
            self.assertFalse(probe_service().running)

    def test_stage_labels_cover_every_engine_stage(self):
        from tiquetaque_sync.engine.workday import WorkdayStage
        from tiquetaque_sync.gui import STAGE_LABELS

        for stage in WorkdayStage:
            if stage is WorkdayStage.CUSTOM:
                continue
            with self.subTest(stage=stage):
                self.assertIn(stage.value, STAGE_LABELS)


class TestCLIParser(unittest.TestCase):
    def test_subcommands_are_registered(self):
        parser = build_parser()
        for argv in (
            ["start", "--no-browser"],
            ["setup"],
            ["autostart", "enable"],
            ["config", "path"],
            ["test", "--channel", "slack"],
            ["open", "--settings"],
            ["gui"],
            ["shortcut", "create"],
            ["shortcut", "remove"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(hasattr(parser.parse_args(argv), "func"))

    def test_start_accepts_host_and_port(self):
        args = build_parser().parse_args(["start", "--host", "0.0.0.0", "--port", "9999"])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9999)


if __name__ == "__main__":
    unittest.main()
