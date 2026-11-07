from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import job_telemetry as telemetry


def test_lifecycle_separates_acceptance_wait_and_completion():
    start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    job = SimpleNamespace(created_at=start, kind='normalize', params={})
    telemetry.published(job, 'batch', 'split', start + timedelta(seconds=3))
    telemetry.started(job, start + timedelta(seconds=8), queue='batch')
    job.params = {**job.params, 'normalization_counts': {'residual': 20, 'requirement': 4}}
    telemetry.finished(job, 'COMPLETED', start + timedelta(seconds=20))
    metrics = telemetry.snapshot(job)
    assert metrics['registration_to_publish_seconds'] == 3
    assert metrics['queue_wait_seconds'] == 5
    assert metrics['registration_to_start_seconds'] == 8
    assert metrics['total_seconds'] == 20
    assert metrics['processing_seconds'] == 12
    assert metrics['rows_processed'] == 24
    assert metrics['rows_per_second'] == 2


def test_retry_preserves_first_start_and_does_not_mark_job_completed():
    start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    job = SimpleNamespace(created_at=start, kind='match', params={'query_timings_ms': [7.5]})
    telemetry.published(job, 'shared', 'shared', start)
    telemetry.started(job, start + timedelta(seconds=1))
    telemetry.finished(job, 'QUEUED', start + timedelta(seconds=2), terminal=False)
    assert 'finished_at' not in job.params['execution_metrics']
    telemetry.started(job, start + timedelta(seconds=11))
    telemetry.finished(job, 'COMPLETED', start + timedelta(seconds=21))
    metrics = telemetry.snapshot(job)
    assert metrics['attempt_count'] == 2
    assert metrics['first_started_at'] == (start + timedelta(seconds=1)).isoformat()
    assert metrics['rows_processed'] == 1
    assert metrics['row_unit'] == 'requirements_matched'
    assert metrics['rows_per_second'] == pytest.approx(0.05)


def test_unknown_row_unit_is_not_invented_and_full_keeps_original_kind():
    start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    job = SimpleNamespace(created_at=start, kind='taxonomy', params={})
    telemetry.started(job, start)
    assert telemetry.snapshot(job, start + timedelta(seconds=1))['rows_processed'] is None
    job = SimpleNamespace(created_at=start, kind='full', params={'normalization_counts': {'requirement': 2}})
    telemetry.started(job, start)
    job.kind = 'normalize'
    assert telemetry.snapshot(job, start + timedelta(seconds=1))['row_unit'] == 'passports_normalized'


def test_manual_retry_clears_old_terminal_timestamp():
    start = datetime(2026, 11, 7, tzinfo=timezone.utc)
    job = SimpleNamespace(created_at=start, kind='match', params={})
    telemetry.started(job, start)
    telemetry.finished(job, 'FAILED', start + timedelta(seconds=1))
    telemetry.started(job, start + timedelta(seconds=5))
    assert 'finished_at' not in job.params['execution_metrics']
    assert 'outcome' not in job.params['execution_metrics']
