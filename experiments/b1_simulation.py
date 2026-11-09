#!/usr/bin/env python3
"""Uncalibrated, reproducible B1 discrete-event model; never an API benchmark.

Run: python experiments/b1_simulation.py --self-test
     python experiments/b1_simulation.py --output-dir experiments/results/b1_simulation

Only Python's standard library is required. All durations are assumptions in
ModelConfig. A catalog occupies one worker throughout its row-by-row processing,
matching the current Celery job granularity. GPU access is FIFO and shared.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import random
import statistics
import sys
import unittest
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


DISCLAIMER = "DISCRETE_EVENT_SIMULATION_UNCALIBRATED_NOT_REAL_STOCKEXPERT_MEASUREMENTS"


@dataclass(frozen=True)
class ModelConfig:
    catalog_rows: int = 12000
    warmup_seconds: float = 180.0
    window_seconds: float = 1800.0
    arrival_interval_seconds: float = 3.0
    protocol_requests: int = 600
    accounts: int = 10
    deadline_seconds: float = 120.0
    polling_seconds: float = 0.25
    request_network_seconds: float = 0.01
    response_network_seconds: float = 0.01
    registration_seconds: float = 0.01
    interactive_cpu_seconds: float = 0.10
    interactive_gpu_seconds: float = 0.25
    batch_cpu_seconds_per_row: float = 0.02
    batch_gpu_seconds_per_row: float = 0.08
    service_time_cv: float = 0.20
    gpu_rows_per_call: int = 1
    catalog_progress_every_rows: int = 100
    minimum_successes_for_protocol_p95: int = 100

    def validate(self):
        if self.catalog_rows < 1 or self.accounts < 1 or self.protocol_requests < 1:
            raise ValueError("catalog_rows, accounts and protocol_requests must be positive")
        for name in ("window_seconds", "arrival_interval_seconds", "deadline_seconds",
                     "polling_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("warmup_seconds", "request_network_seconds", "response_network_seconds",
                     "registration_seconds", "interactive_cpu_seconds", "interactive_gpu_seconds",
                     "batch_cpu_seconds_per_row", "batch_gpu_seconds_per_row", "service_time_cv"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.gpu_rows_per_call < 1 or self.catalog_progress_every_rows < 1:
            raise ValueError("row chunk sizes must be positive")
        if (self.protocol_requests - 1) * self.arrival_interval_seconds >= self.window_seconds:
            raise ValueError("all protocol arrivals must fall inside the measurement window")


@dataclass
class Job:
    job_id: str
    kind: str
    owner: int | None
    phases: list[tuple[str, float, int]]
    measured: bool
    client_start: float | None = None
    enqueued: float | None = None
    worker_start: float | None = None
    server_finish: float | None = None
    client_finish: float | None = None
    terminal_time: float | None = None
    outcome: str = "not_sent"
    worker: int | None = None
    phase: int = 0
    gpu_wait_started: float | None = None
    gpu_wait_seconds: float = 0.0
    rows_completed: int = 0
    rows_in_window: int = 0


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * percentile) - 1)]


def assumed_duration(mean: float, cv: float, rng: random.Random) -> float:
    if mean == 0 or cv == 0:
        return mean
    sigma2 = math.log1p(cv * cv)
    return mean * rng.lognormvariate(-sigma2 / 2, math.sqrt(sigma2))


class Simulation:
    """Non-preemptive workers, FIFO broker queues and FIFO shared GPU slots.

    CPU is one virtual execution context per worker. The model omits actual HTTP,
    PostgreSQL, Redis, Qdrant, inference, permissions and memory pressure.
    """

    def __init__(self, config: ModelConfig, policy: str, workers: int, gpu_slots: int,
                 scenario: str, background: bool, seed: int, pair: int = 1):
        config.validate()
        if policy not in ("shared_fifo", "split"):
            raise ValueError("unknown queue policy")
        if workers < 2 or gpu_slots < 1:
            raise ValueError("at least 2 workers and 1 GPU slot are required")
        if scenario not in ("simultaneous_10", "sequential_10", "full_protocol"):
            raise ValueError("unknown scenario")
        self.c = config
        self.policy, self.workers, self.gpu_slots = policy, workers, gpu_slots
        self.scenario, self.background, self.seed, self.pair = scenario, background, seed, pair
        self.run_id = f"{scenario}_bg{int(background)}_w{workers}_g{gpu_slots}_p{pair}_{policy}"
        self.now = 0.0
        self.counter = 0
        self.events: list[tuple[float, int, str, object]] = []
        self.jobs: dict[str, Job] = {}
        self.ready = {"shared": deque(), "interactive": deque(), "batch": deque()}
        self.worker_jobs: list[str | None] = [None] * workers
        self.gpu_wait = deque()
        self.gpu_active = 0
        self.max_broker_queue = 0
        self.max_gpu_queue = 0
        self.progress: list[dict] = []
        self.window_start = config.warmup_seconds
        self.window_end = self.window_start + config.window_seconds
        self.observation_end = self.window_end + config.deadline_seconds
        self.window_snapshot = {}
        self.seq_next = 1
        self._prepare_workload()

    def event(self, at: float, name: str, payload=None):
        self.counter += 1
        heapq.heappush(self.events, (at, self.counter, name, payload))

    def _prepare_workload(self):
        # Work samples are generated before execution: paired policies get exactly
        # the same phases even if completion order changes.
        rng = random.Random(self.seed + 1_000_003)
        c = self.c
        for i in range(math.ceil(c.warmup_seconds / c.arrival_interval_seconds)):
            at = i * c.arrival_interval_seconds
            if at >= c.warmup_seconds:
                break
            job = Job(f"warmup_{i:04}", "interactive", i % c.accounts, [
                ("cpu", assumed_duration(c.interactive_cpu_seconds, c.service_time_cv, rng), 0),
                ("gpu", assumed_duration(c.interactive_gpu_seconds, c.service_time_cv, rng), 0),
            ], False)
            self.jobs[job.job_id] = job
            self.event(at, "client_start", job.job_id)
        # Background registration precedes simultaneous user submissions. Both
        # policies use this same tie-break rule and the same immutable search base.
        if self.background:
            batch_rng = random.Random(self.seed + 2_000_003)
            phases = []
            remaining = c.catalog_rows
            while remaining:
                rows = min(c.gpu_rows_per_call, remaining)
                phases.append(("cpu", assumed_duration(c.batch_cpu_seconds_per_row * rows,
                                                       c.service_time_cv, batch_rng), 0))
                phases.append(("gpu", assumed_duration(c.batch_gpu_seconds_per_row * rows,
                                                       c.service_time_cv, batch_rng), rows))
                remaining -= rows
            self.jobs["catalog_v1"] = Job("catalog_v1", "batch", None, phases, True)
            self.event(self.window_start, "batch_start", "catalog_v1")
        count = c.protocol_requests if self.scenario == "full_protocol" else 10
        rng = random.Random(self.seed)
        for i in range(count):
            owner = 0 if self.scenario == "sequential_10" else i % c.accounts
            job = Job(f"query_{i:04}", "interactive", owner, [
                ("cpu", assumed_duration(c.interactive_cpu_seconds, c.service_time_cv, rng), 0),
                ("gpu", assumed_duration(c.interactive_gpu_seconds, c.service_time_cv, rng), 0),
            ], True)
            self.jobs[job.job_id] = job
            if self.scenario == "full_protocol":
                self.event(self.window_start + i * c.arrival_interval_seconds,
                           "client_start", job.job_id)
            elif self.scenario == "simultaneous_10" or i == 0:
                self.event(self.window_start, "client_start", job.job_id)
        self.event(self.window_end, "window_end")

    def _enqueue(self, job: Job):
        job.enqueued = self.now
        key = "shared" if self.policy == "shared_fifo" else job.kind
        self.ready[key].append(job.job_id)
        if self.window_start <= self.now < self.window_end:
            self.max_broker_queue = max(self.max_broker_queue, sum(map(len, self.ready.values())))
        self._dispatch()

    def _dispatch(self):
        for worker, active in enumerate(self.worker_jobs):
            if active is not None:
                continue
            key = "shared" if self.policy == "shared_fifo" else ("interactive" if worker == 0 else "batch")
            if self.ready[key]:
                job = self.jobs[self.ready[key].popleft()]
                job.worker, job.worker_start = worker, self.now
                self.worker_jobs[worker] = job.job_id
                self._next_phase(job)

    def _next_phase(self, job: Job):
        if job.phase == len(job.phases):
            job.server_finish = self.now
            self.worker_jobs[job.worker] = None
            if job.kind == "interactive":
                elapsed = max(0, self.now - job.enqueued)
                polls = max(1, math.ceil((elapsed - 1e-10) / self.c.polling_seconds))
                observed = job.enqueued + polls * self.c.polling_seconds + self.c.response_network_seconds
                self.event(observed, "client_result", job.job_id)
            else:
                job.outcome = "server_completed"
                job.terminal_time = self.now
                self._progress(job, "catalog_finished")
            self._dispatch()
            return
        kind, duration, _ = job.phases[job.phase]
        if kind == "cpu":
            self.event(self.now + duration, "phase_done", (job.job_id, kind))
        else:
            job.gpu_wait_started = self.now
            self.gpu_wait.append(job.job_id)
            if self.window_start <= self.now < self.window_end:
                self.max_gpu_queue = max(self.max_gpu_queue, len(self.gpu_wait))
            self._dispatch_gpu()

    def _dispatch_gpu(self):
        while self.gpu_active < self.gpu_slots and self.gpu_wait:
            job = self.jobs[self.gpu_wait.popleft()]
            job.gpu_wait_seconds += self.now - job.gpu_wait_started
            job.gpu_wait_started = None
            self.gpu_active += 1
            self.event(self.now + job.phases[job.phase][1], "phase_done", (job.job_id, "gpu"))

    def _progress(self, job: Job, reason: str):
        self.progress.append({"run_id": self.run_id, "time_s": self.now,
                              "job_id": job.job_id, "rows_completed": job.rows_completed,
                              "rows_completed_in_window": job.rows_in_window, "reason": reason})

    def _client_terminal(self, job: Job, success: bool):
        if job.outcome in ("success", "deadline"):
            return
        job.outcome = "success" if success else "deadline"
        job.terminal_time = self.now
        if success:
            job.client_finish = self.now
        if job.measured and self.scenario == "sequential_10" and self.seq_next < 10:
            next_id = f"query_{self.seq_next:04}"
            self.seq_next += 1
            self.event(self.now, "client_start", next_id)

    def run(self):
        while self.events:
            at, _, name, payload = heapq.heappop(self.events)
            if at > self.observation_end:
                break
            self.now = at
            if name == "client_start":
                job = self.jobs[payload]
                job.client_start, job.outcome = at, "pending"
                self.event(at + self.c.request_network_seconds + self.c.registration_seconds,
                           "enqueue", payload)
                self.event(at + self.c.deadline_seconds, "client_deadline", payload)
            elif name == "batch_start":
                job = self.jobs[payload]
                job.client_start, job.outcome = at, "pending"
                self._enqueue(job)
            elif name == "enqueue":
                self._enqueue(self.jobs[payload])
            elif name == "phase_done":
                job_id, kind = payload
                job = self.jobs[job_id]
                if kind == "gpu":
                    self.gpu_active -= 1
                rows = job.phases[job.phase][2]
                if rows:
                    before = job.rows_completed
                    job.rows_completed += rows
                    if self.window_start <= at < self.window_end:
                        job.rows_in_window += rows
                    if before // self.c.catalog_progress_every_rows != job.rows_completed // self.c.catalog_progress_every_rows:
                        self._progress(job, "rows_completed")
                job.phase += 1
                # Existing GPU waiters precede a job's next GPU phase.
                self._dispatch_gpu()
                self._next_phase(job)
            elif name == "client_result":
                job = self.jobs[payload]
                # Equality with the deadline counts as an on-time response.
                if at <= job.client_start + self.c.deadline_seconds + 1e-10:
                    self._client_terminal(job, True)
            elif name == "client_deadline":
                job = self.jobs[payload]
                if job.outcome == "pending":
                    # A same-timestamp result event must be allowed to run first.
                    self.event(at + 1e-9, "deadline_finalize", payload)
            elif name == "deadline_finalize":
                self._client_terminal(self.jobs[payload], False)
            elif name == "window_end":
                self.window_snapshot = self._snapshot()
                if self.background:
                    self._progress(self.jobs["catalog_v1"], "measurement_window_end")
        self.now = self.observation_end
        return self.summary(), [self.job_record(j) for j in self.jobs.values() if j.measured], self.progress

    def _snapshot(self):
        return {"broker_queued": sum(map(len, self.ready.values())),
                "occupied_workers": sum(x is not None for x in self.worker_jobs),
                "gpu_waiting": len(self.gpu_wait), "gpu_active": self.gpu_active}

    def job_record(self, job: Job):
        cpu = sum(d for k, d, _ in job.phases if k == "cpu")
        gpu = sum(d for k, d, _ in job.phases if k == "gpu")
        queue_wait = None if job.worker_start is None else job.worker_start - job.enqueued
        e2e = None if job.client_finish is None else job.client_finish - job.client_start
        return {"run_id": self.run_id, "kind": job.kind, "job_id": job.job_id,
                "owner_id": job.owner, "search_catalog_version": "ready_v0" if job.kind == "interactive" else None,
                "prepared_catalog_version": "new_v1" if job.kind == "batch" else None,
                "client_start_s": job.client_start, "enqueued_s": job.enqueued,
                "worker_start_s": job.worker_start, "worker_id": job.worker,
                "server_finish_s": job.server_finish, "client_finish_s": job.client_finish,
                "terminal_time_s": job.terminal_time, "outcome": job.outcome,
                "client_time_until_terminal_s": None if job.terminal_time is None else job.terminal_time - job.client_start,
                "client_e2e_s": e2e, "broker_wait_s": queue_wait,
                "gpu_wait_s": job.gpu_wait_seconds,
                "assumed_cpu_service_s": cpu, "assumed_gpu_service_s": gpu,
                "total_assumed_service_s": cpu + gpu,
                "rows_completed": job.rows_completed, "rows_in_window": job.rows_in_window}

    def summary(self):
        queries = [j for j in self.jobs.values() if j.measured and j.kind == "interactive"]
        sent = [j for j in queries if j.client_start is not None]
        success = [j for j in sent if j.outcome == "success"]
        e2e = [j.client_finish - j.client_start for j in success]
        in_window = [j for j in success if self.window_start <= j.client_finish < self.window_end]
        rows = self.jobs["catalog_v1"].rows_in_window if self.background else 0
        failures = sum(j.outcome != "success" for j in sent)
        terminal = [j.terminal_time for j in sent if j.terminal_time is not None]
        batch_finish = self.jobs["catalog_v1"].server_finish if self.background else None
        return {"disclaimer": DISCLAIMER, "run_id": self.run_id, "scenario": self.scenario,
                "background": self.background, "pair": self.pair, "seed": self.seed,
                "policy": self.policy, "workers_total": self.workers,
                "interactive_workers": 1 if self.policy == "split" else None,
                "batch_workers": self.workers - 1 if self.policy == "split" else None,
                "gpu_slots": self.gpu_slots, "planned_queries": len(queries),
                "sent_queries": len(sent), "successes": len(success), "failures": failures,
                "deadline_errors": sum(j.outcome == "deadline" for j in sent),
                "error_rate": failures / len(sent) if sent else None,
                "p50_client_e2e_s": nearest_rank(e2e, 0.50),
                "p95_client_e2e_s": nearest_rank(e2e, 0.95),
                "mean_client_e2e_s": statistics.mean(e2e) if e2e else None,
                "p95_broker_wait_s": nearest_rank([j.worker_start - j.enqueued for j in success], 0.95),
                "p95_gpu_wait_s": nearest_rank([j.gpu_wait_seconds for j in success], 0.95),
                "completed_queries_in_window": len(in_window),
                "interactive_throughput_per_min": len(in_window) * 60 / self.c.window_seconds,
                "catalog_rows_in_window": rows,
                "catalog_throughput_rows_per_min": rows * 60 / self.c.window_seconds,
                "catalog_elapsed_s": None if batch_finish is None else batch_finish - self.window_start,
                "background_active_entire_window": bool(self.background and (batch_finish is None or batch_finish >= self.window_end)),
                "ten_query_client_makespan_s": max(terminal) - self.window_start if terminal and len(queries) == 10 else None,
                "max_broker_queue": self.max_broker_queue, "max_gpu_queue": self.max_gpu_queue,
                "window_start_s": self.window_start, "window_end_s": self.window_end,
                "observation_end_s": self.observation_end,
                "state_at_window_end": self.window_snapshot,
                "state_at_observation_end": self._snapshot(),
                "generator_schedule_misses": 0,
                "generator_note": "virtual scheduler; no claim about real load-generator capacity"}


def compare_pair(b: dict, v: dict, config: ModelConfig) -> dict:
    p95_ratio = v["p95_client_e2e_s"] / b["p95_client_e2e_s"] if b["p95_client_e2e_s"] and v["p95_client_e2e_s"] is not None else None
    batch_ratio = v["catalog_throughput_rows_per_min"] / b["catalog_throughput_rows_per_min"] if b["catalog_throughput_rows_per_min"] else None
    applies = b["scenario"] == "full_protocol" and b["background"]
    enough = b["successes"] >= config.minimum_successes_for_protocol_p95 and v["successes"] >= config.minimum_successes_for_protocol_p95
    continuous_background = b["background_active_entire_window"] and v["background_active_entire_window"]
    # Conservative protection against reporting survivor-only latency as an
    # improvement when the baseline has already timed out many users.
    uncensored_comparison = b["error_rate"] is not None and b["error_rate"] < 0.01
    ratio_ok = p95_ratio is not None and p95_ratio <= 0.70
    speed_ok = batch_ratio is not None and batch_ratio >= 0.90
    errors_ok = v["error_rate"] is not None and v["error_rate"] < 0.01
    if not applies:
        verdict = "CONTROL_OR_EXPLORATORY_NOT_A_B1_CONFIRMATION"
    elif not continuous_background:
        verdict = "INVALID_B1_BACKGROUND_COMPLETED_EARLY"
    elif not enough or not uncensored_comparison or batch_ratio is None:
        verdict = "NOT_EVALUABLE_IN_MODEL_BASELINE_CENSORING_OR_INSUFFICIENT_COMPLETIONS"
    elif ratio_ok and speed_ok and errors_ok:
        verdict = "B1_SUPPORTED_ONLY_WITHIN_THIS_UNCALIBRATED_MODEL"
    else:
        verdict = "B1_NOT_SUPPORTED_WITHIN_THIS_UNCALIBRATED_MODEL"
    return {"scenario": b["scenario"], "background": b["background"],
            "workers_total": b["workers_total"], "gpu_slots": b["gpu_slots"],
            "pair": b["pair"], "seed": b["seed"], "baseline_run_id": b["run_id"],
            "variant_run_id": v["run_id"], "baseline_p95_s": b["p95_client_e2e_s"],
            "variant_p95_s": v["p95_client_e2e_s"], "p95_ratio": p95_ratio,
            "baseline_error_rate": b["error_rate"], "variant_error_rate": v["error_rate"],
            "baseline_catalog_rows_per_min": b["catalog_throughput_rows_per_min"],
            "variant_catalog_rows_per_min": v["catalog_throughput_rows_per_min"],
            "catalog_speed_ratio": batch_ratio, "p95_30_percent_reduction": ratio_ok,
            "catalog_at_least_90_percent": speed_ok, "variant_errors_below_1_percent": errors_ok,
            "baseline_not_censored_by_1_percent_errors": uncensored_comparison,
            "background_active_entire_window_both": continuous_background,
            "enough_successes": enough, "verdict": verdict}


def write_csv(path: Path, records: list[dict]):
    if not records:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        for record in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
                             for k, v in record.items()})


def execute_suite(config: ModelConfig, output: Path, workers_list: list[int],
                  gpu_modes: list[str], seeds: list[int], scenarios: list[str]):
    output.mkdir(parents=True, exist_ok=True)
    summaries, comparisons, raw, progress = [], [], [], []
    # One GPU slot is the hardware-relevant scenario. 'workers' is a sensitivity
    # case removing that bottleneck, not an extra GPU granted to one policy.
    for workers in workers_list:
        for slots in dict.fromkeys(1 if m == "one" else workers for m in gpu_modes):
            for scenario in scenarios:
                for background in (False, True):
                    for pair, seed in enumerate(seeds, 1):
                        paired = []
                        for policy in ("shared_fifo", "split") if pair % 2 else ("split", "shared_fifo"):
                            sim = Simulation(config, policy, workers, slots, scenario, background, seed, pair)
                            summary, jobs, trace = sim.run()
                            summaries.append(summary)
                            raw.extend(jobs)
                            progress.extend(trace)
                            paired.append(summary)
                        by_policy = {x["policy"]: x for x in paired}
                        comparisons.append(compare_pair(by_policy["shared_fifo"], by_policy["split"], config))
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    groups = []
    for workers in workers_list:
        for slots in dict.fromkeys(1 if m == "one" else workers for m in gpu_modes):
            pairs = [r for r in comparisons if r["scenario"] == "full_protocol" and r["background"]
                     and r["workers_total"] == workers and r["gpu_slots"] == slots]
            if pairs:
                groups.append({"workers_total": workers, "gpu_slots": slots, "pairs": len(pairs),
                               "valid_pairs": sum(r["verdict"].startswith(("B1_SUPPORTED", "B1_NOT_SUPPORTED")) for r in pairs),
                               "pair_verdicts": sorted({r["verdict"] for r in pairs}),
                               "all_five_pairs_supported": len(pairs) == 5 and all(r["verdict"].startswith("B1_SUPPORTED") for r in pairs),
                               "verdict": "B1_SUPPORTED_ONLY_WITHIN_MODEL" if len(pairs) == 5 and all(r["verdict"].startswith("B1_SUPPORTED") for r in pairs)
                               else "B1_NOT_CONFIRMED_IN_THIS_MODEL",
                               "p95_ratio_min": min((r["p95_ratio"] for r in pairs if r["p95_ratio"] is not None), default=None),
                               "p95_ratio_max": max((r["p95_ratio"] for r in pairs if r["p95_ratio"] is not None), default=None)})
    manifest = {"disclaimer": DISCLAIMER, "calibrated": False,
                "model_type": "standard-library discrete-event simulation",
                "config": asdict(config), "seeds": seeds, "workers": workers_list,
                "gpu_modes": gpu_modes, "scenarios": scenarios,
                "python_version": sys.version, "source_sha256": source_hash,
                "scope": {"ready_catalog": "v0 immutable, 12000 rows by default",
                          "background_catalog": "one independent v1 job, same row count; no activation during searches",
                          "worker_policy": "shared FIFO versus fixed 1 interactive + (W-1) batch; no work stealing",
                          "gpu_policy": "shared FIFO semaphore; slot held for one modeled call",
                          "deadline_policy": "client times out; server work is not canceled",
                          "clock": "virtual seconds, not elapsed wall-clock benchmark",
                          "input_data": "synthetic durations and counts; real catalog contents are not read",
                          "correctness": "successful simulated completion only; no semantic search-quality or authorization claim"},
                "criterion": {"p95_ratio_max": 0.70, "catalog_speed_ratio_min": 0.90,
                              "variant_error_rate_strict_max": 0.01,
                              "required_pairs": 5,
                              "background_validity": "one background catalog job must remain active throughout the 1800-second measurement window in both policies; no replacement jobs",
                              "extra_validity_guard": "baseline error rate <1% and >=100 successes per policy; otherwise survivor-only p95 is not confirmation"},
                "not_modeled": ["real HTTP traffic", "PostgreSQL/Redis/Qdrant contention", "model inference accuracy",
                                "VRAM/RAM/OOM", "OS scheduling", "network failures", "per-user access control",
                                "Celery prefetch beyond one", "backend intra-job thread pools", "actual GPU utilization"]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps({"disclaimer": DISCLAIMER, "groups": groups, "runs": summaries,
                                                   "comparisons": comparisons}, ensure_ascii=False, indent=2) + "\n")
    with (output / "raw_jobs.jsonl").open("w", encoding="utf-8") as f:
        for row in raw:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    write_csv(output / "raw_jobs.csv", raw)
    write_csv(output / "catalog_progress.csv", progress)
    write_csv(output / "runs.csv", summaries)
    write_csv(output / "comparison.csv", comparisons)
    print(json.dumps({"output": str(output), "runs": len(summaries), "raw_jobs": len(raw),
                      "groups": groups, "disclaimer": DISCLAIMER}, ensure_ascii=False, indent=2))


class ModelTests(unittest.TestCase):
    def config(self, **changes):
        return replace(ModelConfig(), catalog_rows=20, warmup_seconds=0, window_seconds=30,
                       protocol_requests=10, service_time_cv=0, **changes)

    def test_nearest_rank_and_empty(self):
        self.assertIsNone(nearest_rank([], .95))
        self.assertEqual(nearest_rank(list(range(1, 21)), .95), 19)

    def test_single_background_job_two_workers_has_same_capacity(self):
        c = self.config()
        b, _, _ = Simulation(c, "shared_fifo", 2, 1, "full_protocol", True, 1).run()
        v, _, _ = Simulation(c, "split", 2, 1, "full_protocol", True, 1).run()
        self.assertEqual(b["p95_client_e2e_s"], v["p95_client_e2e_s"])
        self.assertEqual(b["catalog_rows_in_window"], c.catalog_rows)
        self.assertEqual(v["catalog_rows_in_window"], c.catalog_rows)

    def test_sequential_client_waits_for_previous_observed_result(self):
        _, raw, _ = Simulation(self.config(), "shared_fifo", 2, 1, "sequential_10", False, 1).run()
        self.assertEqual(len(raw), 10)
        for previous, current in zip(raw, raw[1:]):
            self.assertAlmostEqual(previous["client_finish_s"], current["client_start_s"])

    def test_open_loop_arrivals_do_not_wait_and_deadlines_are_errors(self):
        c = self.config(interactive_gpu_seconds=20, deadline_seconds=1)
        summary, raw, _ = Simulation(c, "shared_fifo", 2, 1, "full_protocol", False, 1).run()
        self.assertEqual(summary["sent_queries"], 10)
        self.assertEqual(summary["deadline_errors"], 10)
        self.assertIsNone(summary["p95_client_e2e_s"])
        self.assertEqual([j["client_start_s"] for j in raw], list(range(0, 30, 3)))

    def test_e2e_includes_polling_network_and_queue(self):
        c = self.config(interactive_cpu_seconds=.1, interactive_gpu_seconds=.25)
        _, raw, _ = Simulation(c, "split", 2, 1, "simultaneous_10", False, 1).run()
        self.assertAlmostEqual(raw[0]["client_e2e_s"], .53)
        self.assertGreater(raw[-1]["broker_wait_s"], 3)
        self.assertGreater(raw[-1]["client_e2e_s"], raw[-1]["total_assumed_service_s"])

    def test_split_does_not_receive_extra_workers_and_can_be_slower(self):
        c = self.config()
        b, _, _ = Simulation(c, "shared_fifo", 4, 4, "simultaneous_10", False, 1).run()
        v, _, _ = Simulation(c, "split", 4, 4, "simultaneous_10", False, 1).run()
        self.assertEqual(b["workers_total"], v["workers_total"])
        self.assertGreater(v["p95_client_e2e_s"], b["p95_client_e2e_s"])

    def test_same_seed_reproduces_jobs_and_paired_assumed_work(self):
        c = replace(self.config(), service_time_cv=.2)
        first = Simulation(c, "shared_fifo", 2, 1, "full_protocol", True, 123).run()
        second = Simulation(c, "shared_fifo", 2, 1, "full_protocol", True, 123).run()
        variant = Simulation(c, "split", 2, 1, "full_protocol", True, 123).run()
        self.assertEqual(first, second)
        for b, v in zip(first[1], variant[1]):
            self.assertEqual(b["total_assumed_service_s"], v["total_assumed_service_s"])

    def test_background_does_not_change_interactive_service_samples(self):
        c = replace(self.config(), service_time_cv=.2)
        _, control, _ = Simulation(c, "shared_fifo", 2, 1, "full_protocol", False, 123).run()
        _, background, _ = Simulation(c, "shared_fifo", 2, 1, "full_protocol", True, 123).run()
        actual = [j for j in background if j["kind"] == "interactive"]
        self.assertEqual([j["total_assumed_service_s"] for j in control],
                         [j["total_assumed_service_s"] for j in actual])

    def test_catalog_throughput_uses_fixed_window_not_full_drain(self):
        c = self.config(batch_cpu_seconds_per_row=0, batch_gpu_seconds_per_row=2,
                        interactive_cpu_seconds=0, interactive_gpu_seconds=0)
        summary, raw, _ = Simulation(c, "split", 2, 1, "full_protocol", True, 1).run()
        batch = next(r for r in raw if r["kind"] == "batch")
        self.assertLess(batch["rows_in_window"], batch["rows_completed"])
        self.assertEqual(summary["catalog_throughput_rows_per_min"], batch["rows_in_window"] * 2)

    def test_early_background_completion_invalidates_full_b1(self):
        c = self.config()
        b, _, _ = Simulation(c, "shared_fifo", 2, 1, "full_protocol", True, 1).run()
        v, _, _ = Simulation(c, "split", 2, 1, "full_protocol", True, 1).run()
        self.assertFalse(b["background_active_entire_window"])
        self.assertEqual(compare_pair(b, v, c)["verdict"], "INVALID_B1_BACKGROUND_COMPLETED_EARLY")

    def test_long_background_is_active_at_end_of_measurement_window(self):
        c = self.config(batch_gpu_seconds_per_row=2)
        summary, _, _ = Simulation(c, "split", 2, 1, "full_protocol", True, 1).run()
        self.assertTrue(summary["background_active_entire_window"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "results" / "b1_simulation")
    parser.add_argument("--config-json", type=Path, help="explicit assumed ModelConfig overrides")
    parser.add_argument("--workers", default="2,4")
    parser.add_argument("--gpu-modes", default="one,workers", help="one: shared GPU; workers: bottleneck-removal sensitivity")
    parser.add_argument("--seeds", default="20261107,20261108,20261109,20261110,20261111")
    parser.add_argument("--scenarios", default="simultaneous_10,sequential_10,full_protocol")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ModelTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    config = ModelConfig(**json.loads(args.config_json.read_text())) if args.config_json else ModelConfig()
    gpu_modes = args.gpu_modes.split(",")
    if any(x not in ("one", "workers") for x in gpu_modes):
        parser.error("gpu-modes must contain only one or workers")
    execute_suite(config, args.output_dir, [int(x) for x in args.workers.split(",")], gpu_modes,
                  [int(x) for x in args.seeds.split(",")], args.scenarios.split(","))


if __name__ == "__main__":
    main()
