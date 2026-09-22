"""Janela desktop (Tkinter) para quem não quer usar o terminal.

A janela é um painel de controle enxuto: liga e desliga o serviço, mostra o
resumo da jornada, abre o painel completo no navegador e dá acesso direto às
configurações. O overview detalhado continua sendo a página web.

Tkinter faz parte da biblioteca padrão, então não há dependência extra — mas
algumas distribuições Linux empacotam o ``tkinter`` à parte, por isso o import
é checado antes de abrir a janela.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass

from . import autostart, paths, store, tray, updater
from .config import reload_settings, settings
from .singleton import SingleInstance, focus_existing_window

WINDOW_TITLE = "TiqueTaque Sync"

# Itens do menu da bandeja: (identificador, rótulo). Rótulo None = separador.
TRAY_MENU = [
    ("show", "Abrir janela"),
    ("dashboard", "Abrir painel no navegador"),
    ("settings", "Configurações"),
    ("sep", None),
    ("quit", "Sair"),
]

PROBE_MS = 2000      # com que frequência perguntamos ao serviço
DRAIN_MS = 200       # com que frequência a janela consome o resultado
POLL_TIMEOUT = 2.0

STAGE_LABELS = {
    "not_started": "Fora de expediente",
    "working_morning": "Em expediente (manhã)",
    "lunch_break": "Em intervalo de almoço",
    "working_afternoon": "Em expediente (tarde)",
    "completed": "Jornada concluída",
}

BG = "#12141e"
BG_CARD = "#1a1d2d"
FG = "#f3f4f6"
FG_MUTED = "#9ca3af"
ACCENT = "#8b5cf6"
OK = "#10b981"
WARN = "#f59e0b"


class TkinterUnavailable(RuntimeError):
    """Raised when the Python install has no usable Tk bindings."""


@dataclass
class ServiceState:
    running: bool = False
    stage: str | None = None
    worked: str | None = None
    departure: str | None = None
    countdown_label: str | None = None
    configured: bool = False


# ------------------------------------------------------------------------------
# Comunicação com o serviço HTTP
# ------------------------------------------------------------------------------
def _get_json(path: str, timeout: float = POLL_TIMEOUT) -> dict | None:
    try:
        with urllib.request.urlopen(f"{settings.dashboard_url}{path}", timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def probe_service() -> ServiceState:
    """Pergunta ao serviço como está a jornada, sem travar a interface."""
    health = _get_json("/healthz", timeout=1.0)
    if not health:
        return ServiceState(running=False, configured=settings.is_configured)

    state = ServiceState(running=True, configured=settings.is_configured)
    status = _get_json("/api/status")
    if status:
        state.stage = status.get("stage")
        state.worked = status.get("worked_formatted")
        state.departure = status.get("estimated_departure")
        state.countdown_label = status.get("next_alert_label")
    return state


def _spawn_service() -> subprocess.Popen:
    """Sobe `tiquetaque-sync start --no-browser` como processo filho."""
    command = autostart.launch_command(windowless=True)
    # Sem `env` limpo, o filho reusa a pasta _MEI do pai e morre quando o pai sai.
    kwargs: dict = {
        "cwd": autostart.launch_workdir(),
        "env": autostart.child_environment(),
    }

    if sys.platform == "win32":
        # CREATE_NO_WINDOW evita o flash de console preto.
        kwargs["creationflags"] = 0x08000000
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(command, **kwargs)


# ------------------------------------------------------------------------------
# Janela
# ------------------------------------------------------------------------------
class ControlPanel:
    def __init__(self, minimized: bool = False, release_lock=None) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox
        except ImportError as exc:  # pragma: no cover - depende do build do Python
            raise TkinterUnavailable(
                "Tkinter não está disponível nesta instalação do Python. "
                "No Debian/Ubuntu: sudo apt install python3-tk. "
                "Enquanto isso, use 'tiquetaque-sync start'."
            ) from exc

        self.tk = tk
        self.messagebox = messagebox

        self._process: subprocess.Popen | None = None
        self._results: queue.Queue[ServiceState] = queue.Queue()
        self._update_queue: queue.Queue = queue.Queue()
        self._probe_in_flight = False
        self._tray_hint_shown = False
        self._update_release = None      # release disponível, ainda não baixada
        self._update_busy = False
        self._release_lock = release_lock or (lambda: None)

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.configure(bg=BG)
        self.root.minsize(420, 470)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_window_icon()

        # A bandeja vem antes dos widgets porque o checkbox de "iniciar
        # minimizado" só faz sentido quando ela existe. Sem bandeja, uma janela
        # escondida seria um processo invisível — exatamente a queixa que
        # motivou esta mudança.
        self.tray = tray.create(WINDOW_TITLE, paths.app_icon("ico"), TRAY_MENU, "show")
        self.tray_supported = self.tray is not None

        self._build_widgets()

        if minimized and not self.tray_supported:
            minimized = False

        if minimized:
            self.root.withdraw()

        self._refresh()
        self._drain_loop()

        # Abrir o app e o serviço estar parado não faz sentido para o usuário:
        # ele quer ser notificado, não administrar processos.
        self.root.after(400, self._ensure_service_running)
        if settings.auto_check_updates:
            self.root.after(2500, self._check_updates_async)

    def _apply_window_icon(self) -> None:
        """Troca o ícone padrão do Tk pelo relógio do app."""
        ico = paths.app_icon("ico")
        if ico and sys.platform == "win32":
            try:
                self.root.iconbitmap(default=str(ico))
                return
            except Exception:
                pass

        # Tk 8.6 lê PNG; é o caminho para Linux e macOS, e reserva no Windows.
        png = paths.app_icon("png")
        if png:
            try:
                self._icon_image = self.tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._icon_image)
            except Exception:
                pass

    # ------------------------------------------------------------------ layout
    def _build_widgets(self) -> None:
        tk = self.tk
        root = self.root

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=20, pady=(18, 8))
        tk.Label(
            header, text="⏱  TiqueTaque Sync", bg=BG, fg=FG,
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header, text="Monitor de jornada e notificações", bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        # Cartão de estado
        card = tk.Frame(root, bg=BG_CARD, padx=16, pady=14)
        card.pack(fill="x", padx=20, pady=10)

        self.status_var = tk.StringVar(value="Verificando...")
        self.status_label = tk.Label(
            card, textvariable=self.status_var, bg=BG_CARD, fg=FG_MUTED,
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        self.status_label.pack(fill="x")

        self.detail_var = tk.StringVar(value="")
        tk.Label(
            card, textvariable=self.detail_var, bg=BG_CARD, fg=FG_MUTED,
            font=("Segoe UI", 9), anchor="w", justify="left",
        ).pack(fill="x", pady=(6, 0))

        self.worked_var = tk.StringVar(value="--h--min")
        tk.Label(
            card, textvariable=self.worked_var, bg=BG_CARD, fg=FG,
            font=("Consolas", 26, "bold"), anchor="w",
        ).pack(fill="x", pady=(10, 0))
        tk.Label(
            card, text="trabalhadas hoje", bg=BG_CARD, fg=FG_MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x")

        self.departure_var = tk.StringVar(value="")
        tk.Label(
            card, textvariable=self.departure_var, bg=BG_CARD, fg=FG_MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", pady=(8, 0))

        # Botões principais
        actions = tk.Frame(root, bg=BG)
        actions.pack(fill="x", padx=20)

        self.toggle_btn = self._button(actions, "Iniciar serviço", self._on_toggle, primary=True)
        self.toggle_btn.pack(fill="x", pady=4)

        self.dashboard_btn = self._button(actions, "Abrir painel no navegador →", self._open_dashboard)
        self.dashboard_btn.pack(fill="x", pady=4)

        self.settings_btn = self._button(actions, "Configurações (notificações e horários)", self._open_settings)
        self.settings_btn.pack(fill="x", pady=4)

        # Autostart
        options = tk.Frame(root, bg=BG)
        options.pack(fill="x", padx=20, pady=(12, 4))

        self.autostart_var = tk.BooleanVar(value=False)
        self.autostart_check = tk.Checkbutton(
            options,
            text="Iniciar junto com o sistema",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
            bg=BG, fg=FG, selectcolor=BG_CARD, activebackground=BG, activeforeground=FG,
            highlightthickness=0, borderwidth=0, font=("Segoe UI", 9), anchor="w",
        )
        self.autostart_check.pack(fill="x")

        self.autostart_detail = tk.StringVar(value="")
        tk.Label(
            options, textvariable=self.autostart_detail, bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=380,
        ).pack(fill="x")

        self.minimized_var = tk.BooleanVar(value=settings.start_minimized)
        self.minimized_check = tk.Checkbutton(
            options,
            text="Iniciar minimizado na bandeja",
            variable=self.minimized_var,
            command=self._on_minimized_toggle,
            bg=BG, fg=FG, selectcolor=BG_CARD, activebackground=BG, activeforeground=FG,
            highlightthickness=0, borderwidth=0, font=("Segoe UI", 9), anchor="w",
        )
        self.minimized_check.pack(fill="x", pady=(6, 0))

        self.minimized_detail = tk.StringVar(
            value="Ao iniciar com o sistema, abre só o ícone da bandeja."
            if self.tray_supported
            else "Bandeja indisponível nesta plataforma — a janela sempre abre."
        )
        tk.Label(
            options, textvariable=self.minimized_detail, bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=380,
        ).pack(fill="x")

        if not self.tray_supported:
            self.minimized_check.configure(state="disabled")

        # Faixa de atualização: fica escondida até haver o que anunciar, para
        # não ocupar espaço no uso normal.
        self.update_frame = tk.Frame(root, bg=BG_CARD, padx=14, pady=10)
        self.update_var = tk.StringVar(value="")
        tk.Label(
            self.update_frame, textvariable=self.update_var, bg=BG_CARD, fg=FG,
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left", wraplength=360,
        ).pack(fill="x")
        self.update_btn = self._button(self.update_frame, "Baixar e instalar", self._on_update_click)
        self.update_btn.pack(fill="x", pady=(8, 0))

        self.footer = tk.Label(
            root, text=f"Configuração: {paths.config_file()}", bg=BG, fg="#6b7280",
            font=("Segoe UI", 7), anchor="w", justify="left", wraplength=380,
        )
        self.footer.pack(fill="x", padx=20, pady=(8, 14))

        self._sync_autostart_widget()

    def _button(self, parent, text: str, command, primary: bool = False):
        return self.tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else BG_CARD,
            fg="white" if primary else FG,
            activebackground="#7c3aed" if primary else "#232739",
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            pady=9,
            cursor="hand2",
        )

    # ----------------------------------------------------------------- estado
    def _refresh(self) -> None:
        """Dispara a sondagem em thread e agenda o próximo ciclo."""
        if not self._probe_in_flight:
            self._probe_in_flight = True
            threading.Thread(target=self._probe_worker, daemon=True).start()

        self.root.after(PROBE_MS, self._refresh)

    def _drain_loop(self) -> None:
        """Consome o resultado assim que ele chega, sem esperar o próximo probe."""
        self._drain_results()
        self._drain_tray_events()
        self._drain_update_events()
        self.root.after(DRAIN_MS, self._drain_loop)

    def _drain_update_events(self) -> None:
        """Resultados da thread de atualização, aplicados no laço do Tk."""
        while True:
            try:
                event = self._update_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, tuple) and event[0] == "downloaded":
                self._on_download_finished(event[1])
            else:
                self._on_update_available(event)

    def _drain_tray_events(self) -> None:
        """Executa, no laço do Tk, os comandos vindos da thread da bandeja."""
        if self.tray is None:
            return
        while True:
            try:
                command = self.tray.events.get_nowait()
            except queue.Empty:
                break
            self._handle_tray_command(command)

    def _handle_tray_command(self, command: str) -> None:
        if command == "show":
            self._show_window()
        elif command == "dashboard":
            self._open_dashboard()
        elif command == "settings":
            self._open_settings()
        elif command == "quit":
            self._quit()

    def _probe_worker(self) -> None:
        try:
            self._results.put(probe_service())
        finally:
            self._probe_in_flight = False

    def _drain_results(self) -> None:
        state = None
        while True:
            try:
                state = self._results.get_nowait()
            except queue.Empty:
                break
        if state is not None:
            self._render(state)

    def _render(self, state: ServiceState) -> None:
        if state.running:
            self.status_var.set("● Serviço ativo")
            self.status_label.configure(fg=OK)
            self.toggle_btn.configure(text="Parar serviço")
            stage = STAGE_LABELS.get(state.stage or "", "Sincronizando...")
            self.detail_var.set(
                stage if state.configured else f"{stage} — credenciais pendentes, abra Configurações"
            )
            self.worked_var.set(state.worked or "--h--min")
            self.departure_var.set(
                f"Previsão de saída: {state.departure}" if state.departure else
                (state.countdown_label or "")
            )
        else:
            self.status_var.set("○ Serviço parado")
            self.status_label.configure(fg=WARN)
            self.toggle_btn.configure(text="Iniciar serviço")
            self.detail_var.set(
                "Clique em iniciar para começar a monitorar."
                if state.configured
                else "Inicie o serviço e abra Configurações para informar suas credenciais."
            )
            self.worked_var.set("--h--min")
            self.departure_var.set("")

        for button in (self.dashboard_btn, self.settings_btn):
            button.configure(state="normal" if state.running else "disabled")

    def _sync_autostart_widget(self) -> None:
        state = autostart.status()
        self.autostart_var.set(state.enabled)
        if not state.supported:
            self.autostart_check.configure(state="disabled")
            self.autostart_detail.set(state.detail or "Não suportado nesta plataforma.")
        else:
            self.autostart_detail.set(state.mechanism)

    # ------------------------------------------------------------------ ações
    def _on_toggle(self) -> None:
        if probe_service().running:
            self._stop_service()
        else:
            self._start_service()

    def _start_service(self) -> None:
        paths.ensure_config_dir()
        reload_settings()
        try:
            self._process = _spawn_service()
        except OSError as exc:
            self.messagebox.showerror("TiqueTaque Sync", f"Não foi possível iniciar o serviço:\n{exc}")
            return
        self.status_var.set("● Iniciando...")
        self.status_label.configure(fg=FG_MUTED)

    def _stop_service(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            self.status_var.set("○ Serviço parado")
            return

        # Instância iniciada por fora (autostart, terminal): não é nossa para matar.
        self.messagebox.showinfo(
            "TiqueTaque Sync",
            "O serviço está rodando em outro processo (autostart ou terminal).\n\n"
            "Encerre-o por onde foi iniciado, ou desative 'Iniciar junto com o sistema' "
            "e reinicie a máquina.",
        )

    def _open_dashboard(self) -> None:
        webbrowser.open(settings.dashboard_url)

    def _open_settings(self) -> None:
        webbrowser.open(f"{settings.dashboard_url}/settings")

    def _on_autostart_toggle(self) -> None:
        desired = self.autostart_var.get()
        try:
            state = autostart.set_enabled(desired)
        except (autostart.AutostartError, OSError) as exc:
            self.autostart_var.set(not desired)
            self.messagebox.showerror("TiqueTaque Sync", f"Falha ao alterar o autostart:\n{exc}")
            return
        self.autostart_detail.set(state.location or state.mechanism)

    # --------------------------------------------------------------- updates
    def _check_updates_async(self) -> None:
        """Consulta o GitHub em thread — a rede nunca bloqueia o laço do Tk."""
        pending = updater.pending_version()
        if pending:
            self._show_update_banner(
                f"Versão {pending} baixada e pronta.",
                "Atualizar",
            )
            return

        def worker() -> None:
            release = updater.check_for_update()
            if release is not None:
                self._update_queue.put(release)

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_banner(self, message: str, button_label: str) -> None:
        self.update_var.set(message)
        self.update_btn.configure(text=button_label, state="normal")
        self.update_frame.pack(fill="x", padx=20, pady=(4, 0), before=self.footer)

    def _on_update_available(self, release) -> None:
        self._update_release = release
        if not autostart.is_frozen():
            # Instalação via pip não troca binário: mostra o comando e pronto.
            self._show_update_banner(
                f"Versão {release.version} disponível. Atualize com:\n{updater.update_hint()}",
                "Abrir a página da release",
            )
            return

        self._show_update_banner(f"Versão {release.version} disponível.", "Baixar e instalar")
        if self.tray is not None:
            self.tray.notify(
                "Atualização disponível",
                f"TiqueTaque Sync {release.version} pode ser instalado.",
            )

    def _on_update_click(self) -> None:
        if self._update_busy:
            return

        # Já baixado: o clique é para reiniciar e aplicar.
        if updater.pending_version():
            self._restart_to_update()
            return

        release = self._update_release
        if release is None:
            return

        if not autostart.is_frozen():
            webbrowser.open(release.page_url)
            return

        self._update_busy = True
        self.update_btn.configure(state="disabled", text="Baixando...")

        def worker() -> None:
            path = updater.download(release)
            self._update_queue.put(("downloaded", path))

        threading.Thread(target=worker, daemon=True).start()

    def _on_download_finished(self, path) -> None:
        self._update_busy = False
        if path is None:
            self.update_var.set(
                "Falha ao baixar ou verificar a atualização. Nada foi alterado."
            )
            self.update_btn.configure(state="normal", text="Tentar de novo")
            return
        self._show_update_banner(
            f"Versão {updater.pending_version()} pronta para instalar.",
            "Reiniciar e atualizar",
        )

    def _restart_to_update(self) -> None:
        """Aplica a troca e reabre o app.

        A ordem importa: encerra o serviço e solta a trava **antes** de lançar o
        novo processo, senão ele esbarraria na instância única e sairia sem abrir.
        """
        if not updater.apply_pending_update(relaunch=False):
            self.messagebox.showerror(
                "TiqueTaque Sync",
                "Não foi possível aplicar a atualização.\n"
                "Verifique o log do app e tente novamente.",
            )
            return

        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

        if self.tray is not None:
            self.tray.stop()
        self._release_lock()

        try:
            kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            kwargs["env"] = autostart.child_environment()
            subprocess.Popen([sys.executable, "gui"], **kwargs)
        except OSError as exc:
            self.messagebox.showerror(
                "TiqueTaque Sync", f"Atualizado, mas não consegui reabrir:\n{exc}"
            )
        self.root.destroy()

    def _on_minimized_toggle(self) -> None:
        try:
            store.save({"start_minimized": self.minimized_var.get()})
            reload_settings()
        except OSError as exc:
            self.minimized_var.set(not self.minimized_var.get())
            self.messagebox.showerror("TiqueTaque Sync", f"Não foi possível salvar:\n{exc}")

    def _ensure_service_running(self) -> None:
        """Sobe o serviço se ele não estiver no ar — sem duplicar o que já roda."""
        if probe_service().running:
            return
        self._start_service()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self) -> None:
        """Encerra a janela e, se o serviço for filho desta janela, o serviço."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def _on_close(self) -> None:
        """O X esconde na bandeja; sair mesmo é pelo menu da bandeja.

        Sem bandeja (Linux/macOS), mantém o comportamento antigo: fecha a janela
        e o serviço segue rodando em background.
        """
        if self.tray is None:
            self.root.destroy()
            return

        self.root.withdraw()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.notify(
                "TiqueTaque Sync continua rodando",
                "A janela foi minimizada para a bandeja. Clique no ícone para reabrir.",
            )

    def run(self) -> None:
        self.root.mainloop()


def launch(minimized: bool = False) -> int:
    """Abre a janela de controle. Retorna o código de saída do processo.

    Uma segunda janela não é aberta: a trava de instância única faz a janela já
    existente vir para frente, em vez de duplicar o painel.
    """
    lock = SingleInstance("gui")
    if not lock.acquire():
        if not focus_existing_window(WINDOW_TITLE):
            print("O TiqueTaque Sync já está aberto (veja a bandeja do sistema).")
        return 0

    try:
        ControlPanel(minimized=minimized, release_lock=lock.release).run()
    except TkinterUnavailable as exc:
        print(exc)
        return 1
    finally:
        lock.release()
    return 0
