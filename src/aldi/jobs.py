"""The Job store: one folder per Job under the app's data directory, and the worker that runs them."""

import json
import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from aldi.blocks import Block
from aldi.reading import translate_page
from aldi.results import deliver, document_name
from aldi.source import SourcePage, read_pages, render_page
from aldi.translator import Offline, QuotaExhausted, Translator
from aldi.writer import TranslatedPage, write_reading_version

# A page gets two retries before it becomes a Failed Page.
_ATTEMPTS = 3


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
    number: int = 0
    """The Job's place in the order she dropped them, from 1: the queue runs and lists Jobs in this order."""
    failed_pages: list[int] = field(default_factory=list)
    """The Failed Pages of a finished Job, by their number in the original."""


class JobStore:
    """Every Job ever started; Jobs a closed app left unfinished run again, from their first page not yet done.

    A page is done when its `pages/NNNN.json` exists: it holds the page's Spanish and the Term List after it, or
    `"failed": true` for a Failed Page, which only a retry of the Job's Failed Pages translates again.
    """

    def __init__(
        self, data_dir: Path, documents_dir: Path, translator: Translator, open_folder: Callable[[Path], None]
    ):
        self._jobs_dir = data_dir / "jobs"
        self._documents_dir = documents_dir
        self._translator = translator
        self._open_folder = open_folder
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._jobs: dict[str, Job] = {}
        # One Job at a time: they would otherwise compete for the same free quota.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aldi-job")
        saved = [Job(**json.loads(path.read_text(encoding="utf-8"))) for path in self._jobs_dir.glob("*/job.json")]
        for job in sorted(saved, key=lambda job: job.number):
            self._jobs[job.id] = job
            if job.state in ("queued", "running"):
                self._update(job.id, state="queued")
                self._worker.submit(self._run, job.id)

    def start(self, filename: str, pdf: bytes) -> Job:
        with self._lock:
            number = max((job.number for job in self._jobs.values()), default=0) + 1
            job = Job(id=uuid.uuid4().hex[:12], name=document_name(filename), number=number)
            self._jobs[job.id] = job
        folder = self._folder(job.id)
        (folder / "pages").mkdir(parents=True)
        (folder / "source.pdf").write_bytes(pdf)
        self._save(job)
        self._worker.submit(self._run, job.id)
        return self.get(job.id)

    def retry(self, job_id: str) -> Job | None:
        """Translates a finished Job's Failed Pages again, delivering new results beside the first; None if none."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state != "done" or not job.failed_pages:
                return None
            # Set under the lock so a second click cannot queue the same Job twice.
            job.state = "queued"
        for number in job.failed_pages:
            (self._folder(job_id) / "pages" / f"{number:04d}.json").unlink(missing_ok=True)
        self._update(job_id, failed_pages=[])
        self._worker.submit(self._run, job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**asdict(job)) if job else None

    def list(self) -> list[Job]:
        with self._lock:
            return [Job(**asdict(job)) for job in self._jobs.values()]

    def close(self) -> None:
        """Stops after the page in hand; the Job resumes from the next page when the app starts again."""
        self._closing.set()
        self._worker.shutdown(wait=True, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        folder = self._folder(job_id)
        try:
            source = read_pages(folder / "source.pdf", folder)
            self._update(job_id, state="running", pages=len(source))
            terms: dict[str, str] = {}
            translated = []
            failed = []
            for page in source:
                done = folder / "pages" / f"{page.number:04d}.json"
                if not done.exists():
                    if self._closing.is_set():
                        return
                    saved = self._translate(page, terms)
                    done.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                saved = json.loads(done.read_text(encoding="utf-8"))
                terms = saved["terms"]
                if saved.get("failed"):
                    image = Path("pages") / f"{page.number:04d}.png"
                    render_page(folder / "source.pdf", page.number, folder / image)
                    failed.append(page.number)
                    translated.append(TranslatedPage(page.number, [], failed_image=image))
                else:
                    blocks = [Block.from_dict(block) for block in saved["blocks"]]
                    translated.append(TranslatedPage(page.number, blocks))
                self._update(job_id, page=len(translated))
            job = self.get(job_id)
            stem = f"{job.name} (es)"
            results = write_reading_version(translated, folder, stem)
            # A retry of the Failed Pages lands beside the first results, numbered: she may have edited those.
            delivered = [deliver(result, self._documents_dir, job.name, f"{stem}{result.suffix}") for result in results]
            self._update(
                job_id,
                state="done",
                results=[d.name for d in delivered],
                folder=str(delivered[0].parent),
                failed_pages=failed,
            )
        except Exception:
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._update(job_id, state="failed")
            return
        try:
            self._open_folder(delivered[0].parent)
        except OSError:
            # Her files are written and listed; a file browser that did not open is no reason to fail the Job.
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")

    def _translate(self, page: SourcePage, terms: dict[str, str]) -> dict:
        """What a page's `pages/NNNN.json` holds once it is done, Failed Page or not."""
        for _ in range(_ATTEMPTS):
            try:
                spanish, after = translate_page(page, self._translator, terms)
            except (QuotaExhausted, Offline):
                # Neither is the page's fault: the Job waits and asks again, and the page is not Failed.
                raise
            except Exception:
                error = traceback.format_exc()
                continue
            return {"blocks": [asdict(block) for block in spanish.blocks], "terms": after}
        return {"failed": True, "error": error, "terms": terms}

    def open_folder(self, job_id: str) -> bool:
        """Shows a finished Job's results folder; False when there is no such folder yet."""
        job = self.get(job_id)
        if not job or not job.folder:
            return False
        self._open_folder(Path(job.folder))
        return True

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
