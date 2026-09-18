from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from jarvis_v2.models import (
    AdjustVolumeIntent,
    CloseAppIntent,
    Intent,
    ListAppsIntent,
    MediaIntent,
    OpenAppIntent,
    OpenUrlIntent,
    PowerIntent,
    ScreenshotIntent,
    SetMuteIntent,
    SetVolumeIntent,
    ToolResult,
    WindowsSettingsIntent,
)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetVolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(ge=0, le=100)


class AdjustVolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delta: int = Field(ge=-100, le=100)


class MuteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    muted: bool


class AppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(min_length=1, max_length=80, pattern=r"^[\w .+()-]+$")


class UrlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def http_only(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError("only valid HTTP/HTTPS addresses are allowed")
        return value


class SettingsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: str


class ScreenshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_path: str | None = None


class MediaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


class PowerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


class FileSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=120, pattern=r"^[\w .()'\-]+$")


class FilePathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=260)


class FileTransferArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=260)
    destination: str = Field(min_length=1, max_length=260)


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    arguments: type[BaseModel]
    execute: Callable[[Any], ToolResult]

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": self.arguments.model_json_schema(),
            },
        }


SETTINGS_URIS = {
    "settings": "ms-settings:",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "network": "ms-settings:network-status",
    "bluetooth": "ms-settings:bluetooth",
    "apps": "ms-settings:appsfeatures",
    "notifications": "ms-settings:notifications",
    "privacy": "ms-settings:privacy",
    "windows_update": "ms-settings:windowsupdate",
}
APP_EXECUTABLES = {
    "calculator": "calc.exe",
    "калькулятор": "calc.exe",
    "notepad": "notepad.exe",
    "блокнот": "notepad.exe",
    "paint": "mspaint.exe",
    "chrome": "chrome.exe",
    "steam": "steam.exe",
    "discord": "Discord.exe",
    "telegram": "Telegram.exe",
    "visual studio code": "Code.exe",
}
PROTECTED_PROCESSES = {
    "system",
    "registry",
    "csrss",
    "wininit",
    "winlogon",
    "services",
    "lsass",
    "smss",
    "dwm",
    "explorer",
    "jarvis",
    "python",
    "powershell",
}


