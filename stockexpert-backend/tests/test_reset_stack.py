"""Exercise reset ordering and deletion scope with a fake Docker process boundary."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import init_env, reset_stack


def setup_reset(tmp_path, monkeypatch, fail=None):
    backend = tmp_path / "stockexpert-backend"
    frontend = tmp_path / "stockexpert-frontend"
    backend.mkdir()
    frontend.mkdir()
    (backend / "docker-compose.yml").write_text("services: {}")
    (frontend / "docker-compose.yml").write_text("services: {}")
    old = backend / ".env"
    old.write_text(
        "POSTGRES_PASSWORD=old-secret\nADMIN_PASSWORD=old-admin\nDEEPSEEK_API_KEY=old-api-key\n"
    )
    monkeypatch.setattr(reset_stack, "ROOT", backend)
    monkeypatch.setattr("sys.argv", ["reset_stack.py", "--yes"])
    monkeypatch.setenv("POSTGRES_PASSWORD", "stale-shell-password")
    monkeypatch.setenv("DATABASE_URL", "stale-shell-url")
    monkeypatch.setenv("COMPOSE_FILE", "wrong-compose")
    calls = []

    def fake(args, env, capture=False):
        calls.append(list(args))
        assert not {"POSTGRES_PASSWORD", "DATABASE_URL", "COMPOSE_FILE"} & env.keys()
        if fail and fail(args):
            raise subprocess.CalledProcessError(1, args)
        if args[1:3] == ["volume", "ls"]:
            assert "label=com.docker.compose.project=stockexpert-backend" in args
            label = next(
                x for x in args if x.startswith("label=com.docker.compose.volume=")
            )
            return SimpleNamespace(
                stdout="stockexpert-backend_" + label.split("=")[-1] + "\n"
            )
        if args[1] == "compose":
            path = Path(args[args.index("--env-file") + 1])
            assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
            assert env["STOCKEXPERT_ENV_FILE"] == str(path)
            if "up" in args:
                assert "old-secret" not in old.read_text()
        return SimpleNamespace(
            stdout="NAME ID SIZE\nbge-m3:latest cached 1GB\nqwen3:8b-q4_K_M cached 5GB\n"
        )

    monkeypatch.setattr(reset_stack, "command", fake)
    return old, calls


def test_reset_rotates_secrets_scopes_volumes_and_preserves_models(
    tmp_path, monkeypatch, capsys
):
    old, calls = setup_reset(tmp_path, monkeypatch)
    reset_stack.main()
    removed = [c[-1] for c in calls if c[1:3] == ["volume", "rm"]]
    assert set(removed) == {
        "stockexpert-backend_" + v for v in reset_stack.DATA_VOLUMES
    }
    assert not any("prune" in c or "-v" in c or "--volumes" in c for c in calls)
    assert not any(c[-2:] == ["pull", "bge-m3:latest"] for c in calls)
    assert not any(c[-2:] == ["pull", "qwen3:8b-q4_K_M"] for c in calls)
    down = next(i for i, c in enumerate(calls) if "down" in c)
    assert all(i < down for i, c in enumerate(calls) if "build" in c or "config" in c)
    assert (
        calls[down][calls[down].index("--project-name") + 1] == "stockexpert-frontend"
    )
    values = dict(line.split("=", 1) for line in old.read_text().splitlines())
    assert (
        values["DEEPSEEK_API_KEY"] == "" and values["LLM_DEFAULT_PROFILE"] == "qwen3-8b"
    )
    output = capsys.readouterr().out
    assert (
        values["ADMIN_PASSWORD"] not in output
        and values["POSTGRES_PASSWORD"] not in output
    )
    assert not list(old.parent.glob(".stockexpert-reset-*"))


def test_failed_build_never_deletes_data_or_replaces_passwords(tmp_path, monkeypatch):
    old, calls = setup_reset(tmp_path, monkeypatch, fail=lambda c: "build" in c)
    before = old.read_bytes()
    with pytest.raises(subprocess.CalledProcessError):
        reset_stack.main()
    assert old.read_bytes() == before
    assert not any("down" in c or c[1:3] == ["volume", "rm"] for c in calls)


def test_failed_start_retains_new_credentials(tmp_path, monkeypatch):
    old, _calls = setup_reset(tmp_path, monkeypatch, fail=lambda c: "up" in c)
    with pytest.raises(subprocess.CalledProcessError):
        reset_stack.main()
    assert "old-secret" not in old.read_text() and "ADMIN_PASSWORD=" in old.read_text()
    assert old.stat().st_mode & 0o777 == 0o600


def test_explicit_reset_flag_required_before_any_docker_command(tmp_path, monkeypatch):
    _old, calls = setup_reset(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["reset_stack.py"])
    with pytest.raises(SystemExit) as error:
        reset_stack.main()
    assert error.value.code == 2 and calls == []


def test_init_env_never_overwrites_existing_file_and_secrets_are_unique(tmp_path):
    values, other = init_env.generate_values(), init_env.generate_values()
    for key in (
        "SECRET_KEY",
        "POSTGRES_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "ADMIN_PASSWORD",
    ):
        assert values[key] != other[key]
    path = tmp_path / ".env"
    init_env.write_env(path, values)
    with pytest.raises(FileExistsError):
        init_env.write_env(path, other)
    assert path.stat().st_mode & 0o777 == 0o600
    assert values["ADMIN_PASSWORD"] in path.read_text()
