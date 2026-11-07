"""Deployment-selected routing for real jobs; API requests cannot select a queue.

The two B1 configurations use the same two solo execution slots. Only their
subscriptions change: shared/shared versus interactive/batch. Administrative
dispatch and experiments have independent queues in both configurations.
"""
from app.core.config import settings

SHARED_QUEUE = 'shared'
INTERACTIVE_QUEUE = 'interactive'
BATCH_QUEUE = 'batch'
CONTROL_QUEUE = 'control'
LABORATORY_QUEUE = 'laboratory'
REGEX_QUEUE = 'regex_experiments'
B1_QUEUE = 'b1_experiments'

TASK_ROUTES = {
    'stockexpert.dispatch': {'queue': CONTROL_QUEUE},
    'stockexpert.regex_experiment': {'queue': REGEX_QUEUE},
    'stockexpert.laboratory': {'queue': LABORATORY_QUEUE},
    'stockexpert.b1_experiment': {'queue': B1_QUEUE},
}


def queue_for_job(kind, base_session_id=None, mode=None):
    mode = mode or settings.JOB_ROUTING_MODE
    if mode not in ('shared', 'split'):
        raise ValueError('JOB_ROUTING_MODE must be shared or split')
    if mode == 'shared':
        return SHARED_QUEUE
    # A full run against an attached prepared base only normalizes the new
    # requirement before matching. A full original-catalog run remains batch.
    if kind == 'match' or (kind == 'full' and base_session_id):
        return INTERACTIVE_QUEUE
    return BATCH_QUEUE


def prepared_base_id(session):
    snapshot = session.base_snapshot or {}
    return snapshot.get('source_session_id') if snapshot.get('reused_passports') else None


def routing_metadata():
    return {
        'mode': settings.JOB_ROUTING_MODE,
        'job_slots': 2,
        'allocation': {'shared': 2} if settings.JOB_ROUTING_MODE == 'shared' else {'interactive': 1, 'batch': 1},
        'prefetch_multiplier': 1,
        'dispatch_interval_seconds': settings.DISPATCH_INTERVAL_SECONDS,
        'worker_pool': 'solo',
        'control_queue': CONTROL_QUEUE,
        'laboratory_queue': LABORATORY_QUEUE,
        'gpu_policy': 'one Ollama instance; NUM_PARALLEL=1; MAX_LOADED_MODELS=1; reranker=cpu',
        'budget_source': 'provided compose files; verify external deployments separately',
    }
