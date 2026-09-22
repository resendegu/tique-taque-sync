"""Ícone na bandeja do Windows, via Win32 puro (``ctypes``).

Sem pystray nem Pillow de propósito: o AGENTS.md exige biblioteca padrão, e
cada dependência a mais engorda o executável e aumenta a superfície de
falso-positivo de antivírus — problema que este projeto já enfrentou.

Fora do Windows, :func:`create` devolve ``None`` e a janela simplesmente se
comporta como antes (fechar encerra). Nenhum outro módulo precisa saber disso.

Desenho da integração: o Win32 exige que a janela e o laço de mensagens vivam
na mesma thread, então a bandeja roda numa thread própria. Ela **não toca** em
widget Tk — apenas empurra o nome do comando numa ``queue.Queue`` que o laço do
Tk consome. Tkinter não é thread-safe; essa fila é a fronteira.
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

HWND_MESSAGE = -3
NIIF_NONE = 0x00


# Abaixo, só no Windows: `ctypes.WINFUNCTYPE` e `ctypes.wintypes` não existem
# em Linux/macOS, e o módulo precisa ser *importável* em qualquer plataforma —
# o `gui.py` o importa incondicionalmente, e a suíte roda no Linux no CI.
if IS_WINDOWS:
    from ctypes import wintypes

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]


    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )


    class WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]


    def _declare_prototypes() -> None:
        """Declara argtypes/restype das funções Win32 usadas aqui.

        Sem isto o ctypes assume ``int`` (32 bits) para retorno e argumentos, e em
        Windows 64 bits todo handle (HWND, HINSTANCE, HICON) é truncado — o sintoma
        é um ``OverflowError: int too long to convert`` ao repassar o handle
        truncado para a próxima chamada.
        """
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user32.RegisterClassW.restype = wintypes.ATOM

        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_void_p

        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]

        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE

        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user32.LoadIconW.restype = wintypes.HICON

        shell32.ExtractIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT]
        shell32.ExtractIconW.restype = wintypes.HICON

        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_void_p, wintypes.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
        ]
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]

        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_void_p


class TrayIcon:
    """Ícone de bandeja com menu de contexto.

    ``commands`` é a lista de itens do menu: ``(identificador, rótulo)``. O
    identificador é o que chega na fila quando o item é escolhido; ``None``
    como rótulo insere um separador.
    """

    def __init__(
        self,
        title: str,
        icon_path: Path | None,
        commands: list[tuple[str, str | None]],
        default_command: str,
    ):
        self.title = title
        self.icon_path = icon_path
        self.commands = commands
        self.default_command = default_command
        self.events: queue.Queue[str] = queue.Queue()

        self._hwnd = None
        self._hicon = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._failed = False
        # As referências precisam sobreviver ao escopo: o Windows guarda o
        # ponteiro do WNDPROC e chamá-lo depois de coletado derruba o processo.
        self._wndproc = WNDPROC(self._handle_message)
        self._wndclass = None
        self._ids = {index + 1: name for index, (name, label) in enumerate(commands) if label}

    # ------------------------------------------------------------------ ciclo
    def start(self, timeout: float = 5.0) -> bool:
        """Sobe a thread da bandeja. False se o ícone não pôde ser criado."""
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return not self._failed and self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def notify(self, title: str, message: str) -> None:
        """Balão de notificação do Windows (usado ao minimizar para a bandeja)."""
        if not self._hwnd:
            return
        data = self._icon_data(NIF_INFO)
        data.szInfoTitle = title[:63]
        data.szInfo = message[:255]
        data.dwInfoFlags = NIIF_NONE
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    # --------------------------------------------------------------- internos
    def _icon_data(self, flags: int) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self._hicon or 0
        data.szTip = self.title[:127]
        return data

    def _load_icon(self):
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        if self.icon_path and Path(self.icon_path).exists():
            handle = user32.LoadImageW(
                None, str(self.icon_path), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if handle:
                return handle

        # No executável congelado o .ico pode não estar em disco — mas o próprio
        # .exe carrega o ícone como recurso.
        if getattr(sys, "frozen", False):
            handle = shell32.ExtractIconW(None, sys.executable, 0)
            if handle and handle > 1:
                return handle

        return user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

    def _run(self) -> None:
        try:
            self._create_window()
            self._add_icon()
            self._ready.set()
            self._message_loop()
        except Exception:
            self._failed = True
            self._ready.set()
        finally:
            self._remove_icon()

    def _create_window(self) -> None:
        _declare_prototypes()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        wndclass = WNDCLASS()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.lpszClassName = "TiqueTaqueSyncTray"
        wndclass.hInstance = kernel32.GetModuleHandleW(None)
        self._wndclass = wndclass  # evita coleta enquanto a classe está viva

        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom:
            # ERROR_CLASS_ALREADY_EXISTS é aceitável: reutilizamos a classe.
            if kernel32.GetLastError() != 1410:
                raise ctypes.WinError()

        self._hwnd = user32.CreateWindowExW(
            0, wndclass.lpszClassName, self.title, 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, wndclass.hInstance, None,
        )
        if not self._hwnd:
            raise ctypes.WinError()

    def _add_icon(self) -> None:
        self._hicon = self._load_icon()
        data = self._icon_data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        if not ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise ctypes.WinError()

    def _remove_icon(self) -> None:
        if not self._hwnd:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._hwnd = None

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _handle_message(self, hwnd, message, wparam, lparam):
        user32 = ctypes.windll.user32

        if message == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.events.put(self.default_command)
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0

        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0

        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0

        # `restype=c_void_p` devolve None quando o valor é zero, e devolver
        # None de um callback declarado como inteiro estoura TypeError.
        return user32.DefWindowProcW(hwnd, message, wparam, lparam) or 0

    def _show_menu(self) -> None:
        user32 = ctypes.windll.user32

        menu = user32.CreatePopupMenu()
        try:
            for index, (name, label) in enumerate(self.commands, start=1):
                if label is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(menu, MF_STRING, index, label)

            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            # Exigido pela documentação: sem isto o menu não fecha ao clicar fora.
            user32.SetForegroundWindow(self._hwnd)

            chosen = user32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self._hwnd, None
            )
            if chosen and chosen in self._ids:
                self.events.put(self._ids[chosen])
        finally:
            user32.DestroyMenu(menu)


def create(
    title: str,
    icon_path: Path | None,
    commands: list[tuple[str, str | None]],
    default_command: str,
) -> TrayIcon | None:
    """Cria o ícone de bandeja, ou devolve None quando não há suporte."""
    if not IS_WINDOWS:
        return None

    icon = TrayIcon(title, icon_path, commands, default_command)
    if not icon.start():
        return None
    return icon
