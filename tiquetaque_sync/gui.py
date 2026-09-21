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

from . import autostart, paths
from .config import reload_settings, settings

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
    kwargs: dict = {"cwd": autostart.launch_workdir()}

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
    def __init__(self) -> None:
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
        self._probe_in_flight = False

        self.root = tk.Tk()
        self.root.title("TiqueTaque Sync")
        self.root.configure(bg=BG)
        self.root.minsize(420, 430)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_widgets()
        self._refresh()
        self._drain_loop()

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

        footer = tk.Label(
            root, text=f"Configuração: {paths.config_file()}", bg=BG, fg="#6b7280",
            font=("Segoe UI", 7), anchor="w", justify="left", wraplength=380,
        )
        footer.pack(fill="x", padx=20, pady=(8, 14))

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
        self.root.after(DRAIN_MS, self._drain_loop)

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

    def _on_close(self) -> None:
        """Fechar a janela não derruba o serviço — ele segue notificando."""
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch() -> int:
    """Abre a janela de controle. Retorna o código de saída do processo."""
    try:
        ControlPanel().run()
    except TkinterUnavailable as exc:
        print(exc)
        return 1
    return 0
