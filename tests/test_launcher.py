from app.launcher import LauncherManager
from app.models import Character, LauncherUpdate


def test_launcher_generates_one_bat_per_character_and_encrypts_stored_secret(tmp_path):
    game_dir = tmp_path / "element"
    game_dir.mkdir()
    (game_dir / "ElementClient.exe").write_bytes(b"")
    manager = LauncherManager(tmp_path / "data")
    character = Character(character_name="Cookie", profile_id="scenario")

    updated = manager.configure(
        character,
        LauncherUpdate(
            game_dir=str(game_dir),
            login="local-user",
            password="local-secret",
            window_title="Cookie shop",
        ),
    )

    launcher = (tmp_path / "data" / "launchers" / f"{character.id}.bat")
    text = launcher.read_text(encoding="utf-8")
    assert updated.launcher_file == str(launcher)
    assert "ElementClient.exe startbypatcher nocheck" in text
    assert "user:local-user" in text
    assert "pwd:local-secret" in text
    assert "role:Cookie" in text
    assert "d_title:Cookie shop" in text
    assert "local-secret" not in (tmp_path / "data" / "credentials.json").read_text(encoding="utf-8")