class WindowsBackend:
    def __init__(self, screenshot_root: Path | None = None) -> None:
        pictures = Path(os.getenv("USERPROFILE", str(Path.home()))) / "Pictures" / "Jarvis"
        self.screenshot_root = screenshot_root or pictures

    @staticmethod
    def _endpoint() -> Any:
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)
        return endpoint if endpoint is not None else device.Activate()

    def set_volume(self, level: int) -> ToolResult:
        try:
            endpoint = self._endpoint()
            endpoint.SetMasterVolumeLevelScalar(level / 100, None)
            endpoint.SetMute(False, None)
            actual = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        except Exception as error:
            return ToolResult(
                tool="set_volume",
                success=False,
                message=f"Не вдалося змінити гучність: {type(error).__name__}.",
            )
        return ToolResult(
            tool="set_volume",
            success=abs(actual - level) <= 2,
            message=f"Гучність встановлено на {actual}%.",
            data={"level": actual},
        )

    def adjust_volume(self, delta: int) -> ToolResult:
        try:
            endpoint = self._endpoint()
            current = round(endpoint.GetMasterVolumeLevelScalar() * 100)
        except Exception as error:
            return ToolResult(
                tool="adjust_volume",
                success=False,
                message=f"Не вдалося прочитати гучність: {type(error).__name__}.",
            )
        result = self.set_volume(max(0, min(100, current + delta)))
        result.tool = "adjust_volume"
        return result

    def set_mute(self, muted: bool) -> ToolResult:
        try:
            endpoint = self._endpoint()
            endpoint.SetMute(muted, None)
            actual = bool(endpoint.GetMute())
        except Exception as error:
            return ToolResult(
                tool="set_mute", success=False, message=f"Не вдалося змінити звук: {type(error).__name__}."
            )
        text = "Системний звук вимкнено." if actual else "Системний звук увімкнено."
        return ToolResult(tool="set_mute", success=actual == muted, message=text, data={"muted": actual})

    def open_app(self, app: str) -> ToolResult:
        executable = APP_EXECUTABLES.get(app.lower(), app)
        try:
            subprocess.Popen([executable], close_fds=True)
        except OSError:
            shortcut = self._find_shortcut(app)
            if shortcut is None:
                return ToolResult(tool="open_app", success=False, message=f"Програму «{app}» не знайдено.")
            try:
                os.startfile(shortcut)  # type: ignore[attr-defined]
            except OSError as error:
                return ToolResult(
                    tool="open_app", success=False, message=f"Windows не прийняла запит: {error}."
                )
        return ToolResult(
            tool="open_app",
            success=True,
            message=f"Windows прийняла запит на запуск «{app}».",
            data={"dispatched": True, "app": app},
        )

    @staticmethod
    def _find_shortcut(app: str) -> Path | None:
        roots = [
            Path(os.getenv("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.getenv("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        wanted = re.sub(r"[^\w]+", "", app.lower())
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.lnk"):
                name = re.sub(r"[^\w]+", "", path.stem.lower())
                if wanted == name or wanted in name:
                    return path
        return None

    def close_app(self, app: str) -> ToolResult:
        try:
            import psutil
        except ImportError:
            return ToolResult(
                tool="close_app", success=False, message="Модуль керування процесами не встановлено."
            )
        target = Path(APP_EXECUTABLES.get(app.lower(), app)).stem.lower()
        if target in PROTECTED_PROCESSES:
            return ToolResult(
                tool="close_app", success=False, message=f"Процес «{target}» захищено від закриття."
            )
        matches = [
            process
            for process in psutil.process_iter(["name"])
            if Path(process.info.get("name") or "").stem.lower() == target
        ]
        if not matches:
            return ToolResult(
                tool="close_app", success=False, message=f"Запущену програму «{app}» не знайдено."
            )
        for process in matches:
            process.terminate()
        _, alive = psutil.wait_procs(matches, timeout=2)
        if alive:
            return ToolResult(tool="close_app", success=False, message=f"«{app}» не завершила роботу вчасно.")
        return ToolResult(
            tool="close_app", success=True, message=f"«{app}» закрито.", data={"count": len(matches)}
        )

    @staticmethod
    def open_url(url: str) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(ord(char) < 32 for char in url):
            return ToolResult(
                tool="open_url", success=False, message="Дозволені лише коректні HTTP/HTTPS адреси."
            )
        try:
            subprocess.Popen(["explorer.exe", url], close_fds=True)
        except OSError as error:
            return ToolResult(tool="open_url", success=False, message=f"Windows не прийняла адресу: {error}.")
        return ToolResult(
            tool="open_url",
            success=True,
            message="Windows прийняла запит на відкриття сторінки.",
            data={"dispatched": True, "url": url},
        )

    @staticmethod
    def open_settings(page: str) -> ToolResult:
        uri = SETTINGS_URIS.get(page)
        if uri is None:
            return ToolResult(
                tool="windows_settings", success=False, message="Невідома сторінка налаштувань Windows."
            )
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
        except OSError as error:
            return ToolResult(
                tool="windows_settings", success=False, message=f"Не вдалося відкрити налаштування: {error}."
            )
        return ToolResult(
            tool="windows_settings", success=True, message="Налаштування Windows відкрито.", data={"uri": uri}
        )

    def screenshot(self, output_path: str | None = None) -> ToolResult:
        try:
            from PIL import ImageGrab
        except ImportError:
            return ToolResult(
                tool="screenshot", success=False, message="Модуль знімків екрана не встановлено."
            )
        path = (
            Path(output_path) if output_path else self.screenshot_root / f"screenshot-{int(time.time())}.png"
        )
        if path.suffix.lower() != ".png":
            return ToolResult(tool="screenshot", success=False, message="Знімок має бути файлом PNG.")
        if path.exists():
            return ToolResult(tool="screenshot", success=False, message=f"Файл уже існує: {path}.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(all_screens=True).save(path, "PNG")
        except Exception as error:
            return ToolResult(
                tool="screenshot",
                success=False,
                message=f"Не вдалося зробити знімок: {type(error).__name__}.",
            )
        return ToolResult(
            tool="screenshot",
            success=path.is_file() and path.stat().st_size > 0,
            message=f"Знімок збережено: {path}.",
            data={"path": str(path)},
        )

    @staticmethod
    def list_apps() -> ToolResult:
        try:
            import psutil

            apps = sorted(
                {
                    process.info.get("name")
                    for process in psutil.process_iter(["name"])
                    if process.info.get("name")
                }
            )
        except Exception as error:
            return ToolResult(
                tool="list_apps",
                success=False,
                message=f"Не вдалося отримати процеси: {type(error).__name__}.",
            )
        return ToolResult(
            tool="list_apps", success=True, message=f"Знайдено {len(apps)} процесів.", data={"apps": apps}
        )

    @staticmethod
    def media(action: str) -> ToolResult:
        key = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}.get(action)
        if key is None:
            return ToolResult(tool="media", success=False, message="Невідома медіакоманда.")
        try:
            ctypes.windll.user32.keybd_event(key, 0, 0, 0)
            ctypes.windll.user32.keybd_event(key, 0, 2, 0)
        except Exception as error:
            return ToolResult(
                tool="media", success=False, message=f"Медіакоманда не виконана: {type(error).__name__}."
            )
        return ToolResult(
            tool="media", success=True, message="Медіакоманду надіслано.", data={"action": action}
        )

    @staticmethod
    def _safe_user_path(value: str) -> Path:
        home = Path(os.getenv("USERPROFILE", str(Path.home()))).resolve()
        candidate = Path(value).expanduser().resolve()
        try:
            candidate.relative_to(home)
        except ValueError as error:
            raise ValueError("Дозволені лише шляхи у профілі поточного користувача.") from error
        return candidate

    def search_files(self, query: str) -> ToolResult:
        home = Path(os.getenv("USERPROFILE", str(Path.home())))
        matches: list[str] = []
        try:
            for root_name in ("Desktop", "Documents", "Downloads"):
                root = home / root_name
                if not root.is_dir():
                    continue
                for path in root.rglob("*"):
                    if query.lower() in path.name.lower():
                        matches.append(str(path))
                        if len(matches) == 30:
                            break
                if len(matches) == 30:
                    break
        except OSError as error:
            return ToolResult(tool="file_search", success=False, message=f"Пошук не вдався: {error}.")
        return ToolResult(
            tool="file_search",
            success=True,
            message=f"Знайдено {len(matches)} збігів.",
            data={"paths": matches},
        )

    def open_file(self, path: str) -> ToolResult:
        try:
            target = self._safe_user_path(path)
            if not target.is_file():
                return ToolResult(tool="open_file", success=False, message="Файл не знайдено.")
            os.startfile(target)  # type: ignore[attr-defined]
        except (OSError, ValueError) as error:
            return ToolResult(tool="open_file", success=False, message=f"Файл не відкрито: {error}.")
        return ToolResult(tool="open_file", success=True, message="Windows прийняла запит на відкриття файла.", data={"path": str(target)})

    def create_folder(self, path: str) -> ToolResult:
        try:
            target = self._safe_user_path(path)
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return ToolResult(tool="create_folder", success=False, message="Така папка вже існує.")
        except (OSError, ValueError) as error:
            return ToolResult(tool="create_folder", success=False, message=f"Папку не створено: {error}.")
        return ToolResult(tool="create_folder", success=True, message="Папку створено.", data={"path": str(target)})

    def transfer_file(self, source: str, destination: str, *, move: bool) -> ToolResult:
        try:
            origin = self._safe_user_path(source)
            target = self._safe_user_path(destination)
            if not origin.is_file() or target.exists():
                return ToolResult(tool="move_file" if move else "copy_file", success=False, message="Некоректний вихідний або цільовий файл.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(origin), str(target))
            else:
                shutil.copy2(origin, target)
        except (OSError, ValueError) as error:
            return ToolResult(tool="move_file" if move else "copy_file", success=False, message=f"Операція з файлом не вдалася: {error}.")
        name = "переміщено" if move else "скопійовано"
        return ToolResult(tool="move_file" if move else "copy_file", success=True, message=f"Файл {name}.", data={"path": str(target)})

    @staticmethod
    def power(action: str) -> ToolResult:
        command = {
            "shutdown": ["shutdown.exe", "/s", "/t", "0"],
            "restart": ["shutdown.exe", "/r", "/t", "0"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        }.get(action)
        if command is None:
            return ToolResult(tool="power", success=False, message="Невідома дія живлення.")
        try:
            subprocess.Popen(command, close_fds=True)
        except OSError as error:
            return ToolResult(tool="power", success=False, message=f"Windows не прийняла дію: {error}.")
        return ToolResult(tool="power", success=True, message="Дію живлення передано Windows.", data={"action": action})


class ToolRegistry:
    def __init__(self, backend: WindowsBackend | None = None) -> None:
        self.backend = backend or WindowsBackend()
        self._tools: dict[str, ToolDefinition] = {
            "set_volume": ToolDefinition(
                "set_volume",
                "Set Windows master volume to 0..100.",
                SetVolumeArgs,
                lambda args: self.backend.set_volume(args.level),
            ),
            "adjust_volume": ToolDefinition(
                "adjust_volume",
                "Adjust Windows master volume by a signed amount.",
                AdjustVolumeArgs,
                lambda args: self.backend.adjust_volume(args.delta),
            ),
            "set_mute": ToolDefinition(
                "set_mute",
                "Mute or unmute Windows audio.",
                MuteArgs,
                lambda args: self.backend.set_mute(args.muted),
            ),
            "open_app": ToolDefinition(
                "open_app",
                "Launch a Windows application.",
                AppArgs,
                lambda args: self.backend.open_app(args.app),
            ),
            "close_app": ToolDefinition(
                "close_app",
                "Gracefully terminate a non-protected app.",
                AppArgs,
                lambda args: self.backend.close_app(args.app),
            ),
            "open_url": ToolDefinition(
                "open_url",
                "Open an HTTP or HTTPS address.",
                UrlArgs,
                lambda args: self.backend.open_url(args.url),
            ),
            "windows_settings": ToolDefinition(
                "windows_settings",
                "Open a known Windows Settings page.",
                SettingsArgs,
                lambda args: self.backend.open_settings(args.page),
            ),
            "screenshot": ToolDefinition(
                "screenshot",
                "Capture all Windows displays as PNG.",
                ScreenshotArgs,
                lambda args: self.backend.screenshot(args.output_path),
            ),
            "list_apps": ToolDefinition(
                "list_apps", "List running processes.", EmptyArgs, lambda _: self.backend.list_apps()
            ),
            "media": ToolDefinition(
                "media",
                "Send a play/pause, next, or previous media key.",
                MediaArgs,
                lambda args: self.backend.media(args.action),
            ),
            "power": ToolDefinition(
                "power",
                "Run a shutdown, restart, or sleep action only after local confirmation.",
                PowerArgs,
                lambda args: self.backend.power(args.action),
            ),
            "file_search": ToolDefinition("file_search", "Search standard user folders by file name.", FileSearchArgs, lambda args: self.backend.search_files(args.query)),
            "open_file": ToolDefinition("open_file", "Open one existing file inside the user profile.", FilePathArgs, lambda args: self.backend.open_file(args.path)),
            "create_folder": ToolDefinition("create_folder", "Create a new folder inside the user profile.", FilePathArgs, lambda args: self.backend.create_folder(args.path)),
            "copy_file": ToolDefinition("copy_file", "Copy a file after host confirmation.", FileTransferArgs, lambda args: self.backend.transfer_file(args.source, args.destination, move=False)),
            "move_file": ToolDefinition("move_file", "Move a file after host confirmation.", FileTransferArgs, lambda args: self.backend.transfer_file(args.source, args.destination, move=True)),
        }

    def schemas(self) -> list[dict[str, object]]:
        # Power needs a host-owned user confirmation, so the model cannot invoke
        # it directly by inventing an argument or a confirmation token.
        return [tool.schema() for name, tool in self._tools.items() if name not in {"power", "copy_file", "move_file"}]

    def execute(self, name: str, arguments: dict[str, object]) -> ToolResult:
        if name in {"power", "copy_file", "move_file"}:
            return ToolResult(
                tool=name,
                success=False,
                message="Ця дія потребує підтвердження користувача й відхилена в прямому виклику.",
            )
        return self._execute(name, arguments)

    def _execute(self, name: str, arguments: dict[str, object]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool=name or "unknown", success=False, message="Невідомий інструмент відхилено."
            )
        try:
            parsed = tool.arguments.model_validate(arguments)
        except ValidationError as error:
            return ToolResult(
                tool=name,
                success=False,
                message=f"Некоректні аргументи інструмента: {error.errors()[0]['msg']}.",
            )
        try:
            return tool.execute(parsed)
        except Exception as error:
            return ToolResult(
                tool=name, success=False, message=f"Інструмент завершився помилкою: {type(error).__name__}."
            )

    def execute_confirmed(self, name: str, arguments: dict[str, object]) -> ToolResult:
        """Host-only path after an explicit, canonical user confirmation."""
        if name not in {"power", "copy_file", "move_file"}:
            return ToolResult(tool=name, success=False, message="Ця дія не потребує confirmed executor.")
        return self._execute(name, arguments)

    def execute_intent(self, intent: Intent) -> ToolResult:
        if isinstance(intent, SetVolumeIntent):
            return self.execute("set_volume", {"level": intent.level})
        if isinstance(intent, AdjustVolumeIntent):
            return self.execute("adjust_volume", {"delta": intent.delta})
        if isinstance(intent, SetMuteIntent):
            return self.execute("set_mute", {"muted": intent.muted})
        if isinstance(intent, OpenAppIntent):
            return self.execute("open_app", {"app": intent.app})
        if isinstance(intent, CloseAppIntent):
            return self.execute("close_app", {"app": intent.app})
        if isinstance(intent, OpenUrlIntent):
            return self.execute("open_url", {"url": intent.url})
        if isinstance(intent, WindowsSettingsIntent):
            return self.execute("windows_settings", {"page": intent.page})
        if isinstance(intent, ScreenshotIntent):
            return self.execute("screenshot", {})
        if isinstance(intent, ListAppsIntent):
            return self.execute("list_apps", {})
        if isinstance(intent, MediaIntent):
            return self.execute("media", {"action": intent.action})
        if isinstance(intent, PowerIntent):
            return self.execute_confirmed("power", {"action": intent.action})
        return ToolResult(tool=intent.kind, success=False, message="Невідомий intent не виконано.")
