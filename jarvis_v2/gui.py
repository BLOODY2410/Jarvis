"""Small local launcher and settings window for JARVIS v2.

The GUI never receives a tool call or model response.  It only writes the local
``.env`` configuration and starts the already-installed one-process runtime.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, messagebox, ttk

from jarvis_v2.processes import any_runtime_is_running, read_runtime_state, request_activation, request_stop

APP_TITLE = "JARVIS v2"
DEFAULT_ENV = """GEMINI_API_KEY=
GROQ_API_KEY=
FISH_API_KEY=
FISH_VOICE_ID=
JARVIS_WAKE_WORD=джарвіс
JARVIS_WAKE_VARIANTS=джарвіс,джарвис,джарвиз,jarvis
JARVIS_VOICE_MODE=gemini
JARVIS_FALLBACK_ENABLED=true
JARVIS_DEBUG=false
"""


def discover_root() -> Path | None:
    """Find the project next to a source checkout or a packaged launcher."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    candidates = [Path.cwd(), Path(__file__).resolve().parents[1]]
    configured = os.getenv("JARVIS_HOME")
    if configured:
        candidates.insert(0, Path(configured))
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "jarvis_v2").is_dir():
            return candidate
    return None


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_env(text: str, updates: dict[str, str]) -> str:
    """Update selected keys while preserving comments and unrelated settings."""
    remaining = dict(updates)
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}")
                continue
        result.append(line)
    result.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(result).rstrip() + "\n"


def runtime_pid(root: Path) -> int | None:
    del root  # Runtime state is shared across every extracted JARVIS folder.
    state = read_runtime_state()
    return state.pid if state else None


