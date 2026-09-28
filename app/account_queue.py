from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from difflib import SequenceMatcher

from app.capture import WindowCapture
from app.game_state import detect_character_name
from app.launcher import LauncherManager
from app.models import Character, JobState, QueueState
from app.shop import ShopRunner, get_runner
from app.storage import Repository
from app.windows import GameWindow, game_windows


class AccountQueue:
    """Launch configured characters and create their offline shops sequentially."""

    def __init__(
        self,
        repository: Repository,
        launcher: LauncherManager | None = None,
        shop_runner: ShopRunner | None = None,
        window_provider: Callable[[], list[GameWindow]] = game_windows,
        game_state_provider: Callable[[GameWindow], str | None] | None = None,
    ) -> None:
        self.repository = repository
        self.launcher = launcher or LauncherManager(repository.root)
        self.shop_runner = shop_runner or get_runner(repository)
        self.window_provider = window_provider
        self.game_state_provider = game_state_provider or (
            lambda window: detect_character_name(WindowCapture(window.hwnd).grab())
        )
        self.state = QueueState()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> QueueState:
        with self._lock:
            return self.state.model_copy(deep=True)

    def _set(self, **updates) -> None:
        with self._lock:
            self.state = self.state.model_copy(update=updates)

    def _log(self, message: str) -> None:
        with self._lock:
            self.state = self.state.model_copy(
                update={"message": message, "log": [*self.state.log, message][-200:]}
            )

    def _record_character_status(self, character_id: str | None, status: str, message: str) -> None:
        if not character_id:
            return
        character = next(
            (item for item in self.repository.characters() if item.id == character_id),
            None,
        )
        if character is None:
            return
        self.repository.replace_character(
            character.model_copy(
                update={
                    "last_run_status": status,
                    "last_run_message": message[:1000],
                    "last_run_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )

    def _selected(self, character_ids: list[str] | None) -> list[Character]:
        characters = self.repository.characters()
        if character_ids is None:
            return [item for item in characters if item.queue_enabled]
        wanted = set(character_ids)
        missing = wanted - {item.id for item in characters}
        if missing:
            raise RuntimeError("У черзі є невідомі персонажі")
        return [item for item in characters if item.id in wanted]

    def start(self, character_ids: list[str] | None = None) -> QueueState:
        with self._lock:
            if self.state.running:
                raise RuntimeError("черга акаунтів уже виконується")
            selected = self._selected(character_ids)
            if not selected:
                raise RuntimeError("у черзі немає увімкнених персонажів")
            missing_launcher = [item.character_name for item in selected if not item.launcher_file]
            if missing_launcher:
                raise RuntimeError(f"Не налаштовано BAT: {', '.join(missing_launcher)}")
            online_only = [item.character_name for item in selected if not item.offline_trade]
            if online_only:
                raise RuntimeError(
                    "Для послідовної черги увімкніть офлайн-торгівлю: "
                    + ", ".join(online_only)
                )
            self.state = QueueState(running=True, stage="starting", total=len(selected))
            self._thread = threading.Thread(
                target=self._run_guarded,
                args=([item.id for item in selected],),
                daemon=True,
            )
            self._thread.start()
            return self.state.model_copy(deep=True)

    def stop(self) -> QueueState:
        with self._lock:
            if self.state.running:
                self.state = self.state.model_copy(
                    update={"stop_requested": True, "message": "зупиняю чергу"}
                )
        self.shop_runner.stop()
        return self.snapshot()

    def _check_stop(self) -> None:
        if self.snapshot().stop_requested:
            raise InterruptedError

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_stop()
            time.sleep(min(0.2, max(0, end - time.monotonic())))

    def _find_window(
        self,
        title: str | None,
        excluded_hwnds: set[int],
        timeout: float,
    ) -> GameWindow:
        expected = (title or "").casefold()
        deadline = time.monotonic() + timeout
        last_candidates: list[GameWindow] = []
        while time.monotonic() < deadline:
            self._check_stop()
            candidates = [
                window for window in self.window_provider() if window.hwnd not in excluded_hwnds
            ]
            last_candidates = candidates
            for window in candidates:
                actual = window.title.casefold()
                if expected and (actual == expected or expected in actual):
                    return window
            if len(candidates) == 1:
                window = candidates[0]
                self._log(
                    f"Нове вікно знайдено за HWND {window.hwnd}; "
                    f"заголовок залишився «{window.title}»"
                )
                return window
            time.sleep(0.5)
        if len(last_candidates) > 1:
            raise RuntimeError(
                "після запуску одночасно з'явилося кілька нових вікон PW; "
                "не вдалося безпечно вибрати потрібне"
            )
        raise RuntimeError("після запуску BAT не з'явилося нове вікно PW")

    def _wait_for_job(self) -> JobState:
        while True:
            self._check_stop()
            state = self.shop_runner.snapshot()
            if not state.running:
                if state.error or state.stage == "error":
                    raise RuntimeError(state.error or state.message or "помилка виставлення лавки")
                return state
            time.sleep(0.4)

    def _wait_for_game(self, window: GameWindow, character: Character, timeout: float) -> str:
        expected = character.character_name.casefold()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_stop()
            try:
                detected = self.game_state_provider(window)
            except Exception:
                detected = None
            if detected:
                similarity = SequenceMatcher(None, expected, detected.casefold()).ratio()
                if similarity >= 0.65:
                    return detected
            time.sleep(1.0)
        raise RuntimeError(f"{character.character_name}: не вдалося підтвердити вхід у світ")

    def _wait_for_close(self, hwnd: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_stop()
            if not any(window.hwnd == hwnd for window in self.window_provider()):
                return
            time.sleep(0.5)
        raise RuntimeError("після офлайн-торгівлі вікно клієнта не закрилося")

    def _run_guarded(self, character_ids: list[str]) -> None:
        try:
            self._run(character_ids)
            self._set(
                running=False,
                stage="done",
                message="усі офлайн-лавки запущено",
                current_character_id=None,
                current_character_name="",
            )
        except InterruptedError:
            self._record_character_status(
                self.snapshot().current_character_id,
                "stopped",
                "Чергу зупинено",
            )
            self._set(running=False, stage="stopped", message="чергу зупинено")
        except Exception as error:
            self._record_character_status(
                self.snapshot().current_character_id,
                "error",
                str(error),
            )
            self._set(running=False, stage="error", message=str(error), error=str(error))

    def _run(self, character_ids: list[str]) -> None:
        config = self.repository.config()
        for position, character_id in enumerate(character_ids, start=1):
            self._check_stop()
            character = next(
                (item for item in self.repository.characters() if item.id == character_id),
                None,
            )
            if character is None:
                raise RuntimeError("персонажа з черги видалено")
            self._set(
                stage="launching",
                current_character_id=character.id,
                current_character_name=character.character_name,
            )
            self._record_character_status(character.id, "running", "Запуск клієнта")
            self._log(f"{position}/{len(character_ids)} · запускаю {character.character_name}")
            existing = {window.hwnd for window in self.window_provider()}
            self.launcher.launch(character)
            window = self._find_window(
                character.window_title,
                existing,
                config.queue_window_timeout,
            )
            character = character.model_copy(update={"window_hwnd": window.hwnd, "window_index": 0})
            self.repository.replace_character(character)
            self._set(stage="game_wait")
            self._log(f"{character.character_name}: вікно знайдено, чекаю фактичний вхід у світ")
            detected_name = self._wait_for_game(window, character, config.queue_game_timeout)

            self._set(stage="trade_cooldown")
            self._log(
                f"{character.character_name}: вхід підтверджено як {detected_name}; "
                f"чекаю блокування трейду {config.queue_login_delay:g} с"
            )
            self._wait(config.queue_login_delay)

            self._set(stage="trading")
            self._log(f"{character.character_name}: виставляю лоти й офлайн-торгівлю")
            # Make the final queue stop check and child-job start atomic. Without
            # this lock, Stop could land in the tiny gap where no shop job was
            # running yet and the queue could start one immediately afterwards.
            with self._lock:
                if self.state.stop_requested:
                    raise InterruptedError
                self.shop_runner.start(False, character_id=character.id)
            self._wait_for_job()

            self._set(stage="offline_wait")
            self._log(f"{character.character_name}: чекаю закриття клієнта")
            self._wait_for_close(window.hwnd, config.queue_close_timeout)
            latest = next(
                (item for item in self.repository.characters() if item.id == character.id),
                None,
            )
            if latest is not None:
                self.repository.replace_character(latest.model_copy(update={"window_hwnd": None}))
            self._set(completed=position)
            self._log(f"{character.character_name}: офлайн-лавку запущено")


_queue: AccountQueue | None = None


def get_account_queue(repository: Repository) -> AccountQueue:
    global _queue
    if _queue is None or _queue.repository is not repository:
        _queue = AccountQueue(repository)
    return _queue
