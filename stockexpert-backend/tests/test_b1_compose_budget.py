"""Verify the deployable profiles keep a fair job-worker resource budget."""
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def merged_services(mode):
    base = yaml.safe_load((ROOT / 'stockexpert-backend/docker-compose.yml').read_text())
    overlay = yaml.safe_load((ROOT / f'compose.b1.{mode}.yml').read_text())
    services = deepcopy(base['services'])
    for name, update in overlay['services'].items():
        for key, value in update.items():
            if isinstance(value, dict):
                services[name][key] = {**services[name].get(key, {}), **value}
            else:
                services[name][key] = value
    return services


def queue(service):
    command = service['command']
    return command[command.index('-Q') + 1]


def test_modes_have_identical_execution_slots_cpu_memory_and_inference_budget():
    shared, split = merged_services('shared'), merged_services('split')
    for name in ('worker', 'worker-batch'):
        for key in ('cpus', 'mem_limit', 'volumes', 'depends_on'):
            assert shared[name][key] == split[name][key]
        for mode in (shared, split):
            assert '--concurrency=1' in mode[name]['command']
            assert '--pool=solo' in mode[name]['command']
            assert '--prefetch-multiplier=1' in mode[name]['command']
            assert mode[name]['environment']['OLLAMA_LLM_CONCURRENCY'] == '1'
    assert sum(float(shared[name]['cpus']) for name in ('worker', 'worker-batch')) == 2
    assert [queue(shared[name]) for name in ('worker', 'worker-batch')] == ['shared', 'shared']
    assert [queue(split[name]) for name in ('worker', 'worker-batch')] == ['interactive', 'batch']
    assert shared['ollama']['environment'] == split['ollama']['environment']
    assert shared['ollama']['environment']['OLLAMA_NUM_PARALLEL'] == '1'
    assert shared['ollama']['environment']['OLLAMA_MAX_LOADED_MODELS'] == '1'


def test_dispatch_and_experiment_workers_are_independent_in_both_modes():
    for mode in ('shared', 'split'):
        services = merged_services(mode)
        for service, expected in [('worker-control', 'control'), ('worker-laboratory', 'laboratory'),
                                  ('worker-regex', 'regex_experiments'), ('worker-b1', 'b1_experiments')]:
            assert queue(services[service]) == expected
            assert services[service]['environment']['JOB_ROUTING_MODE'] == mode
        assert services['worker-b1']['profiles'] == ['backend-experiments']
        assert services['backend']['volumes'] == services['worker-b1']['volumes']
        assert services['backend']['environment']['JOB_ROUTING_MODE'] == mode
        assert services['beat']['environment']['JOB_ROUTING_MODE'] == mode
