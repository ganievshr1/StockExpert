"""Exercise device selection, bounded retries and thread safety without GPU/models."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

import pytest

from app.services import reranker_runtime as rr


class FakeOOM(RuntimeError):
    pass


class FakeCuda:
    OutOfMemoryError = FakeOOM

    def __init__(self, available=True):
        self.available = available
        self.emptied = 0

    def is_available(self):
        return self.available

    def empty_cache(self):
        self.emptied += 1


class Factory:
    def __init__(self, gpu_max_batch=64, transfer_oom=False, other_error=False):
        self.loaded = 0
        self.gpu_max_batch = gpu_max_batch
        self.transfer_oom = transfer_oom
        self.other_error = other_error
        self.moves = []
        self.calls = []
        self.active = self.max_active = 0
        self.counter_lock = Lock()

    def __call__(self, *args, **kwargs):
        self.loaded += 1
        assert kwargs == dict(device='cpu', max_length=512, trust_remote_code=False)
        self.device = 'cpu'
        return self

    def to(self, device):
        self.moves.append(device)
        if device == 'cuda' and self.transfer_oom:
            raise FakeOOM('cuda transfer OOM')
        self.device = device

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append((self.device, batch_size))
        if self.other_error:
            raise RuntimeError('invalid tokenizer input')
        if self.device == 'cuda' and batch_size > self.gpu_max_batch:
            raise FakeOOM('cuda allocation OOM')
        with self.counter_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        sleep(.005)
        with self.counter_lock:
            self.active -= 1
        return [float(i) / 10 for i, _ in enumerate(pairs)]


def runtime(device='auto', available=True, **kwargs):
    factory = Factory(**kwargs)
    cuda = FakeCuda(available)
    instance = rr.RerankerRuntime('test-model', requested_device=device,
        model_factory=factory, torch_module=SimpleNamespace(cuda=cuda))
    return instance, factory, cuda


def test_auto_selects_gpu_when_available_and_records_actual_device():
    instance, factory, _ = runtime()
    rr.reset_job_usage()
    assert instance.predict([('q', 'a'), ('q', 'b')]) == [0.0, .1]
    assert factory.loaded == 1
    assert factory.calls == [('cuda', 8)]
    assert rr.job_usage()['cuda_calls'] == 1
    assert instance.snapshot()['active_device'] == 'cuda'


def test_cpu_default_never_moves_model_to_gpu():
    instance, factory, _ = runtime(device='cpu')
    instance.predict([('q', 'a')])
    assert factory.moves == []
    assert instance.device == 'cpu'


def test_auto_without_cuda_is_observable_cpu_fallback(caplog):
    instance, factory, _ = runtime(available=False)
    instance.predict([('q', 'a')])
    assert factory.calls == [('cpu', 8)]
    assert instance.fallback_reason == 'cuda_unavailable'
    assert 'cuda_unavailable' in caplog.text


def test_explicit_cuda_can_require_gpu_instead_of_silently_falling_back():
    instance, factory, _ = runtime(device='cuda', available=False)
    instance.cpu_fallback = False
    with pytest.raises(RuntimeError, match='CUDA is unavailable'):
        instance.load()
    assert factory.loaded == 0


def test_gpu_oom_halves_batch_and_retains_working_size():
    instance, factory, cuda = runtime(gpu_max_batch=2)
    instance.predict([('q', str(i)) for i in range(10)])
    instance.predict([('q', 'next')])
    assert factory.calls == [('cuda', 8), ('cuda', 4), ('cuda', 2), ('cuda', 2)]
    assert cuda.emptied == 2
    assert instance.device == 'cuda'
    assert factory.loaded == 1


def test_gpu_oom_at_one_falls_back_to_same_model_and_stays_on_cpu():
    instance, factory, _ = runtime(gpu_max_batch=0)
    rr.reset_job_usage()
    assert instance.predict([('q', 'a')]) == [0.0]
    assert instance.predict([('q', 'b')]) == [0.0]
    assert factory.calls == [('cuda', 8), ('cuda', 4), ('cuda', 2), ('cuda', 1), ('cpu', 1), ('cpu', 1)]
    assert factory.loaded == 1
    assert factory.moves == ['cuda', 'cpu']
    assert rr.job_usage()['cpu_calls'] == 2
    assert rr.job_usage()['cuda_calls'] == 0
    assert instance.fallback_reason == 'cuda_oom_batch_1'


def test_loading_cuda_oom_recovers_existing_model_without_second_load():
    instance, factory, _ = runtime(transfer_oom=True)
    instance.predict([('q', 'a')])
    assert factory.loaded == 1
    assert factory.calls == [('cpu', 8)]
    assert instance.fallback_reason == 'cuda_oom_loading_model'


def test_non_memory_errors_are_not_hidden_as_gpu_fallback():
    instance, factory, _ = runtime(other_error=True)
    with pytest.raises(RuntimeError, match='tokenizer'):
        instance.predict([('q', 'a')])
    assert factory.calls == [('cuda', 8)]
    assert instance.fallback_reason is None


def test_strict_cuda_oom_raises_after_bounded_retries():
    instance, factory, _ = runtime(gpu_max_batch=0)
    instance.cpu_fallback = False
    with pytest.raises(FakeOOM):
        instance.predict([('q', 'a')])
    assert factory.calls == [('cuda', 8), ('cuda', 4), ('cuda', 2), ('cuda', 1)]


def test_ten_threads_share_one_model_and_do_not_overlap_gpu_inference():
    instance, factory, _ = runtime()
    barrier = Barrier(10)

    def call(_):
        rr.reset_job_usage()
        barrier.wait(timeout=5)
        result = instance.predict([('q', 'a')])
        return result, rr.job_usage()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(call, range(10)))
    assert factory.loaded == 1
    assert factory.max_active == 1
    assert all(scores == [0.0] and usage['cuda_calls'] == 1 for scores, usage in results)


def test_runtime_snapshot_does_not_initialize_model(monkeypatch):
    monkeypatch.setattr(rr, '_runtime', None)
    config = rr.runtime_config()
    assert config['loaded'] is False
    assert config['active_device'] is None
    assert rr._runtime is None


def test_job_telemetry_preserves_existing_runtime_and_no_model_load(monkeypatch):
    from app.tasks import record_reranker_runtime
    monkeypatch.setattr(rr, '_runtime', None)
    rr.reset_job_usage()
    job = SimpleNamespace(params={'existing': True, 'runtime': {'provider': 'regex'}})
    record_reranker_runtime(job)
    assert job.params['existing'] is True
    assert job.params['runtime']['provider'] == 'regex'
    assert job.params['runtime']['reranker_execution']['loaded'] is False
    assert job.params['runtime']['reranker_execution']['job_usage']['calls'] == 0
    assert rr._runtime is None
