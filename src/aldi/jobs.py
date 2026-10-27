"""The Job store: one folder per Job under the app's data directory, and the worker that runs them."""

import json
import threading
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aldi.results import deliver, document_name
from aldi.source import read_pages
from aldi.translator import Offline, QuotaExhausted, TextTranslation, Translator
from aldi.writer import TranslatedPage, write_reading_version

QUOTA_PAUSE_NOTICE = "En pausa hasta que Gemini vuelva a estar disponible"
OFFLINE_NOTICE = "Sin conexión a internet"
# Seconds before a paused Job asks Gemini again: a per-minute quota is back by then, a daily one costs a few 429s.
QUOTA_PAUSE = 5 * 60
OFFLINE_WAIT = 30


class _Closing(Exception):
    """The app is closing while a Job waits: it stops where it is, its finished pages kept."""


@dataclass
class Job:
    id: str
    name: str
    state: str = "queued"
    """queued, running, paused, done or failed."""
    page: int = 0
    """How many pages are translated so far."""
    pages: int = 0
    results: list[str] = field(default_factory=list)
    """The delivered files' names, inside `<Documents>/Aldi/<name>/`."""
    folder: str = ""
    """The results' folder, for the UI to show."""
    notice: str = ""
    """What the UI says under the state besides the progress, such as why a Job is paused."""


class JobStore:
    def __init__(
        self,
        data_dir: Path,
        documents_dir: Path,
        translator: Translator,
        sleep: Callable[[float], None] | None = None,
    ):
        self._jobs_dir = data_dir / "jobs"
        self._documents_dir = documents_dir
        self._translator = translator
        self._closing = threading.Event()
        self._sleep = sleep or self._closing.wait
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
        self._closing.set()
        self._worker.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        folder = self._folder(job_id)
        try:
            source = read_pages(folder / "source.pdf")
            self._update(job_id, state="running", pages=len(source))
            terms: dict[str, str] = {}
            translated = []
            for page in source:
                answer = self._translate(job_id, page.texts, terms) if page.texts else None
                texts = answer.texts if answer else []
                terms = answer.terms if answer else terms
                (folder / "pages" / f"{page.number:04d}.json").write_text(
                    json.dumps({"source": page.texts, "spanish": texts}, ensure_ascii=False), encoding="utf-8"
                )
                translated.append(TranslatedPage(page.number, texts))
                self._update(job_id, page=len(translated))
            name = self.get(job_id).name
            stem = f"{name} (es)"
            results = write_reading_version(translated, folder, stem)
            delivered = [deliver(result, self._documents_dir, name, f"{stem}{result.suffix}") for result in results]
            self._update(job_id, state="done", results=[d.name for d in delivered], folder=str(delivered[0].parent))
        except _Closing:
            return
        except Exception:
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._update(job_id, state="failed")

    def _translate(self, job_id: str, texts: Sequence[str], terms: Mapping[str, str]) -> TextTranslation:
        """The page's translation, waiting out a Quota Pause or a lost connection as often as it takes."""
        while True:
            try:
                answer = self._translator.translate_texts(texts, terms)
            except QuotaExhausted:
                self._wait(job_id, QUOTA_PAUSE_NOTICE, QUOTA_PAUSE)
            except Offline:
                self._wait(job_id, OFFLINE_NOTICE, OFFLINE_WAIT)
            else:
                if self.get(job_id).state == "paused":
                    self._update(job_id, state="running", notice="")
                return answer

    def _wait(self, job_id: str, notice: str, seconds: float) -> None:
        self._update(job_id, state="paused", notice=notice)
        self._sleep(seconds)
        if self._closing.is_set():
            raise _Closing

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
