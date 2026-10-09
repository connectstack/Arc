"""Long-running work as *jobs*: queued, run in their own process, observable, cancellable.

A job is a stream of small JSON events::

    {"type": "status",   "status": "queued" | "running"}
    {"type": "progress", "phase": "plan" | "audio" | "frames" | "mux", "done": 12, "total": 1491, "eta_sec": 8.1}
    {"type": "note" | "warning" | "log", "message": "..."}
    {"type": "done",      "result": {...}}            (terminal)
    {"type": "error",     "message": "...", "hint": "...", "lint": {...}}   (terminal)
    {"type": "cancelled"}                              (terminal)

Every event carries a ``seq`` number.  The web app follows a job over server-sent events (``/api/jobs/<id>/events``) and may
reconnect with ``Last-Event-ID``, so a job survives a page reload; ``GET /api/jobs/<id>`` returns the same events as a snapshot.

*Why a process:* a render uses all cores through its own worker pool and takes tens of seconds.  In a separate process the
server keeps answering preview frames, **Cancel** is a SIGINT to that process group (the engine already turns Ctrl-C into a
clean shutdown: workers stopped, no half-written file), and a crash cannot take the server down.  Jobs that wait on the
network (script -> spec with a hosted model) run on a thread instead.  Process jobs run one at a time, in the order they were
submitted, so two renders never fight for the CPU.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import queue
import signal
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

TERMINAL = frozenset({"done", "error", "cancelled"})
Emit = Callable[[dict[str, Any]], None]


class Job:
    def __init__(self, job_id: str, kind: str, title: str, payload: dict[str, Any]) -> None:
        self.id = job_id
        self.kind = kind
        self.title = title
        self.payload = payload
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.result: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.cancel_requested = False
        self.proc: Any = None
        self._cond = threading.Condition()

    def emit(self, event: dict[str, Any]) -> None:
        with self._cond:
            if self.status in TERMINAL:  # nothing follows the end of a job
                return
            ev = {**event, "seq": len(self.events)}
            self.events.append(ev)
            kind = ev.get("type")
            if kind == "status":
                self.status = str(ev["status"])
                if self.status == "running":
                    self.started_at = time.time()
            elif kind in TERMINAL:
                self.status = str(kind)
                self.finished_at = time.time()
                if kind == "done":
                    self.result = ev.get("result")
            self._cond.notify_all()

    def wait(self, after: int, timeout: float) -> tuple[list[dict[str, Any]], bool]:
        """Events with ``seq >= after`` (waiting up to ``timeout`` s for one) and whether the job has ended."""
        with self._cond:
            if len(self.events) <= after and self.status not in TERMINAL:
                self._cond.wait(timeout)
            return list(self.events[after:]), self.status in TERMINAL

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        with self._cond:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "status": self.status,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "result": self.result,
                "events": list(self.events[after:]),
            }


# ------------------------------------------------------------------------------- the child process
def _child_main(kind: str, payload: dict[str, Any], q: Any) -> None:
    """Entry point of a job process (module-level so it can be pickled by the ``spawn`` start method)."""
    from reel.server.workers import WORKERS

    with contextlib.suppress(OSError):
        os.setsid()  # own process group: Cancel signals the worker pool together with this process
    with contextlib.suppress(OSError):
        os.nice(5)  # a render must not starve the preview frames the UI is asking for
    signal.signal(signal.SIGINT, signal.default_int_handler)
    ended = False

    def emit(event: dict[str, Any]) -> None:
        nonlocal ended
        ended = ended or event.get("type") in TERMINAL
        q.put(event)

    try:
        WORKERS[kind](payload, emit)
        if not ended:
            emit({"type": "error", "message": "the worker finished without a result"})
    except KeyboardInterrupt:
        emit({"type": "cancelled"})
    except (
        BaseException
    ) as exc:  # never let a worker die silently: the UI is waiting for an end event
        traceback.print_exc()
        emit({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:400]}"})
    finally:
        q.close()
        q.join_thread()


class JobManager:
    """Creates, queues, runs, observes and cancels jobs.

    ``inline=True`` runs process jobs on a thread instead of a subprocess: the same code and events, without the start-up
    cost, for tests (it cannot cancel a running job).
    """

    def __init__(self, *, max_history: int = 60, inline: bool = False) -> None:
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._heavy: queue.Queue[Job | None] = queue.Queue()
        self._max_history = max_history
        self._inline = inline
        self._stopping = False
        self._thread = threading.Thread(target=self._heavy_loop, name="reel-jobs", daemon=True)
        self._thread.start()

    # -- creating ---------------------------------------------------------------------------
    def submit(
        self, kind: str, title: str, payload: dict[str, Any], *, mode: str = "process"
    ) -> Job:
        job = Job(uuid.uuid4().hex[:12], kind, title, payload)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._max_history:
                oldest = next(iter(self._jobs))
                if self._jobs[oldest].status not in TERMINAL:
                    break
                self._jobs.popitem(last=False)
        if mode == "thread":
            job.emit({"type": "status", "status": "running"})
            threading.Thread(
                target=self._run_thread, args=(job,), name=f"reel-{kind}", daemon=True
            ).start()
        else:
            job.emit({"type": "status", "status": "queued", "position": self._heavy.qsize() + 1})
            self._heavy.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status not in TERMINAL]

    # -- running ----------------------------------------------------------------------------
    def _run_thread(self, job: Job) -> None:
        from reel.server.workers import WORKERS

        try:
            WORKERS[job.kind](job.payload, job.emit)
            if job.status not in TERMINAL:
                job.emit({"type": "error", "message": "the worker finished without a result"})
        except BaseException as exc:
            traceback.print_exc()
            job.emit({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:400]}"})

    def _heavy_loop(self) -> None:
        while True:
            job = self._heavy.get()
            if job is None or self._stopping:
                return
            if job.cancel_requested or job.status in TERMINAL:
                continue
            try:
                if self._inline:
                    job.emit({"type": "status", "status": "running"})
                    self._run_thread(job)
                else:
                    self._run_process(job)
            except BaseException as exc:
                traceback.print_exc()
                job.emit({"type": "error", "message": f"{type(exc).__name__}: {str(exc)[:400]}"})

    def _run_process(self, job: Job) -> None:
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue()
        proc = ctx.Process(
            target=_child_main, args=(job.kind, job.payload, q), name=f"reel-{job.kind}"
        )
        proc.start()
        job.proc = proc
        job.emit({"type": "status", "status": "running"})
        while True:
            try:
                item = q.get(timeout=0.25)
            except queue.Empty:
                if not proc.is_alive():
                    with contextlib.suppress(queue.Empty):  # the last events can still be in flight
                        while True:
                            job.emit(q.get_nowait())
                    break
                continue
            job.emit(item)
            if item.get("type") in TERMINAL:
                break
        proc.join(timeout=20)
        if proc.is_alive():
            self._kill_group(proc, signal.SIGKILL)
            proc.join(timeout=5)
        if job.status not in TERMINAL:
            job.emit(
                {
                    "type": "cancelled" if job.cancel_requested else "error",
                    **(
                        {}
                        if job.cancel_requested
                        else {
                            "message": f"the worker stopped unexpectedly (exit code {proc.exitcode})"
                        }
                    ),
                }
            )
        q.close()

    @staticmethod
    def _kill_group(proc: Any, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(proc.pid, sig)

    # -- cancelling -------------------------------------------------------------------------
    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None or job.status in TERMINAL:
            return job
        job.cancel_requested = True
        if job.status == "queued":
            job.emit({"type": "cancelled"})
        elif job.proc is not None:
            self._kill_group(job.proc, signal.SIGINT)  # the engine turns this into a clean shutdown
            threading.Thread(target=self._watchdog, args=(job,), daemon=True).start()
        return job

    def _watchdog(self, job: Job) -> None:
        deadline = time.time() + 12
        while time.time() < deadline:
            if job.status in TERMINAL:
                return
            time.sleep(0.2)
        if job.proc is not None and job.proc.is_alive():
            self._kill_group(job.proc, signal.SIGKILL)

    def shutdown(self) -> None:
        self._stopping = True
        for job in self.active():
            self.cancel(job.id)
        self._heavy.put(None)
        deadline = time.time() + 15
        while time.time() < deadline and any(
            j.proc is not None and j.proc.is_alive() for j in self.active()
        ):
            time.sleep(0.1)
