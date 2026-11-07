"""No Docker daemon needed: record profile switching against existing resources."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ParallelLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        (self.root / 'scripts').mkdir()
        for name in ('b1-parallel.sh', 'scripts/b1_parallel_runtime.py'):
            shutil.copy(source / name, self.root / name)
        (self.root / 'stockexpert-backend').mkdir()
        (self.root / 'stockexpert-backend/.env').write_text('SECRET_KEY=fixture-not-production\n')
        (self.root / 'b1-local.sh').write_text('unchanged-local-launcher\n')
        bindir = self.root / 'bin'
        bindir.mkdir()
        stub = bindir / 'docker'
        stub.write_text('''#!/usr/bin/env python3
import json,os,sys
a=sys.argv[1:]
with open(os.environ['PARALLEL_LOG'],'a') as f: f.write(json.dumps({'args':a,'concurrency':os.getenv('B1_INTERACTIVE_CONCURRENCY')})+'\\n')
if a[0]=='ps':
 service=next(x.split('=',2)[-1] for x in a if x.startswith('label=com.docker.compose.service='))
 if service!=os.getenv('MISSING_SERVICE'): print(service+'-id')
elif a[0]=='inspect':
 ident=a[-1]
 if '.NetworkSettings' in a[2]:
  port='80/tcp' if ident=='frontend-id' else '9001/tcp' if ident=='minio-id' else '8000/tcp'
  host='5176' if ident=='frontend-id' else '9003' if ident=='minio-id' else '8003'
  network='different-network' if ident==os.getenv('WRONG_NETWORK') else 'existing-stock-network'
  print(json.dumps({'Networks':{network:{}},'Ports':{port:[{'HostIp':'127.0.0.1','HostPort':host}]}}))
 elif '.Mounts' in a[2]:
  print(json.dumps([{'Destination':'/models/huggingface','Type':'volume','Name':'retained-model-cache'}, {'Destination':'/data/b1','Type':'volume','Name':'retained-b1-artifacts'}]))
 elif '.Config.Env' in a[2]:
  print(json.dumps(['JOB_ROUTING_MODE=shared','B1_RUNTIME_PROFILE=standard','SECRET_KEY=must-not-appear-in-state']))
elif a[0]=='exec': print(os.getenv('ACTIVE_JOBS','0'))
if os.getenv('FAIL_BUILD') and 'build' in a: sys.exit(1)
''')
        stub.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
                    'PARALLEL_LOG': str(self.root / 'docker.jsonl')}
        for name in ('B1_PROJECT', 'B1_FRONTEND_PROJECT', 'STOCKEXPERT_NETWORK'):
            self.env.pop(name, None)

    def launch(self, *args):
        return subprocess.run(['bash', str(self.root / 'b1-parallel.sh'), *args], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def calls(self):
        path = self.root / 'docker.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_gpu_start_reuses_network_ports_volumes_and_only_recreates_apps(self):
        result = self.launch('start', 'gpu', '10')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        ups = [c for c in calls if 'up' in c['args']]
        self.assertEqual(len(ups), 2)
        for c in ups:
            a = c['args']
            self.assertIn('--no-deps', a)
            self.assertIn('--no-build', a)
            self.assertIn('--wait', a)
            self.assertFalse(set(('postgres', 'redis', 'minio', 'qdrant', 'ollama')) & set(a))
            self.assertNotIn('--remove-orphans', a)
            self.assertNotIn('down', a)
        back = ups[0]
        self.assertIn('stockexpert-backend', back['args'])
        self.assertIn(str(self.root / 'compose.b1.gpu.yml'), back['args'])
        self.assertEqual(back['concurrency'], '10')
        override = json.loads((self.root / '.b1-parallel-existing.json').read_text())
        self.assertEqual(override['networks']['default'], {'name':'existing-stock-network','external':True})
        self.assertEqual(override['volumes']['model_cache'], {'name':'retained-model-cache','external':True})
        state = json.loads((self.root / '.b1-parallel.json').read_text())
        self.assertEqual((state['backend_port'], state['frontend_port']), ('8003', '5176'))
        self.assertEqual(state['transition'], 'ready')
        self.assertNotIn('must-not-appear-in-state', (self.root / '.b1-parallel.json').read_text())
        self.assertEqual((self.root / 'b1-local.sh').read_text(), 'unchanged-local-launcher\n')
        self.assertEqual((self.root / 'stockexpert-backend/.env').read_text(), 'SECRET_KEY=fixture-not-production\n')
        guards = [c for c in calls if c['args'][0] == 'exec']
        self.assertEqual(len(guards), 2)
        self.assertIn('NormalizationExperiment', guards[0]['args'][-1])
        self.assertIn('active_runs', guards[0]['args'][-1])

    def test_active_jobs_abort_before_build_or_up(self):
        self.env['ACTIVE_JOBS'] = '2'
        result = self.launch('start', 'gpu', '4')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Активные задания/эксперименты: 2', result.stderr)
        self.assertFalse(any('build' in c['args'] or 'up' in c['args'] for c in self.calls()))
        self.assertFalse((self.root / '.b1-parallel.json').exists())

    def test_status_and_default_restart_preserve_saved_gpu_profile(self):
        self.assertEqual(self.launch('start', 'gpu', '3').returncode, 0)
        (self.root / 'docker.jsonl').unlink()
        self.assertEqual(self.launch('status').returncode, 0)
        self.assertTrue(any(str(self.root / 'compose.b1.gpu.yml') in c['args'] for c in self.calls()))
        self.assertFalse(any('up' in c['args'] or 'build' in c['args'] for c in self.calls()))
        self.assertEqual(self.launch('start').returncode, 0)
        state = json.loads((self.root / '.b1-parallel.json').read_text())
        self.assertEqual((state['hardware'], state['concurrency']), ('gpu', 3))

    def test_restore_omits_parallel_and_gpu_overrides_and_sets_standard(self):
        self.assertEqual(self.launch('start', 'gpu', '4').returncode, 0)
        (self.root / 'docker.jsonl').unlink()
        result = self.launch('restore', 'shared')
        self.assertEqual(result.returncode, 0, result.stderr)
        ups = [c['args'] for c in self.calls() if 'up' in c['args']]
        self.assertIn(str(self.root / 'compose.b1.shared.yml'), ups[0])
        self.assertNotIn(str(self.root / 'compose.b1.parallel.yml'), ups[0])
        self.assertNotIn(str(self.root / 'compose.b1.gpu.yml'), ups[0])
        state = json.loads((self.root / '.b1-parallel.json').read_text())
        self.assertEqual((state['profile'], state['mode']), ('standard', 'shared'))
        override = json.loads((self.root / '.b1-parallel-existing.json').read_text())
        self.assertTrue(all(s['environment']['B1_RUNTIME_PROFILE'] == 'standard' for s in override['services'].values()))

    def test_missing_infrastructure_or_wrong_network_never_builds(self):
        for variable, value in [('MISSING_SERVICE', 'postgres'), ('WRONG_NETWORK', 'redis-id')]:
            with self.subTest(variable=variable):
                self.env[variable] = value
                result = self.launch('start', 'cpu', '1')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any('up' in c['args'] or 'build' in c['args'] for c in self.calls()))
                self.env.pop(variable)

    def test_invalid_concurrency_rejected_before_docker(self):
        self.assertNotEqual(self.launch('start', 'cpu', '11').returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_single_slot_cpu_recovery_profile_is_supported(self):
        result = self.launch('start', 'cpu', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.root / '.b1-parallel.json').read_text())
        self.assertEqual((state['hardware'], state['concurrency']), ('cpu', 1))
        ups = [c for c in self.calls() if 'up' in c['args']]
        self.assertNotIn(str(self.root / 'compose.b1.gpu.yml'), ups[0]['args'])

    def test_gpu_check_does_not_load_another_reranker_model(self):
        self.assertEqual(self.launch('start', 'gpu', '4').returncode, 0)
        (self.root / 'docker.jsonl').unlink()
        result = self.launch('gpu-check')
        self.assertEqual(result.returncode, 0, result.stderr)
        script = self.calls()[-1]['args'][-1]
        self.assertIn('torch.cuda.is_available', script)
        self.assertNotIn('CrossEncoder', script)
        self.assertFalse(any('up' in c['args'] or 'build' in c['args'] for c in self.calls()))

    def test_build_failure_does_not_stop_or_recreate_apps(self):
        self.env['FAIL_BUILD'] = '1'
        result = self.launch('start', 'gpu', '4')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in c['args'] or 'stop' in c['args'] for c in self.calls()))
        self.assertFalse((self.root / '.b1-parallel.json').exists())


if __name__ == '__main__':
    unittest.main()
