"""Command line interface — the way the app is meant to be used on a laptop.

    tiquetaque-sync setup              # wizard: credentials, channels, moments
    tiquetaque-sync                    # start the service and open the dashboard
    tiquetaque-sync gui                # desktop window, no terminal needed
    tiquetaque-sync autostart enable   # launch together with the operating system
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from . import autostart, paths, shortcut, store
from .config import reload_settings, settings

APP_VERSION = "2.0.0"


# ------------------------------------------------------------------------------
# Small console helpers
# ------------------------------------------------------------------------------
def _use_utf8_console() -> None:
    """Best-effort UTF-8 console so the emojis below never crash on Windows cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _say(message: str = "") -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        # Legacy code page that cannot represent the emoji: drop what it cannot show.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(message.encode(encoding, errors="ignore").decode(encoding, errors="ignore"))


def _ask(label: str, current: str | None = None, secret: bool = False) -> str | None:
    """Prompt for a value, returning None when the user just presses Enter."""
    if secret and current:
        shown = "•" * 8
    else:
        shown = current or ""
    suffix = f" [{shown}]" if shown else ""
    answer = input(f"{label}{suffix}: ").strip()
    return answer or None


def _ask_bool(label: str, current: bool) -> bool:
    default = "S/n" if current else "s/N"
    answer = input(f"{label} [{default}]: ").strip().lower()
    if not answer:
        return current
    return answer in ("s", "sim", "y", "yes", "1", "true")


def _ask_int(label: str, current: int) -> int:
    while True:
        answer = input(f"{label} [{current}]: ").strip()
        if not answer:
            return current
        try:
            return int(answer)
        except ValueError:
            _say("  Informe um número inteiro.")


# ------------------------------------------------------------------------------
# Server lifecycle
# ------------------------------------------------------------------------------
def _is_port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def _instance_already_running(host: str, port: int) -> bool:
    """True when a TiqueTaque Sync instance already answers on this port."""
    if not _is_port_open(host, port):
        return False
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read()).get("status") == "ok"
    except (urllib.error.URLError, ValueError, OSError):
        return False


