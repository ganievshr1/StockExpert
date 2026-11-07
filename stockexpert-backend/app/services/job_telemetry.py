"""Backward-compatible job timestamps stored in params, without schema changes.

created_at is durable registration time. dispatched_at remains a renewable
outbox lease and must never be presented as the immutable first publication.
All durations cover wall-clock time, not GPU time. Row counts are useful units
completed by the workflow; they do not count LLM calls or internal retries.
"""
from datetime import datetime, timezone

from app.db import now


def _iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


def _elapsed(start, end):
    if not start or not end:
        return None
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def _metrics(job):
    return {**(job.params or {}).get('execution_metrics', {}), 'registered_at': _iso(job.created_at)}


def _save(job, metrics):
    # Assign a new JSON object so SQLAlchemy detects the mutation.
    job.params = {**(job.params or {}), 'execution_metrics': metrics}


def published(job, queue, mode, timestamp=None):
    metrics = _metrics(job)
    stamp = _iso(timestamp or now())
    metrics.setdefault('first_published_at', stamp)
    metrics.update(last_published_at=stamp, queue=queue, routing_mode=mode,
                   publication_count=metrics.get('publication_count', 0) + 1)
    metrics['publication_scope'] = 'durable outbox publications; Celery internal retries excluded'
    metrics['registration_to_publish_seconds'] = _elapsed(metrics['registered_at'], metrics['first_published_at'])
    _save(job, metrics)


def started(job, timestamp=None, queue=None):
    metrics = _metrics(job)
    stamp = _iso(timestamp or now())
    metrics.setdefault('first_started_at', stamp)
    metrics.setdefault('original_kind', job.kind)
    metrics.update(last_started_at=stamp, attempt_count=metrics.get('attempt_count', 0) + 1)
    if queue:
        metrics['execution_queue'] = queue
    # A manual retry keeps first-run history but invalidates the old terminal
    # snapshot. The next finish updates the complete lifecycle measurement.
    for key in ('finished_at', 'completed_at', 'outcome', 'total_seconds', 'processing_seconds', 'rows_per_second'):
        metrics.pop(key, None)
    metrics['queue_wait_seconds'] = _elapsed(metrics.get('first_published_at'), metrics['first_started_at'])
    metrics['registration_to_start_seconds'] = _elapsed(metrics['registered_at'], metrics['first_started_at'])
    _save(job, metrics)


def row_progress(job):
    params = job.params or {}
    kind = params.get('execution_metrics', {}).get('original_kind', job.kind)
    if kind == 'match':
        return len(params.get('query_timings_ms', [])), 'requirements_matched'
    if kind in ('normalize', 'full'):
        return sum(int(value) for value in params.get('normalization_counts', {}).values()), 'passports_normalized'
    # No exact persisted progress counter exists for schema discovery/taxonomy.
    # Do not manufacture throughput from all rows already in the session.
    return None, None


def snapshot(job, timestamp=None):
    metrics = _metrics(job)
    rows, unit = row_progress(job)
    metrics.update(rows_processed=rows, row_unit=unit)
    end = metrics.get('finished_at') or _iso(timestamp or now())
    processing = _elapsed(metrics.get('first_started_at'), end)
    metrics['rows_per_second'] = rows / processing if rows is not None and processing and processing > 0 else None
    metrics['throughput_scope'] = 'useful rows / wall time since first start, including retries'
    return metrics


def finished(job, outcome, timestamp=None, terminal=True):
    metrics = snapshot(job, timestamp)
    stamp = _iso(timestamp or now())
    metrics['last_attempt_finished_at'] = stamp
    if terminal:
        metrics.update(finished_at=stamp, outcome=outcome)
        if outcome == 'COMPLETED':
            metrics['completed_at'] = stamp
        metrics['total_seconds'] = _elapsed(metrics['registered_at'], stamp)
        metrics['processing_seconds'] = _elapsed(metrics.get('first_started_at'), stamp)
    _save(job, metrics)
