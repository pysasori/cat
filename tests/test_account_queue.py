import pytest

from app.account_queue import AccountQueue
from app.models import Character, JobState, Profile
from app.storage import Repository
from app.windows import GameWindow


class FakeLauncher:
    def __init__(self):
        self.launched = []

    def launch(self, character):
        self.launched.append(character.id)


class FakeShopRunner:
    def __init__(self, repository=None):
        self.started = []
        self.bound_hwnds = []
        self.refreshed = []
        self.refresh_flags = []
        self.repository = repository

    def refresh_character_market(self, character_id):
        self.refreshed.append(character_id)

    def start(self, dry_run, profile_id=None, character_id=None, refresh_market=True):
        self.started.append(character_id)
        self.refresh_flags.append(refresh_market)
        if self.repository is not None:
            character = next(item for item in self.repository.characters() if item.id == character_id)
            self.bound_hwnds.append(character.window_hwnd)
        return JobState(running=False, stage="done")

    def snapshot(self):
        return JobState(running=False, stage="done")

    def stop(self):
        return JobState(running=False, stage="stopped")


def add_character(repository, profile, name, title):
    return repository.add_character(
        Character(
            character_name=name,
            profile_id=profile.id,
            shop_name=name,
            launcher_file=f"{name}.bat",
            window_title=title,
            queue_enabled=True,
            offline_trade=True,
        )
    )


def test_account_queue_processes_characters_in_repository_order(tmp_path):
    repository = Repository(tmp_path)
    config = repository.config().model_copy(update={"queue_login_delay": 0})
    repository.save_config(config)
    profile = repository.add_profile(Profile(name="Shared"))
    repository.save_characters([])
    first = add_character(repository, profile, "CatOne", "cat-one")
    second = add_character(repository, profile, "CatTwo", "cat-two")
    windows = iter(
        [
            [],
            [GameWindow(101, "ComebackPW", 1440, 1080, False, 1)],
            [],
            [],
            [GameWindow(202, "ComebackPW", 1440, 1080, False, 2)],
            [],
        ]
    )
    launcher = FakeLauncher()
    shop = FakeShopRunner(repository)
    queue = AccountQueue(
        repository,
        launcher,
        shop,
        lambda: next(windows),
        lambda window: "CatOne" if window.hwnd == 101 else "CatTwo",
    )

    queue._run([first.id, second.id])

    assert launcher.launched == [first.id, second.id]
    assert shop.refreshed == [first.id, second.id]
    assert shop.started == [first.id, second.id]
    assert shop.refresh_flags == [False, False]
    assert shop.bound_hwnds == [101, 202]
    assert [item.window_hwnd for item in repository.characters()] == [None, None]
    assert queue.snapshot().completed == 2


def test_account_queue_requires_offline_trade(tmp_path):
    repository = Repository(tmp_path)
    profile = repository.add_profile(Profile(name="Shared"))
    repository.save_characters([])
    character = add_character(repository, profile, "Cat", "cat-one")
    repository.replace_character(character.model_copy(update={"offline_trade": False}))
    queue = AccountQueue(repository, FakeLauncher(), FakeShopRunner(), lambda: [])

    with pytest.raises(RuntimeError, match="офлайн-торгівлю"):
        queue.start()
