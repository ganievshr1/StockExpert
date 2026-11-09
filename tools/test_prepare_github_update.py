"""Offline integration checks; no GitHub, Docker, or app services are used."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("prepare_github_update.py")


def git(root, *args):
    env = os.environ.copy()
    env.update(GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                             "-c", "commit.gpgSign=false", *args], cwd=root,
                            env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stockexpert-git-test-")
        self.root = Path(self.tmp.name)
        self.old = self.root / "old"
        self.old.mkdir()
        git(self.old, "init", "-b", "master")
        (self.old / "old-only.txt").write_text("old source\n")
        git(self.old, "add", ".")
        git(self.old, "commit", "-m", "old first")
        self.old_first = git(self.old, "rev-parse", "HEAD")
        (self.old / "app.py").write_text("old app\n")
        (self.old / "database.sqlite").write_bytes(b"old tracked database")
        git(self.old, "add", ".")
        git(self.old, "commit", "-m", "old second")
        self.old_tip = git(self.old, "rev-parse", "HEAD")
        self.remote = self.root / "remote.git"
        git(self.root, "clone", "--bare", str(self.old), str(self.remote))
        self.package = self.root / "package"
        self.package.mkdir()
        git(self.package, "init", "-b", "package-history")
        (self.package / "app.py").write_text("new app\n")
        git(self.package, "add", ".")
        git(self.package, "commit", "-m", "restored release")
        self.package_first = git(self.package, "rev-parse", "HEAD")
        (self.package / "CHANGELOG.txt").write_text("verified release history\n")
        git(self.package, "add", ".")
        git(self.package, "commit", "-m", "author and history")
        self.package_tip = git(self.package, "rev-parse", "HEAD")
        self.bundle = self.root / "history.bundle"
        git(self.package, "bundle", "create", str(self.bundle), "package-history")
        self.target = self.root / "new working tree"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, extra=(), omit_email=False):
        args = [sys.executable, str(SCRIPT), "--repository", str(self.remote),
                "--target", str(self.target), "--bundle", str(self.bundle)]
        if not omit_email:
            args += ["--email", "owner@example.invalid"]
        return subprocess.run(args + list(extra), text=True, capture_output=True)

    def test_preserves_both_histories_and_replaces_complete_tree_without_push(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.target, "show", "-s", "--format=%P", "HEAD").split(),
                         [self.old_tip, self.package_tip])
        for commit in (self.old_first, self.old_tip, self.package_first, self.package_tip):
            git(self.target, "merge-base", "--is-ancestor", commit, "HEAD")
        self.assertEqual(git(self.target, "rev-parse", "HEAD^{tree}"),
                         git(self.package, "rev-parse", "HEAD^{tree}"))
        self.assertFalse((self.target / "database.sqlite").exists())
        self.assertFalse((self.target / "old-only.txt").exists())
        self.assertEqual((self.target / "app.py").read_text(), "new app\n")
        self.assertEqual(git(self.target, "status", "--porcelain"), "")
        self.assertEqual(git(self.target, "show", "-s", "--format=%an <%ae>", "HEAD"),
                         "ganievshr <owner@example.invalid>")
        self.assertEqual(git(self.target, "remote", "get-url", "origin"), str(self.remote))
        self.assertEqual(git(self.remote, "rev-parse", "master"), self.old_tip)
        self.assertEqual(git(self.target, "rev-parse", "--is-shallow-repository"), "false")
        self.assertIn("HEAD:refs/heads/master", result.stdout)
        self.assertNotIn("--force", result.stdout)
        self.assertTrue((self.old / "database.sqlite").exists())

    def test_existing_destination_is_never_modified(self):
        self.target.mkdir()
        marker = self.target / "keep"
        marker.write_text("untouched")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("уже существует", result.stderr)
        self.assertEqual(marker.read_text(), "untouched")
        self.assertEqual(list(self.target.iterdir()), [marker])

    def test_missing_email_fails_before_clone(self):
        result = self.run_script(omit_email=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())

    def test_invalid_branch_fails_before_clone(self):
        result = self.run_script(["--branch", "bad..branch"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())

    def test_absent_bundle_fails_before_clone(self):
        self.bundle.unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Не найден Git bundle", result.stderr)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
