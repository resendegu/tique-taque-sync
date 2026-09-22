"""Unit tests for the installable-app layer: paths, config store and autostart."""

import importlib
import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests import _bootstrap  # noqa: F401  (isolates config/data before imports)

from tiquetaque_sync import __version__, autostart, paths, shortcut, store, updater
from tiquetaque_sync.singleton import SingleInstance
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


class TestTrayIsImportableEverywhere(unittest.TestCase):
    """`gui.py` importa `tray` incondicionalmente, e a suíte roda no Linux no CI.

    O módulo usa `ctypes.WINFUNCTYPE` e `ctypes.wintypes`, que não existem fora
    do Windows: se algo Win32 voltar para o nível do módulo, o import quebra em
    qualquer plataforma que não seja Windows — foi assim que o CI caiu uma vez.
    """

    def _reload_as(self, platform: str):
        with mock.patch.object(sys, "platform", platform):
            return importlib.reload(importlib.import_module("tiquetaque_sync.tray"))

    def test_module_imports_and_degrades_outside_windows(self):
        try:
            for platform in ("linux", "darwin"):
                with self.subTest(platform=platform):
                    module = self._reload_as(platform)
                    self.assertFalse(module.IS_WINDOWS)
                    self.assertIsNone(module.create("t", None, [], "show"))
        finally:
            # Devolve o módulo ao estado desta plataforma para os demais testes.
            importlib.reload(importlib.import_module("tiquetaque_sync.tray"))


class TestSingleInstance(unittest.TestCase):
    """Duas instâncias do serviço duplicariam notificações e disputariam o SQLite."""

    def test_second_acquire_is_refused(self):
        first = SingleInstance("test-lock")
        second = SingleInstance("test-lock")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.release()
            second.release()

    def test_lock_is_reusable_after_release(self):
        lock = SingleInstance("test-lock-reuse")
        self.assertTrue(lock.acquire())
        lock.release()

        again = SingleInstance("test-lock-reuse")
        try:
            self.assertTrue(again.acquire())
        finally:
            again.release()


class TestAutostartOpensWindow(unittest.TestCase):
    """O login abre a janela; o serviço cru era invisível para o usuário."""

    def test_autostart_command_launches_the_window(self):
        command = autostart.autostart_command()
        self.assertIn("gui", command)
        self.assertIn("--autostart", command)
        self.assertNotIn("start", command)

    def test_service_command_still_starts_the_service(self):
        command = autostart.launch_command(windowless=True)
        self.assertIn("start", command)
        self.assertIn("--no-browser", command)


class TestAppIcon(unittest.TestCase):
    def test_icon_is_packaged(self):
        for extension in ("ico", "png"):
            with self.subTest(extension=extension):
                icon = paths.app_icon(extension)
                self.assertIsNotNone(icon, f"icon.{extension} não foi empacotado")
                self.assertGreater(icon.stat().st_size, 0)

    def test_every_tray_command_is_handled(self):
        from tiquetaque_sync.gui import TRAY_MENU

        handled = {"show", "dashboard", "settings", "quit"}
        for name, label in TRAY_MENU:
            if label is None:
                continue
            with self.subTest(command=name):
                self.assertIn(name, handled)


class TestVersionIsConsistent(unittest.TestCase):
    """O updater compara a versão do pacote com a da release.

    Se `__version__` ficar para trás do pyproject.toml, o app se acharia
    desatualizado para sempre e rebaixaria a mesma versão em loop.
    """

    def test_package_version_matches_pyproject(self):
        import tomllib

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(
            __version__,
            declared,
            "tiquetaque_sync.__version__ divergiu de [project].version",
        )


class TestUpdaterVersions(unittest.TestCase):
    def test_comparison(self):
        casos = [
            ("2.2.0", "2.1.0", True),
            ("2.1.1", "2.1.0", True),
            ("3.0.0", "2.9.9", True),
            ("2.1.0", "2.1.0", False),
            ("2.0.9", "2.1.0", False),
            ("v2.2.0", "2.1.0", True),      # a tag do git traz o "v"
            ("2.2.0-rc.1", "2.1.0", True),  # pré-lançamento ainda é mais novo
            ("não-é-versão", "2.1.0", False),
        ]
        for candidato, atual, esperado in casos:
            with self.subTest(candidato=candidato):
                self.assertEqual(updater.is_newer(candidato, atual), esperado)


class TestUpdaterSafety(unittest.TestCase):
    """Baixar e executar binário exige checagem; sem ela, nada é instalado."""

    def setUp(self):
        updater.discard_pending()
        self.release = updater.Release(
            version="9.9.9",
            tag="v9.9.9",
            download_url="https://github.com/x/y/releases/download/v9.9.9/TiqueTaqueSync.exe",
            checksum_url="https://github.com/x/y/releases/download/v9.9.9/TiqueTaqueSync.exe.sha256",
            page_url="https://github.com/x/y/releases/tag/v9.9.9",
        )

    def tearDown(self):
        updater.discard_pending()

    def test_download_refuses_when_checksum_is_missing(self):
        with mock.patch.object(updater, "_expected_checksum", return_value=None):
            self.assertIsNone(updater.download(self.release))
        self.assertIsNone(updater.pending_version())

    def test_download_refuses_when_checksum_does_not_match(self):
        payload = b"binario-adulterado"

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def read(self, size=-1):
                data, self._data = self._data, b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(updater, "_expected_checksum", return_value="0" * 64), \
             mock.patch.object(updater, "_open", return_value=FakeResponse(payload)):
            self.assertIsNone(updater.download(self.release))

        self.assertIsNone(updater.pending_version())
        self.assertFalse((updater._updates_dir() / "download.part").exists())

    def test_release_without_executable_is_not_downloadable(self):
        release = updater.Release(
            version="9.9.9", tag="v9.9.9",
            download_url=None, checksum_url=None,
            page_url="https://example.invalid",
        )
        self.assertFalse(release.has_executable)
        self.assertIsNone(updater.download(release))

    def test_repository_is_pinned_to_https(self):
        # O updater executa o que baixa: a origem não pode ser configurável.
        self.assertTrue(updater.RELEASES_API.startswith("https://api.github.com/"))
        self.assertIn("resendegu/tique-taque-sync", updater.RELEASES_API)

    def test_non_https_is_refused(self):
        with self.assertRaises(ValueError):
            updater._open("http://exemplo.invalido/payload.exe")

    def test_apply_is_a_no_op_outside_the_frozen_app(self):
        # Instalação via pip não troca binário nenhum.
        self.assertFalse(updater.apply_pending_update())


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
            ["gui", "--minimized"],
            ["gui", "--autostart"],
            ["update"],
            ["update", "--check"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(hasattr(parser.parse_args(argv), "func"))

    def test_start_accepts_host_and_port(self):
        args = build_parser().parse_args(["start", "--host", "0.0.0.0", "--port", "9999"])
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 9999)


if __name__ == "__main__":
    unittest.main()
