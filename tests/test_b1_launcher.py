"""Exercise launcher isolation and restart guard with a recording Docker stub."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class B1LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy(Path(__file__).resolve().parents[1] / 'b1.sh', self.root / 'b1.sh')
        (self.root / 'stockexpert-backend').mkdir()
        (self.root / 'stockexpert-backend' / '.env').write_text('SECRET_KEY=fixture-only\n')
        bindir = self.root / 'bin'
        bindir.mkdir()
        docker = bindir / 'docker'
        docker.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$B1_TEST_LOG"
case " $* " in
  *" ps -q backend "*) printf 'test-container\\n' ;;
  *" exec -T backend python -c "*) printf '%s\\n' "${B1_TEST_ACTIVE:-0}" ;;
esac
''')
        docker.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
                    'B1_TEST_LOG': str(self.root / 'docker.log')}
        for name in ('B1_CPU', 'B1_PROJECT', 'STOCKEXPERT_NETWORK', 'BACKEND_PORT', 'FRONTEND_PORT', 'MINIO_CONSOLE_PORT'):
            self.env.pop(name, None)

    def run_launcher(self, *args):
        return subprocess.run(['bash', str(self.root / 'b1.sh'), *args], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def test_active_jobs_block_restart_and_preserve_mode(self):
        (self.root / '.b1-mode').write_text('shared\n')
        self.env['B1_TEST_ACTIVE'] = '1'
        result = self.run_launcher('start', 'split')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(' up ', (self.root / 'docker.log').read_text())
        self.assertEqual((self.root / '.b1-mode').read_text(), 'shared\n')

    def test_idle_start_selects_isolated_project_and_split(self):
        result = self.run_launcher('start', 'split')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'docker.log').read_text()
        self.assertIn('--project-name stockexpert-b1 ', calls)
        self.assertIn('compose.b1.split.yml --profile backend-experiments up -d --build', calls)
        self.assertIn('--project-name stockexpert-b1-frontend ', calls)
        self.assertEqual((self.root / '.b1-mode').read_text(), 'split\n')

    def test_stop_does_not_delete_volumes(self):
        result = self.run_launcher('stop')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'docker.log').read_text()
        self.assertIn(' stop\n', calls)
        self.assertNotIn('down', calls)
        self.assertNotIn('--volumes', calls)

    def test_existing_backend_uses_user_selected_frontend_project(self):
        self.env['B1_PROJECT'] = 'stockexpert-backend'
        self.env['B1_FRONTEND_PROJECT'] = 'stockexpert-backend-frontend'
        result = self.run_launcher('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'docker.log').read_text()
        self.assertIn('--project-name stockexpert-backend ', calls)
        self.assertIn('--project-name stockexpert-frontend ', calls)
        self.assertNotIn('--project-name stockexpert-backend-frontend ', calls)


if __name__ == '__main__':
    unittest.main()
