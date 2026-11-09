#!/usr/bin/env python3
"""Create a reviewable update branch without rewriting either Git history."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


class UpdateError(RuntimeError):
    pass


def git(*args: str, cwd: Path | None = None, identity: str | None = None) -> str:
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR",
        "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE",
    ):
        env.pop(key, None)
    if identity is not None:
        env.update(GIT_AUTHOR_NAME="ganievshr", GIT_COMMITTER_NAME="ganievshr",
                   GIT_AUTHOR_EMAIL=identity, GIT_COMMITTER_EMAIL=identity)
    command = ["git", "-c", f"core.hooksPath={os.devnull}",
               "-c", "commit.gpgSign=false", *args]
    try:
        result = subprocess.run(command, cwd=cwd, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise UpdateError("Git не установлен. Установите Git и повторите команду.") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise UpdateError(f"Ошибка Git ({args[0]}): {detail}")
    return result.stdout.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Подготовить новую ветку StockExpert, сохранив обе истории Git. Push не выполняется.")
    parser.add_argument("--repository", default="https://github.com/ganievshr1/StockExpert.git")
    parser.add_argument("--target", type=Path, required=True,
                        help="Новая, ещё не существующая папка для клонирования")
    parser.add_argument("--email", required=True,
                        help="Подтверждённый email или noreply из GitHub Settings → Emails")
    parser.add_argument("--bundle", type=Path,
                        default=Path(__file__).resolve().with_name("history.bundle"))
    parser.add_argument("--branch", default="update/b1-ganievshr-20261109")
    return parser.parse_args(argv)


def prepare(args: argparse.Namespace) -> dict[str, str]:
    # Refuse before cloning or editing anything.
    raw_target = args.target.expanduser().absolute()
    if os.path.lexists(raw_target):
        raise UpdateError(f"Папка назначения уже существует: {raw_target}. Укажите новую папку.")
    target = raw_target.resolve()
    bundle = args.bundle.expanduser().resolve()
    if not bundle.is_file():
        raise UpdateError(f"Не найден Git bundle: {bundle}")
    email = args.email.strip()
    if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email):
        raise UpdateError("Укажите корректный email через --email.")
    if not args.repository or args.repository.startswith("-"):
        raise UpdateError("Некорректный адрес репозитория.")
    git("check-ref-format", "--branch", args.branch)
    bundle_heads = git("bundle", "list-heads", str(bundle), "refs/heads/package-history")
    lines = bundle_heads.splitlines()
    if len(lines) != 1 or lines[0].split()[-1] != "refs/heads/package-history":
        raise UpdateError("В bundle отсутствует единственная ветка package-history.")
    expected_package = lines[0].split()[0]

    try:
        # No shallow options: all history reachable from the remote branches is fetched.
        # The empty template and command-level hooksPath prevent local Git hooks from running.
        with tempfile.TemporaryDirectory(prefix="stockexpert-empty-template-") as template:
            git("clone", "--no-local", f"--template={template}", "--",
                args.repository, str(target))
    except UpdateError as exc:
        raise UpdateError(
            f"Не удалось клонировать репозиторий. {exc}\n"
            "Для закрытого GitHub-репозитория сначала выполните gh auth login и gh auth setup-git "
            "или настройте SSH и используйте --repository git@github.com:ganievshr1/StockExpert.git. "
            "Если после ошибки осталась папка назначения, выберите другое имя; скрипт её не удаляет."
        ) from exc

    old_tip = git("rev-parse", "--verify", "HEAD", cwd=target)
    default_branch = git("symbolic-ref", "--short", "HEAD", cwd=target)
    if git("rev-parse", "--is-shallow-repository", cwd=target) != "false":
        raise UpdateError("Получен shallow clone; операция остановлена, история не изменена.")
    git("config", "user.name", "ganievshr", cwd=target)
    git("config", "user.email", email, cwd=target)
    git("checkout", "-b", args.branch, cwd=target)
    package_ref = "refs/stockexpert/package-history"
    git("fetch", "--no-tags", str(bundle),
        f"refs/heads/package-history:{package_ref}", cwd=target)
    package_tip = git("rev-parse", package_ref, cwd=target)
    if package_tip != expected_package:
        raise UpdateError("Bundle изменился во время чтения; операция остановлена.")
    git("bundle", "verify", str(bundle), cwd=target)
    git("merge", "--no-commit", "--no-ff", "--allow-unrelated-histories",
        "-s", "ours", package_ref, cwd=target)
    # 'ours' establishes both parents; read-tree installs the complete package snapshot.
    # This only touches the clean, newly-created clone, never an existing working directory.
    git("rev-parse", "--verify", "MERGE_HEAD", cwd=target)
    git("read-tree", "--reset", "-u", package_ref, cwd=target)
    git("commit", "-m", "chore: update StockExpert B1 by ganievshr; preserve both histories",
        cwd=target, identity=email)

    merge_tip = git("rev-parse", "HEAD", cwd=target)
    parents = git("show", "-s", "--format=%P", "HEAD", cwd=target).split()
    if parents != [old_tip, package_tip]:
        raise UpdateError("Проверка родителей merge-коммита не пройдена. Push не выполнялся.")
    if git("rev-parse", "HEAD^{tree}", cwd=target) != git("rev-parse", f"{package_ref}^{{tree}}", cwd=target):
        raise UpdateError("Состав файлов отличается от пакета. Push не выполнялся.")
    git("merge-base", "--is-ancestor", old_tip, "HEAD", cwd=target)
    git("merge-base", "--is-ancestor", package_tip, "HEAD", cwd=target)
    if git("status", "--porcelain", cwd=target):
        raise UpdateError("После подготовки появились незакоммиченные изменения. Push не выполнялся.")
    return {"target": str(target), "branch": args.branch, "default_branch": default_branch,
            "old_tip": old_tip, "package_tip": package_tip, "merge_tip": merge_tip}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = prepare(args)
    except UpdateError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    q = shlex.quote
    print("Готово. Создан merge-коммит с исходной историей GitHub и историей пакета.")
    print(f"Старая вершина: {result['old_tip']}")
    print(f"История пакета: {result['package_tip']}")
    print(f"Новый коммит:   {result['merge_tip']}")
    print("Новое рабочее дерево точно совпадает с файлами пакета.")
    print("Исходные папки и Docker не изменялись. На GitHub пока ничего не отправлено.")
    print("\nПроверка и отправка новой ветки:")
    print(f"cd {q(result['target'])}")
    print("git log --graph --decorate --oneline --all -25")
    print("git diff --stat HEAD^1 HEAD")
    print(f"git push -u origin {q(result['branch'])}")
    print("\nЕсли нужна отправка прямо в основную ветку, вместо предыдущего push:")
    print("git fetch origin")
    print(f"git merge-base --is-ancestor {q('origin/' + result['default_branch'])} HEAD && "
          f"git push origin {q('HEAD:refs/heads/' + result['default_branch'])}")
    print("Если проверка или push отклонены из-за новых коммитов/защиты ветки, force не используйте: "
          "опубликуйте ветку обновления и создайте Pull Request с обычным merge commit "
          "(не Squash и не Rebase), либо подготовьте новую папку заново.")
    print("Старые файлы БД, если были закоммичены раньше, остаются в старой истории Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
