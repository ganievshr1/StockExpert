"""One lazily loaded reranker per process, shared safely by worker threads.

Only model inference is accelerated. Queuing, SQL, parsing and BM25 remain on
the CPU. GPU OOM retries lower the batch size, then retain a CPU fallback until
the worker restarts. This avoids repeatedly filling an already busy GPU.
"""
from __future__ import annotations

import gc
import logging
from contextvars import ContextVar
from threading import RLock

from app.core.config import settings

logger = logging.getLogger(__name__)
_runtime = None
_singleton_lock = RLock()
_job_usage = ContextVar('reranker_job_usage', default=None)


def reset_job_usage():
    _job_usage.set({'calls': 0, 'cpu_calls': 0, 'cuda_calls': 0})


def job_usage():
    return dict(_job_usage.get() or {'calls': 0, 'cpu_calls': 0, 'cuda_calls': 0})


def _record_usage(device, batch_size, fallback_reason):
    value = job_usage()
    value['calls'] += 1
    value[f'{device}_calls'] += 1
    value.update(last_device=device, effective_batch_size=batch_size,
                 fallback_reason=fallback_reason)
    _job_usage.set(value)


class RerankerRuntime:
    def __init__(self, model_name, requested_device='cpu', batch_size=8,
                 cpu_fallback=True, model_factory=None, torch_module=None):
        self.model_name = model_name
        self.requested_device = requested_device
        self.batch_size = batch_size
        self.cpu_fallback = cpu_fallback
        self.model_factory = model_factory
        self.torch = torch_module
        self.model = None
        self.device = None
        self.fallback_reason = None
        self.lock = RLock()

    def snapshot(self):
        # Read-only observability must not load weights or initialize CUDA.
        return dict(loaded=self.model is not None, active_device=self.device,
                    effective_batch_size=self.batch_size,
                    fallback_reason=self.fallback_reason)

    def _empty_cuda_cache(self):
        gc.collect()
        self.torch.cuda.empty_cache()

    def _is_cuda_oom(self, exc):
        oom_type = getattr(self.torch.cuda, 'OutOfMemoryError', ())
        return isinstance(exc, oom_type) or (
            isinstance(exc, RuntimeError) and 'cuda' in str(exc).lower()
            and 'out of memory' in str(exc).lower())

    def _fallback(self, reason):
        self.model.to('cpu')
        self.device = 'cpu'
        self.fallback_reason = reason
        self._empty_cuda_cache()
        logger.warning('RERANKER_CPU_FALLBACK reason=%s; retained until worker restart', reason)

    def _load_locked(self):
        if self.model is not None:
            return
        if self.torch is None:
            import torch
            self.torch = torch
        factory = self.model_factory
        if factory is None:
            from sentence_transformers import CrossEncoder
            factory = CrossEncoder
        wants_cuda = self.requested_device in ('auto', 'cuda')
        cuda_available = bool(wants_cuda and self.torch.cuda.is_available())
        if self.requested_device == 'cuda' and not cuda_available and not self.cpu_fallback:
            raise RuntimeError('RERANKER_DEVICE=cuda requested but CUDA is unavailable')
        if wants_cuda and not cuda_available:
            self.fallback_reason = 'cuda_unavailable'
            logger.warning('RERANKER_CPU_FALLBACK reason=cuda_unavailable requested=%s', self.requested_device)
        # Load on CPU first so a failed CUDA transfer still has one recoverable
        # model, instead of loading a second CPU copy after a constructor OOM.
        model = factory(self.model_name, device='cpu', max_length=512,
                        trust_remote_code=False)
        self.model, self.device = model, 'cpu'
        if cuda_available:
            try:
                model.to('cuda')
                self.device = 'cuda'
            except RuntimeError as exc:
                if not self._is_cuda_oom(exc) or not self.cpu_fallback:
                    # Do not silently keep a partly moved model after failure.
                    self.model, self.device = None, None
                    raise
                exc.__traceback__ = None
                self._fallback('cuda_oom_loading_model')
        logger.info('RERANKER_READY requested=%s actual=%s batch=%s model=%s',
                    self.requested_device, self.device, self.batch_size, self.model_name)

    def load(self):
        with self.lock:
            self._load_locked()
        return self

    def predict(self, pairs, **kwargs):
        # Concurrent requests can progress through network/SQL independently,
        # while a single model owns GPU memory and the tokenizer at a time.
        with self.lock:
            self._load_locked()
            while True:
                try:
                    scores = self.model.predict(pairs, batch_size=self.batch_size,
                                                show_progress_bar=False, **kwargs)
                except RuntimeError as exc:
                    if self.device != 'cuda' or not self._is_cuda_oom(exc):
                        raise
                    exc.__traceback__ = None
                    if self.batch_size > 1:
                        self.batch_size = max(1, self.batch_size // 2)
                        self._empty_cuda_cache()
                        logger.warning('RERANKER_CUDA_OOM retry_batch=%s', self.batch_size)
                        continue
                    if not self.cpu_fallback:
                        raise
                    self._fallback('cuda_oom_batch_1')
                    continue
                if len(scores) != len(pairs):
                    raise ValueError('Reranker returned an unexpected number of scores')
                _record_usage(self.device, self.batch_size, self.fallback_reason)
                return scores


def get_runtime():
    global _runtime
    with _singleton_lock:
        if _runtime is None:
            _runtime = RerankerRuntime(settings.RERANKER_MODEL,
                settings.RERANKER_DEVICE, settings.RERANKER_BATCH_SIZE,
                settings.RERANKER_CPU_FALLBACK)
        return _runtime


def runtime_config():
    value = dict(enabled=settings.RERANKER_ENABLED, model=settings.RERANKER_MODEL,
                 requested_device=settings.RERANKER_DEVICE,
                 configured_batch_size=settings.RERANKER_BATCH_SIZE,
                 cpu_fallback=settings.RERANKER_CPU_FALLBACK)
    with _singleton_lock:
        value.update(_runtime.snapshot() if _runtime is not None else dict(
            loaded=False, active_device=None, effective_batch_size=None, fallback_reason=None))
    return value