class JarvisWindow:
    def __init__(self, root: Tk, project: Path) -> None:
        self.root = root
        self.project = project
        self.status = StringVar(value="Перевіряю стан…")
        self.gemini_key = StringVar()
        self.groq_key = StringVar()
        self.wake_word = StringVar(value="джарвіс")
        self.fallback = BooleanVar(value=True)
        self.debug = BooleanVar(value=False)
        self._launching = False
        self._build()
        self._load()
        self._refresh()

    @property
    def env_path(self) -> Path:
        return self.project / ".env"

    def _build(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("560x510")
        self.root.minsize(520, 470)
        self.root.configure(bg="#10131a")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background="#10131a")
        style.configure("TLabel", background="#10131a", foreground="#e7edf6", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground="#f4f7fb")
        style.configure("Status.TLabel", foreground="#92d2ff")
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(14, 8))
        style.configure("TEntry", fieldbackground="#1c2330", foreground="#eef3f8")
        style.configure("TRadiobutton", background="#10131a", foreground="#e7edf6")
        style.configure("TCheckbutton", background="#10131a", foreground="#e7edf6")
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="JARVIS", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Локальний голосовий помічник", foreground="#aab8ca").grid(row=1, column=0, sticky="w", pady=(0, 14))
        ttk.Label(frame, textvariable=self.status, style="Status.TLabel").grid(row=2, column=0, sticky="w", pady=(0, 14))
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, sticky="ew", pady=(0, 20))
        self.start_button = ttk.Button(buttons, text="Запустити JARVIS", command=self.start)
        self.start_button.pack(side="left")
        ttk.Button(buttons, text="Зупинити", command=self.stop).pack(side="left", padx=8)
        ttk.Button(buttons, text="Слухати зараз", command=self.listen_now).pack(side="left")
        ttk.Button(buttons, text="Відкрити журнал", command=self.open_log).pack(side="right")
        settings = ttk.LabelFrame(frame, text="Налаштування", padding=14)
        settings.grid(row=4, column=0, sticky="nsew")
        settings.columnconfigure(1, weight=1)
        self._field(settings, 0, "Gemini API key", self.gemini_key, secret=True, paste=self.paste_gemini_key)
        self._field(settings, 1, "Groq API key (резервний)", self.groq_key, secret=True, paste=self.paste_groq_key)
        self._field(settings, 2, "Wake word", self.wake_word)
        ttk.Label(settings, text="Голос").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Label(settings, text="Gemini Live").grid(row=3, column=1, sticky="w", pady=6)
        ttk.Checkbutton(settings, text="Увімкнути резервний режим", variable=self.fallback).grid(row=4, column=1, sticky="w", pady=5)
        ttk.Checkbutton(settings, text="Діагностичний журнал", variable=self.debug).grid(row=5, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="Зберегти налаштування", command=self.save).grid(row=5, column=0, sticky="w", pady=(18, 0))
        ttk.Label(frame, text="Після запуску натисніть «Слухати зараз» або Ctrl+Alt+J, потім скажіть команду.", foreground="#aab8ca").grid(row=6, column=0, sticky="w", pady=(14, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

    @staticmethod
    def _field(
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: StringVar,
        secret: bool = False,
        paste: object | None = None,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 14))
        entry_frame = ttk.Frame(parent)
        entry_frame.grid(row=row, column=1, sticky="ew", pady=6)
        entry_frame.columnconfigure(0, weight=1)
        ttk.Entry(entry_frame, textvariable=variable, show="•" if secret else "").grid(row=0, column=0, sticky="ew")
        if paste is not None:
            ttk.Button(entry_frame, text="Вставити", command=paste).grid(row=0, column=1, padx=(8, 0))

    def _paste_key(self, destination: StringVar) -> None:
        try:
            value = self.root.clipboard_get().strip()
        except Exception:
            messagebox.showwarning(APP_TITLE, "У буфері обміну немає тексту для вставлення.")
            return
        if not value:
            messagebox.showwarning(APP_TITLE, "У буфері обміну немає тексту для вставлення.")
            return
        destination.set(value)
        self.status.set("Ключ вставлено. Натисніть «Зберегти налаштування».")

    def paste_gemini_key(self) -> None:
        self._paste_key(self.gemini_key)

    def paste_groq_key(self) -> None:
        self._paste_key(self.groq_key)

    def _load(self) -> None:
        if self.env_path.exists():
            text = self.env_path.read_text(encoding="utf-8")
        else:
            template = self.project / ".env.example"
            text = template.read_text(encoding="utf-8") if template.exists() else DEFAULT_ENV
        values = parse_env(text)
        self.gemini_key.set(values.get("GEMINI_API_KEY", ""))
        self.groq_key.set(values.get("GROQ_API_KEY", ""))
        self.wake_word.set(values.get("JARVIS_WAKE_WORD", "джарвіс"))
        self.fallback.set(values.get("JARVIS_FALLBACK_ENABLED", "true").lower() == "true")
        self.debug.set(values.get("JARVIS_DEBUG", "false").lower() == "true")

    def save(self, quiet: bool = False) -> None:
        if self.env_path.exists():
            existing = self.env_path.read_text(encoding="utf-8")
        else:
            template = self.project / ".env.example"
            existing = template.read_text(encoding="utf-8") if template.exists() else DEFAULT_ENV
        updates = {
            "GEMINI_API_KEY": self.gemini_key.get().strip(),
            "GROQ_API_KEY": self.groq_key.get().strip(),
            "JARVIS_VOICE_MODE": "gemini",
            "JARVIS_WAKE_WORD": self.wake_word.get().strip() or "джарвіс",
            "JARVIS_FALLBACK_ENABLED": str(self.fallback.get()).lower(),
            "JARVIS_DEBUG": str(self.debug.get()).lower(),
        }
        self.env_path.write_text(update_env(existing, updates), encoding="utf-8")
        if not quiet:
            self.status.set("Налаштування збережено. Вони застосуються під час наступного запуску.")

    def start(self) -> None:
        if self._launching or runtime_pid(self.project) or any_runtime_is_running():
            self.status.set("JARVIS уже працює.")
            return
        if not self.gemini_key.get().strip():
            messagebox.showwarning(APP_TITLE, "Додайте Gemini API key у налаштуваннях перед запуском.")
            return
        self.save(quiet=True)
        self._launching = True
        self.start_button.state(["disabled"])
        self.status.set("Підготовка JARVIS…")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        log_path = self.project / ".run" / "gui-runtime.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("a", encoding="utf-8") as log:
                command = [sys.executable, "--runtime"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "jarvis_v2"]
                environment = os.environ.copy()
                environment["JARVIS_ROOT"] = str(self.project)
                subprocess.Popen(
                    command,
                    cwd=self.project,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except (OSError, subprocess.CalledProcessError) as error:
            message = f"Не вдалося запустити: {error}"
            self.root.after(0, lambda: self._startup_failed(message))
            return
        self.root.after(1200, self._refresh)

    def _startup_failed(self, message: str) -> None:
        self._launching = False
        self.start_button.state(["!disabled"])
        self.status.set(message)

    def stop(self) -> None:
        self.status.set("Зупиняю JARVIS…")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def listen_now(self) -> None:
        state = read_runtime_state()
        if state is None:
            messagebox.showwarning(APP_TITLE, "Спершу запустіть JARVIS.")
            return
        request_activation(state.pid)
        self.status.set("Слухаю. Скажіть команду.")

    def _stop_worker(self) -> None:
        state = read_runtime_state()
        if state is None:
            self.root.after(0, self._refresh)
            return
        try:
            request_stop(state.pid)
        except OSError as error:
            message = f"Не вдалося надіслати запит зупинки: {error}"
            self.root.after(0, lambda: self.status.set(message))
            return
        self.root.after(700, self._refresh)

    def open_log(self) -> None:
        log_path = self.project / ".run" / "gui-runtime.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        os.startfile(log_path)  # type: ignore[attr-defined]

    def _refresh(self) -> None:
        state = read_runtime_state()
        if state:
            self._launching = False
            self.start_button.state(["disabled"])
            if Path(state.root) == self.project.resolve():
                self.status.set(f"JARVIS працює · PID {state.pid}")
            else:
                self.status.set(f"JARVIS працює в іншій папці · PID {state.pid}")
        elif any_runtime_is_running():
            self._launching = False
            self.start_button.state(["disabled"])
            self.status.set("JARVIS працює в іншому відкритому вікні")
        else:
            self._launching = False
            self.start_button.state(["!disabled"])
            self.status.set("JARVIS зупинено")
        self.root.after(1500, self._refresh)


def main() -> None:
    project = discover_root()
    if project is None:
        messagebox.showerror(APP_TITLE, "Не знайдено папку JARVIS. Розмістіть JARVIS.exe в папці dist проєкту.")
        return
    root = Tk()
    JarvisWindow(root, project)
    root.mainloop()


if __name__ == "__main__":
    main()
