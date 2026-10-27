"""The Job store: one folder per Job under the app's data directory, and the worker that runs them."""

import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aldi.reading import translate_page
from aldi.results import deliver, document_name
from aldi.source import read_pages
from aldi.translator import Translator
from aldi.writer import write_reading_version


@dataclass
class Job:
    id: str
    name: str
    state: str = "queued"
    """queued, running, done or failed."""
    page: int = 0
    """How many pages are translated so far."""
    pages: int = 0
    results: list[str] = field(default_factory=list)
    """The delivered files' names, inside `<Documents>/Aldi/<name>/`."""
    folder: str = ""
    """The results' folder, for the UI to show."""


class JobStore:
    def __init__(self, data_dir: Path, documents_dir: Path, translator: Translator):
        self._jobs_dir = data_dir / "jobs"
        self._documents_dir = documents_dir
        self._translator = translator
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        # One Job at a time: they would otherwise compete for the same free quota.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aldi-job")

    def start(self, filename: str, pdf: bytes) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], name=document_name(filename))
        folder = self._folder(job.id)
        (folder / "pages").mkdir(parents=True)
        (folder / "source.pdf").write_bytes(pdf)
        self._save(job)
        self._worker.submit(self._run, job.id)
        return self.get(job.id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**asdict(job)) if job else None

    def list(self) -> list[Job]:
        with self._lock:
            return [Job(**asdict(job)) for job in self._jobs.values()]

    def close(self) -> None:
        self._worker.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        folder = self._folder(job_id)
        try:
            source = read_pages(folder / "source.pdf", folder)
            self._update(job_id, state="running", pages=len(source))
            terms: dict[str, str] = {}
            translated = []
            for page in source:
                spanish, terms = translate_page(page, self._translator, terms)
                (folder / "pages" / f"{page.number:04d}.json").write_text(
                    json.dumps({"blocks": [asdict(block) for block in spanish.blocks]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                translated.append(spanish)
                self._update(job_id, page=len(translated))
            name = self.get(job_id).name
            stem = f"{name} (es)"
            results = write_reading_version(translated, folder, stem)
            delivered = [deliver(result, self._documents_dir, name, f"{stem}{result.suffix}") for result in results]
            self._update(job_id, state="done", results=[d.name for d in delivered], folder=str(delivered[0].parent))
        except Exception:
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._update(job_id, state="failed")

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
        self._save(job)

    def _save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            data = json.dumps(asdict(job), ensure_ascii=False, indent=2)
        (self._folder(job.id) / "job.json").write_text(data, encoding="utf-8")

    def _folder(self, job_id: str) -> Path:
        return self._jobs_dir / job_id
