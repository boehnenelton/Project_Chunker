#!/usr/bin/env python3
"""
File Name:       jobs.py
Relative Path:   Web/chunker_web/jobs.py
Description:     Single-slot background job runner for the web app: one live job at a time, progress events for
                 polling, optional cancel (only honoured by jobs that check it), and a BEJSON 104a journal of
                 finished jobs under data/.
VERSION:         1.8.0
Author:          Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
RELATIONAL_ID:   b07d7ddc-2a5e-4b84-9270-0cfb76348acf
"""

import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lib_bejson_Core_bejson_core import bejson_core_atomic_write, bejson_core_get_field_map, bejson_core_load_file
from lib_bejson_Core_bejson_validator import validate_bejson
from lib_bejson_Projects_logging import projects_logging_get_logger

VERSION = "1.8.0"
RECORDS_TYPE = "WebJob"
FIELDS = [{"name": n, "type": "string"} for n in
          ("job_id", "op", "label", "state", "started", "finished", "summary", "error")]
MAX_EVENTS = 200
KEEP_RESULTS = 20
_log = projects_logging_get_logger("web.jobs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRunner:
    def __init__(self, journal_path: Path, journal_size: int = 200):
        self.journal_path, self.journal_size = Path(journal_path), max(10, int(journal_size))
        self._lock = threading.Lock()
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._cancel: Dict[str, threading.Event] = {}

    def start(self, op: str, label: str, fn: Callable[[Callable, Callable], Any], cancellable: bool = False,
              summarize: Optional[Callable[[Any], str]] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if any(j["state"] == "running" for j in self._jobs.values()):
                return None
            job = {"id": uuid.uuid4().hex[:12], "op": op, "label": label, "state": "running", "started": _now(),
                   "finished": "", "events": [], "current": {}, "result": None, "error": "", "summary": "",
                   "cancellable": cancellable}
            self._jobs[job["id"]] = job
            self._cancel[job["id"]] = threading.Event()
            while len(self._jobs) > KEEP_RESULTS:
                old = next(iter(self._jobs))
                if self._jobs[old]["state"] == "running":
                    break
                self._jobs.pop(old)
                self._cancel.pop(old, None)
        threading.Thread(target=self._run, args=(job, fn, summarize), daemon=True).start()
        return job

    def _run(self, job, fn, summarize):
        flag = self._cancel[job["id"]]

        def emit(event: Dict[str, Any]) -> None:
            job["current"] = event
            if event.get("event") != "scan":
                job["events"].append(event)
                del job["events"][:-MAX_EVENTS]
        final = "done"
        try:
            job["result"] = fn(emit, flag.is_set)
            final = "cancelled" if flag.is_set() else "done"
            if summarize and job["result"] is not None:
                job["summary"] = summarize(job["result"])
        except Exception as exc:                          # surfaced in the UI and the journal
            _log.error("job %s (%s) failed: %s", job["id"], job["op"], exc)
            job["error"], final = str(exc), "failed"
        job["finished"] = _now()
        self._journal(dict(job, state=final))             # journal first: a finished state implies a journalled job
        job["state"] = final

    def _journal(self, job: Dict[str, Any]) -> None:
        try:
            rows = self.history()
            rows.append({k: str(job.get(k, "")) for k in ("job_id", "op", "label", "state", "started", "finished", "summary", "error")}
                        | {"job_id": job["id"]})
            rows = rows[-self.journal_size:]
            doc = {"Format": "BEJSON", "Format_Version": "104a", "Format_Creator": "Elton Boehnen",
                   "Records_Type": [RECORDS_TYPE], "Fields": [dict(f) for f in FIELDS],
                   "Values": [[r[f["name"]] for f in FIELDS] for r in rows]}
            if validate_bejson(doc).valid:
                self.journal_path.parent.mkdir(parents=True, exist_ok=True)
                bejson_core_atomic_write(str(self.journal_path), doc)
        except Exception as exc:
            _log.warning("journal write failed: %s", exc)

    def history(self) -> List[Dict[str, str]]:
        if not self.journal_path.is_file():
            return []
        try:
            doc = bejson_core_load_file(str(self.journal_path)) or {}
            fm = bejson_core_get_field_map(doc)
            return [{name: str(row[idx]) for name, idx in fm.items() if not name.startswith("_")}
                    for row in doc.get("Values", [])]
        except Exception as exc:
            _log.warning("journal unreadable: %s", exc)
            return []

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._jobs.get(job_id)

    def current(self) -> Optional[Dict[str, Any]]:
        for job in reversed(self._jobs.values()):
            if job["state"] == "running":
                return job
        return None

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job["state"] != "running" or not job["cancellable"]:
            return False
        self._cancel[job_id].set()
        return True

    @staticmethod
    def view(job: Dict[str, Any], with_result: bool = True) -> Dict[str, Any]:
        out = {k: job[k] for k in ("id", "op", "label", "state", "started", "finished", "current", "error",
                                   "summary", "cancellable")}
        out["events"] = job["events"][-60:]
        if with_result:
            out["result"] = job["result"]
        return out
