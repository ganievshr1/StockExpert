"""Reset this StockExpert installation; preserve downloaded models and image caches."""

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from .init_env import ROOT, generate_values, write_env
else:
    from init_env import ROOT, generate_values, write_env

DATA_VOLUMES = ("postgres_data", "redis_data", "minio_data", "qdrant_data")
BACKEND_PROJECT = "stockexpert-backend"
FRONTEND_PROJECT = "stockexpert-frontend"


def command(args, env, *, capture=False):
    return subprocess.run(
        args,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def clean_environment(values, old_env):
    keys = set(values)
    if old_env.exists():
        for line in old_env.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=", line)
            if match:
                keys.add(match[1])
    # A shell export must not override a freshly generated password or model.
    keys.update(
        {
            "DATABASE_URL",
            "OLLAMA_IMAGE",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_ENV_FILES",
            "STOCKEXPERT_ENV_FILE",
        }
    )
    return {key: value for key, value in os.environ.items() if key not in keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete StockExpert users, sessions, uploads, results and indexes; rotate passwords.",
    )
    args = parser.parse_args()
    if not args.yes:
        parser.exit(
            2,
            "Удаляет все данные StockExpert и заменяет .env новыми паролями. "
            "Модели Ollama и кэш сохраняются. Для выполнения: --yes\n",
        )
    frontend = ROOT.parent / "stockexpert-frontend" / "docker-compose.yml"
    backend = ROOT / "docker-compose.yml"
    if not backend.is_file() or not frontend.is_file():
        raise SystemExit(
            "Ожидаются соседние каталоги stockexpert-backend и stockexpert-frontend с docker-compose.yml."
        )
    values = generate_values()
    env = clean_environment(values, ROOT / ".env")
    command(["docker", "info", "--format", "{{.ServerVersion}}"], env, capture=True)
    # Prepare configuration and builds before removing user data.
    with tempfile.TemporaryDirectory(
        prefix=".stockexpert-reset-", dir=ROOT
    ) as temporary:
        fresh = Path(temporary) / ".env"
        write_env(fresh, values)
        env["STOCKEXPERT_ENV_FILE"] = str(fresh)

        def compose(project, file, *args, capture=False):
            return command(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    project,
                    "--env-file",
                    str(fresh),
                    "-f",
                    str(file),
                    *args,
                ],
                env,
                capture=capture,
            )

        compose(BACKEND_PROJECT, backend, "config", "--quiet")
        compose(FRONTEND_PROJECT, frontend, "config", "--quiet")
        print("Сборка перед удалением данных…", flush=True)
        compose(
            BACKEND_PROJECT, backend, "pull", "postgres", "redis", "qdrant", "ollama"
        )
        compose(BACKEND_PROJECT, backend, "build")
        compose(FRONTEND_PROJECT, frontend, "build")
        print("Удаление данных StockExpert…", flush=True)
        compose(FRONTEND_PROJECT, frontend, "down", "--remove-orphans")
        compose(BACKEND_PROJECT, backend, "down", "--remove-orphans")
        for volume in DATA_VOLUMES:
            names = command(
                [
                    "docker",
                    "volume",
                    "ls",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={BACKEND_PROJECT}",
                    "--filter",
                    f"label=com.docker.compose.volume={volume}",
                ],
                env,
                capture=True,
            ).stdout.splitlines()
            for name in names:
                command(["docker", "volume", "rm", name], env)
        os.replace(fresh, ROOT / ".env")
        # The new credentials are now durable even if pulling or startup fails.
        fresh = ROOT / ".env"
        env["STOCKEXPERT_ENV_FILE"] = str(fresh)
        compose(
            BACKEND_PROJECT,
            backend,
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "180",
        )
        compose(FRONTEND_PROJECT, frontend, "up", "-d", "--no-build", "--pull", "never")
        models = compose(
            BACKEND_PROJECT,
            backend,
            "exec",
            "-T",
            "ollama",
            "ollama",
            "list",
            capture=True,
        ).stdout
        installed = {
            line.split()[0] for line in models.splitlines()[1:] if line.split()
        }
        for model in ("bge-m3:latest", "qwen3:8b-q4_K_M"):
            if model not in installed:
                compose(
                    BACKEND_PROJECT,
                    backend,
                    "exec",
                    "-T",
                    "ollama",
                    "ollama",
                    "pull",
                    model,
                )
        compose(BACKEND_PROJECT, backend, "ps", "-a")
    print(
        "Готово. Интерфейс: http://localhost:5173. Новые ADMIN_USERNAME и ADMIN_PASSWORD находятся в .env. "
        "Старые пользователи и токены недействительны. DeepSeek API key очищен."
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        raise SystemExit(
            "Команда Docker завершилась с ошибкой. Смотрите сообщение выше. "
            "Если сброс уже выполнен, новые пароли сохранены в .env; повторный --yes снова удалит данные."
        )
