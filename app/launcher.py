from __future__ import annotations

import base64
import json
import subprocess
import threading
from pathlib import Path

import win32crypt

from app.models import Character, LauncherUpdate


class CredentialStore:
    """Small local DPAPI store. Secrets can only be decrypted by this Windows user."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def set(self, character_id: str, password: str) -> None:
        encrypted = win32crypt.CryptProtectData(
            password.encode("utf-8"),
            "PwCatBot launcher password",
            None,
            None,
            None,
            0,
        )
        with self._lock:
            payload = self._read()
            payload[character_id] = base64.b64encode(encrypted).decode("ascii")
            self._write(payload)

    def get(self, character_id: str) -> str | None:
        with self._lock:
            value = self._read().get(character_id)
        if not value:
            return None
        decrypted = win32crypt.CryptUnprotectData(base64.b64decode(value), None, None, None, 0)[1]
        return decrypted.decode("utf-8")

    def has(self, character_id: str) -> bool:
        with self._lock:
            return character_id in self._read()

    def delete(self, character_id: str) -> None:
        with self._lock:
            payload = self._read()
            if character_id in payload:
                del payload[character_id]
                self._write(payload)


class LauncherManager:
    def __init__(self, data_root: Path) -> None:
        self.launchers = data_root / "launchers"
        self.credentials = CredentialStore(data_root / "credentials.json")
        self.launchers.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _argument(name: str, value: str) -> str:
        token = f"{name}:{value}"
        return f'"{token}"' if any(character.isspace() for character in token) else token

    def configure(self, character: Character, settings: LauncherUpdate) -> Character:
        game_dir = Path(settings.game_dir).expanduser().resolve()
        executable = game_dir / "ElementClient.exe"
        if not game_dir.is_dir() or not executable.is_file():
            raise RuntimeError(f"У папці {game_dir} не знайдено ElementClient.exe")
        if settings.password:
            self.credentials.set(character.id, settings.password)
        password = self.credentials.get(character.id)
        if not password:
            raise RuntimeError("Вкажіть пароль для генерації BAT")

        launcher = self.launchers / f"{character.id}.bat"
        arguments = " ".join(
            [
                "startbypatcher",
                "nocheck",
                "game:cpw",
                "console:1",
                self._argument("user", settings.login),
                self._argument("pwd", password),
                self._argument("role", character.character_name),
                self._argument("d_title", settings.window_title),
            ]
        )
        script = (
            "@echo off\n"
            "chcp 65001 >nul\n"
            f'cd /d "{game_dir}"\n'
            f'start "" ElementClient.exe {arguments}\n'
        )
        launcher.write_text(script, encoding="utf-8")
        return character.model_copy(
            update={
                "game_dir": str(game_dir),
                "login": settings.login,
                "window_title": settings.window_title,
                "launcher_file": str(launcher),
            }
        )

    def launch(self, character: Character) -> None:
        launcher = Path(character.launcher_file)
        if not launcher.is_file():
            raise RuntimeError("BAT-файл ще не налаштовано")
        subprocess.Popen(
            ["cmd.exe", "/c", str(launcher)],
            cwd=character.game_dir or str(launcher.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def delete(self, character: Character) -> None:
        self.credentials.delete(character.id)
        if character.launcher_file:
            launcher = Path(character.launcher_file)
            if launcher.is_file() and launcher.parent.resolve() == self.launchers.resolve():
                launcher.unlink()