def _open_browser_when_ready(url: str, host: str, port: int, attempts: int = 60) -> None:
    for _ in range(attempts):
        if _is_port_open(host, port):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def cmd_start(args: argparse.Namespace) -> int:
    import uvicorn

    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"

    if _instance_already_running(host, port):
        _say(f"TiqueTaque Sync já está rodando em {url} — abrindo o painel.")
        webbrowser.open(url)
        return 0

    paths.ensure_config_dir()
    if not settings.is_configured:
        _say("⚠️  Credenciais do TiqueTaque ainda não configuradas.")
        _say(f"    Rode 'tiquetaque-sync setup' ou ajuste em {url}/settings\n")

    should_open = not args.no_browser and (args.open_browser or settings.open_browser_on_start)
    if should_open:
        threading.Thread(
            target=_open_browser_when_ready, args=(url, host, port), daemon=True
        ).start()

    _say(f"⏱️  TiqueTaque Sync em {url}  (Ctrl+C para encerrar)")
    try:
        uvicorn.run(
            "tiquetaque_sync.main:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=args.log_level,
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        _say("\nEncerrado.")
    except OSError as exc:
        _say(f"Não foi possível abrir a porta {port}: {exc}")
        return 1
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    url = settings.dashboard_url
    if not _instance_already_running(settings.host, settings.port):
        _say(f"Nenhuma instância respondendo em {url}. Rode 'tiquetaque-sync start' antes.")
        return 1
    webbrowser.open(url + ("/settings" if args.settings else ""))
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Open the small desktop control panel (Tkinter)."""
    from .gui import launch

    return launch()


def cmd_shortcut(args: argparse.Namespace) -> int:
    try:
        if args.action == "remove":
            removed = shortcut.remove()
            _say("Atalho removido." if removed else "Nenhum atalho encontrado.")
            for path in removed:
                _say(f"  {path}")
            return 0

        created = shortcut.create()
        _say("Atalho criado:")
        for path in created:
            _say(f"  {path}")
        return 0
    except (shortcut.ShortcutError, OSError) as exc:
        _say(f"Erro: {exc}")
        return 1


# ------------------------------------------------------------------------------
# Setup wizard
# ------------------------------------------------------------------------------
def cmd_setup(args: argparse.Namespace) -> int:
    # O .exe é compilado sem console: não há stdin para o assistente interativo.
    if sys.stdin is None or sys.stdin.closed:
        _say("O assistente interativo precisa de um terminal. Use a tela de configurações.")
        return 1

    _say("⏱️  Configuração do TiqueTaque Sync")
    _say(f"    Arquivo: {paths.config_file()}")
    _say("    Enter mantém o valor atual.\n")

    values: dict = {}

    _say("── Conta TiqueTaque ───────────────────────────────")
    email = _ask("E-mail", settings.tiquetaque_email)
    if email:
        values["tiquetaque_email"] = email
    code = _ask("Código de verificação (4 dígitos)", settings.tiquetaque_code, secret=True)
    if code:
        values["tiquetaque_code"] = code

    _say("\n── Jornada ────────────────────────────────────────")
    values["work_hours_per_day"] = float(
        _ask_int("Horas por dia", int(settings.work_hours_per_day))
    )
    values["lunch_duration_minutes"] = _ask_int(
        "Duração do almoço (minutos)", settings.lunch_duration_minutes
    )

    _say("\n── Momentos das notificações ──────────────────────")
    values["lunch_warning_advance_minutes"] = _ask_int(
        "Aviso antes do fim do almoço (minutos)", settings.lunch_warning_advance_minutes
    )
    values["end_work_warning_advance_minutes"] = _ask_int(
        "Aviso antes do fim da jornada (minutos)", settings.end_work_warning_advance_minutes
    )
    values["continuous_work_warning_advance_minutes"] = _ask_int(
        "Aviso antes do limite de 6h contínuas (minutos)",
        settings.continuous_work_warning_advance_minutes,
    )

    _say("\n── Telegram ───────────────────────────────────────")
    values["telegram_enabled"] = _ask_bool("Habilitar Telegram?", settings.telegram_enabled)
    if values["telegram_enabled"]:
        token = _ask("Bot token (@BotFather)", settings.telegram_bot_token, secret=True)
        if token:
            values["telegram_bot_token"] = token
        chat_id = _ask("Chat ID", settings.telegram_chat_id)
        if chat_id:
            values["telegram_chat_id"] = chat_id

    _say("\n── Slack ──────────────────────────────────────────")
    values["slack_enabled"] = _ask_bool("Habilitar Slack?", settings.slack_enabled)
    if values["slack_enabled"]:
        webhook = _ask("Incoming Webhook URL", settings.slack_webhook_url, secret=True)
        if webhook:
            values["slack_webhook_url"] = webhook

    _say("\n── Aplicativo ─────────────────────────────────────")
    values["port"] = _ask_int("Porta do painel", settings.port)
    values["open_browser_on_start"] = _ask_bool(
        "Abrir o painel automaticamente ao iniciar?", settings.open_browser_on_start
    )

    store.save(values)
    reload_settings()
    _say(f"\n✅ Configuração salva em {paths.config_file()}")

    if _ask_bool("Iniciar junto com o sistema?", autostart.status().enabled):
        try:
            state = autostart.enable()
            _say(f"✅ Autostart ativado ({state.mechanism}).")
        except (autostart.AutostartError, OSError) as exc:
            _say(f"⚠️  Não foi possível ativar o autostart: {exc}")
    else:
        try:
            autostart.disable()
        except (autostart.AutostartError, OSError):
            pass

    _say("\nPronto! Rode 'tiquetaque-sync' para abrir o painel.")
    return 0


# ------------------------------------------------------------------------------
# Autostart / config / test
# ------------------------------------------------------------------------------
def cmd_autostart(args: argparse.Namespace) -> int:
    try:
        if args.action == "enable":
            state = autostart.enable()
        elif args.action == "disable":
            state = autostart.disable()
        else:
            state = autostart.status()
    except (autostart.AutostartError, OSError) as exc:
        _say(f"Erro: {exc}")
        return 1

    _say(f"Autostart: {'ativado' if state.enabled else 'desativado'}")
    _say(f"Mecanismo: {state.mechanism}")
    if state.location:
        _say(f"Arquivo:   {state.location}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.action == "path":
        _say(str(paths.config_file()))
        return 0

    if args.action == "show":
        stored = store.load()
        _say(f"# {paths.config_file()}")
        _say(json.dumps(store.mask(stored), indent=2, ensure_ascii=False, sort_keys=True))
        _say(f"\n# Dados (SQLite): {settings.data_dir}")
        return 0

    if args.action == "edit":
        paths.ensure_config_dir()
        config_file = paths.config_file()
        if not config_file.exists():
            store.save({})
        webbrowser.open(config_file.as_uri())
        _say(f"Abrindo {config_file}")
        return 0

    return 1


def cmd_test(args: argparse.Namespace) -> int:
    from .notifiers.dispatcher import create_dispatcher_from_settings

    dispatcher = create_dispatcher_from_settings(settings)
    title = "Teste de Notificação — TiqueTaque Sync"
    message = "Canal configurado corretamente. Você receberá os avisos de jornada por aqui! 🚀"

    async def run() -> dict[str, bool]:
        if args.channel == "all":
            return await dispatcher.dispatch(title, message, level="success")
        notifier = next((n for n in dispatcher.notifiers if n.name == args.channel), None)
        if not notifier or not notifier.is_enabled:
            return {args.channel: False}
        return {args.channel: await notifier.send_message(title, message, level="success")}

    results = asyncio.run(run())
    failed = [name for name, ok in results.items() if not ok]
    for name, ok in results.items():
        _say(f"{'✅' if ok else '❌'} {name}")
    if failed:
        _say("\nVerifique as credenciais em 'tiquetaque-sync setup'.")
        return 1
    return 0


# ------------------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiquetaque-sync",
        description="Monitor de jornada TiqueTaque com painel web e notificações.",
    )
    parser.add_argument("--version", action="version", version=f"TiqueTaque Sync {APP_VERSION}")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="Inicia o serviço e abre o painel")
    start.add_argument("--host", default=None, help="Interface de escuta (padrão: 127.0.0.1)")
    start.add_argument("--port", type=int, default=None, help="Porta do painel (padrão: 8000)")
    start.add_argument("--no-browser", action="store_true", help="Não abrir o navegador")
    start.add_argument("--open-browser", action="store_true", help="Forçar abertura do navegador")
    start.add_argument("--reload", action="store_true", help="Auto-reload (desenvolvimento)")
    start.add_argument("--log-level", default="info", help="Nível de log do uvicorn")
    start.set_defaults(func=cmd_start)

    open_cmd = sub.add_parser("open", help="Abre o painel de uma instância já rodando")
    open_cmd.add_argument("--settings", action="store_true", help="Abrir direto as configurações")
    open_cmd.set_defaults(func=cmd_open)

    setup = sub.add_parser("setup", help="Assistente de configuração interativo")
    setup.set_defaults(func=cmd_setup)

    gui = sub.add_parser("gui", help="Abre a janela do app (sem terminal)")
    gui.set_defaults(func=cmd_gui)

    sc = sub.add_parser("shortcut", help="Cria ou remove o atalho da janela do app")
    sc.add_argument("action", choices=["create", "remove"], nargs="?", default="create")
    sc.set_defaults(func=cmd_shortcut)

    auto = sub.add_parser("autostart", help="Iniciar junto com o sistema")
    auto.add_argument("action", choices=["enable", "disable", "status"], nargs="?", default="status")
    auto.set_defaults(func=cmd_autostart)

    config = sub.add_parser("config", help="Inspecionar a configuração salva")
    config.add_argument("action", choices=["show", "path", "edit"], nargs="?", default="show")
    config.set_defaults(func=cmd_config)

    test = sub.add_parser("test", help="Envia uma notificação de teste")
    test.add_argument("--channel", choices=["telegram", "slack", "all"], default="all")
    test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    _use_utf8_console()
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    # Bare `tiquetaque-sync` behaves like `tiquetaque-sync start`.
    if not argv:
        argv = ["start"]

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


def main_gui(argv: list[str] | None = None) -> int:
    """Entry point of the windowed launcher: no arguments means 'open the window'."""
    return main(list(sys.argv[1:] if argv is None else argv) or ["gui"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
