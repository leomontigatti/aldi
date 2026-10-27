"""The Job store: one folder per Job under the app's data directory, and the worker that runs them."""

import json
import logging
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypeVar

from aldi import reports
from aldi.blocks import Block
from aldi.files import write_atomically
from aldi.layout import Cancelled, write_layout_kept
from aldi.page_range import format_page_range, parse_page_range
from aldi.reading import PAGE_BATCH, read_page_batch, translate_page
from aldi.reports import Context, Reporter
from aldi.results import deliver, document_name
from aldi.source import SourcePage, inspect, read_pages, render_page
from aldi.translator import KeyRefused, Offline, QuotaExhausted, Translator
from aldi.writer import TranslatedPage, write_reading_version

# A page gets two retries before it becomes a Failed Page.
_ATTEMPTS = 3
QUOTA_PAUSE_NOTICE = "En pausa hasta que Gemini vuelva a estar disponible"
OFFLINE_NOTICE = "Sin conexión a internet"
KEY_REFUSED_NOTICE = "Tu clave de Gemini dejó de funcionar. Revisa la clave en Ajustes."
# Seconds before a paused Job asks Gemini again: a per-minute quota is back by then, a daily one costs a few 429s.
QUOTA_PAUSE = 5 * 60
OFFLINE_WAIT = 30

_Answer = TypeVar("_Answer")
_log = logging.getLogger(__name__)


class Closing(Exception):
    """The app is closing, or she cancelled the Job: it stops where it is. Closing keeps its finished pages for the
    next start; a cancelled Job's folder goes with it."""


def closing_wait(closing: threading.Event) -> Callable[[float], None]:
    """A wait of some seconds that the app closing ends at once, raising `Closing`: the Job store's, and the
    translator's between its retries."""

    def wait(seconds: float) -> None:
        if closing.wait(seconds):
            raise Closing

    return wait


@dataclass
class Job:
    id: str
    name: str
    state: str = "queued"
    """queued, running, paused, done or failed."""
    page: int = 0
    """How many pages are translated so far."""
    pages: int = 0
    """How many pages it translates: those of its Page Range."""
    page_range: str = ""
    """The Page Range, as `parse_page_range` reads it; empty for every page."""
    results: list[str] = field(default_factory=list)
    """The delivered files' names, inside `<Documents>/Aldi/<name>/`."""
    folder: str = ""
    """The results' folder, for the UI to show."""
    number: int = 0
    """The Job's place in the order she dropped them, from 1: the queue runs and lists Jobs in this order."""
    failed_pages: list[int] = field(default_factory=list)
    """The Failed Pages of a finished Job, by their number in the original."""
    notice: str = ""
    """The Spanish line the UI shows under the Job's state besides the progress, such as why it is paused or an
    Error Report's."""
    output_mode: str = "reading"
    """The Output Mode: `reading`, the Reading Version, or `layout`, the Layout-kept Translation."""


@dataclass(frozen=True)
class SourceDocument:
    """A PDF she dropped that waits for her to choose its pages before it becomes a Job."""

    id: str
    name: str
    scanned: list[bool]
    """Whether each page is scanned, in order."""

    def pages(self, page_range: str) -> list[int]:
        """The page numbers `page_range` names; raises `PageRangeError` with what she is told when it names none."""
        return parse_page_range(page_range, len(self.scanned))

    def keeps_layout(self, pages: Sequence[int]) -> bool:
        """Whether a Layout-kept Translation of `pages` is offered: only when every one has a text layer."""
        return not any(self.scanned[number - 1] for number in pages)


class LayoutNotOffered(Exception):
    """A Layout-kept Translation asked for pages that are not all born-digital."""


class JobStore:
    """Every Job ever started; Jobs a closed app left unfinished run again, from their first page not yet done.

    One Job translates at a time: they would otherwise compete for the same free quota. The turn goes to the Job
    dropped first among those ready; a Job that pauses (a Quota Pause, no connection, a refused key) gives it up
    while it waits, and takes it again once its wait is over and the Job translating finishes or pauses.

    A page is done when its `pages/NNNN.json` exists: it holds the page's Spanish and the Term List after it, or
    `"failed": true` for a Failed Page, which only a retry of the Job's Failed Pages translates again.
    """

    def __init__(
        self,
        data_dir: Path,
        documents_dir: Path,
        translator: Translator,
        open_folder: Callable[[Path], None],
        reporter: Reporter,
        sleep: Callable[[float], None] | None = None,
        closing: threading.Event | None = None,
    ):
        """`closing` is set when the app closes, `closing_wait(closing)` being the translator's waits too; `sleep`
        stands in for the store's own waits in tests, which closing the app or cancelling the Job end otherwise."""
        self._jobs_dir = data_dir / "jobs"
        # A PDF dropped but never started is not kept past the app's run.
        self._dropped_dir = data_dir / "dropped"
        shutil.rmtree(self._dropped_dir, ignore_errors=True)
        self._dropped: dict[str, SourceDocument] = {}
        self._documents_dir = documents_dir
        self._translator = translator
        self._open_folder = open_folder
        self._reporter = reporter
        self._lock = threading.Lock()
        self._closing = closing or threading.Event()
        self._sleep = sleep
        # Counts the keys she saved, for a Job waiting on a refused key to know a new one is there.
        self._key_saved = threading.Condition()
        self._keys = 0
        self._jobs: dict[str, Job] = {}
        self._cancelled: set[str] = set()
        # The turn to translate: the Job holding it, and the Jobs ready for it with their place in the order.
        self._turn = threading.Condition()
        self._holder: str | None = None
        self._ready: dict[str, int] = {}
        self._threads: list[threading.Thread] = []
        saved = [Job(**json.loads(path.read_text(encoding="utf-8"))) for path in self._jobs_dir.glob("*/job.json")]
        for job in sorted(saved, key=lambda job: job.number):
            self._jobs[job.id] = job
            # A Job the app closed on while it waited out a Quota Pause, no connection or a refused key goes on too.
            if job.state in ("queued", "running", "paused"):
                self._update(job.id, state="queued", notice="")
                self._launch(job)

    def drop(self, filename: str, pdf: bytes) -> SourceDocument:
        """Keeps a PDF she dropped until she starts it; raises `NotAPdfError` when it is not one."""
        document_id = uuid.uuid4().hex[:12]
        folder = self._dropped_dir / document_id
        folder.mkdir(parents=True)
        (folder / "source.pdf").write_bytes(pdf)
        try:
            scanned = inspect(folder / "source.pdf")
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        document = SourceDocument(document_id, document_name(filename), scanned)
        with self._lock:
            self._dropped[document_id] = document
        return document

    def dropped(self, document_id: str) -> SourceDocument | None:
        with self._lock:
            return self._dropped.get(document_id)

    def start(self, document_id: str, page_range: str, output_mode: str = "reading") -> Job | None:
        """Starts the Job of a dropped PDF on its Page Range in an Output Mode; None when no such PDF waits.

        Raises `PageRangeError` when the Page Range names none of its pages, and `LayoutNotOffered` when the
        Layout-kept Translation is asked for pages without text; the PDF then still waits.
        """
        with self._lock:
            document = self._dropped.get(document_id)
            if not document:
                return None
            pages = document.pages(page_range)
            if output_mode == "layout" and not document.keeps_layout(pages):
                raise LayoutNotOffered
            del self._dropped[document_id]
            number = max((job.number for job in self._jobs.values()), default=0) + 1
            job = Job(
                id=document.id,
                name=document.name,
                pages=len(pages),
                page_range=format_page_range(pages, len(document.scanned)),
                number=number,
                output_mode="layout" if output_mode == "layout" else "reading",
            )
            self._jobs[job.id] = job
        folder = self._folder(job.id)
        folder.parent.mkdir(parents=True, exist_ok=True)
        (self._dropped_dir / document.id).rename(folder)
        (folder / "pages").mkdir()
        self._save(job)
        self._launch(job)
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
        self._update(job_id, failed_pages=[], notice="")
        self._launch(job)
        return self.get(job_id)

    def cancel(self, job_id: str) -> bool | None:
        """Stops a queued, running or paused Job and drops it with its Job folder; her delivered files stay. False
        for a finished Job, None for no such Job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.state in ("done", "failed"):
                return False
            del self._jobs[job_id]
            self._cancelled.add(job_id)
            # Gone at once, so a restart never resumes it; the rest of its folder goes when its thread stops.
            (self._folder(job_id) / "job.json").unlink(missing_ok=True)
        self._wake()
        return True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**asdict(job)) if job else None

    def list(self) -> list[Job]:
        with self._lock:
            return [Job(**asdict(job)) for job in self._jobs.values()]

    def key_saved(self) -> None:
        """She saved a new key: a Job paused on a refused one goes on."""
        with self._key_saved:
            self._keys += 1
            self._key_saved.notify_all()

    def close(self) -> None:
        """Stops after the page in hand; the Job resumes from the next page when the app starts again."""
        self._closing.set()
        self._wake()
        for thread in list(self._threads):
            thread.join()

    def _wake(self) -> None:
        """Every Job waiting for its turn, a wait's end or a new key looks again at whether it should stop."""
        with self._key_saved:
            self._key_saved.notify_all()
        with self._turn:
            self._turn.notify_all()

    def _stopping(self, job_id: str) -> bool:
        return self._closing.is_set() or job_id in self._cancelled

    def _launch(self, job: Job) -> None:
        """Runs the Job in a thread of its own once it has the turn; ready from now, so Jobs keep their order."""
        with self._turn:
            self._ready[job.id] = job.number
        thread = threading.Thread(target=self._work, args=(job.id,), name=f"aldi-job-{job.id}")
        self._threads = [other for other in self._threads if other.is_alive()] + [thread]
        thread.start()

    def _work(self, job_id: str) -> None:
        try:
            self._take_turn(job_id)
            try:
                self._run(job_id)
            finally:
                self._give_turn(job_id)
        except Closing:
            pass
        finally:
            if job_id in self._cancelled:
                shutil.rmtree(self._folder(job_id), ignore_errors=True)

    def _take_turn(self, job_id: str, waiting: Callable[[], None] | None = None) -> None:
        """Waits until the Job is the first ready one and no other translates; `waiting` runs first when it must
        wait. Raises `Closing` when the app closes or she cancels the Job meanwhile."""
        with self._turn:
            if job_id not in self._ready:
                job = self.get(job_id)
                if not job:
                    raise Closing
                self._ready[job_id] = job.number

            def first() -> bool:
                return self._holder is None and min(self._ready, key=self._ready.__getitem__) == job_id

            try:
                if waiting and not first() and not self._stopping(job_id):
                    waiting()
                self._turn.wait_for(lambda: self._stopping(job_id) or first())
                if self._stopping(job_id):
                    raise Closing
                self._holder = job_id
            finally:
                del self._ready[job_id]
                self._turn.notify_all()

    def _give_turn(self, job_id: str) -> None:
        with self._turn:
            if self._holder == job_id:
                self._holder = None
                self._turn.notify_all()

    def _run(self, job_id: str) -> None:
        folder = self._folder(job_id)
        scanned: list[bool] = []
        failed: list[int] = []
        failure = None
        problem = "Crash while translating"
        try:
            job = self.get(job_id)
            scanned = inspect(folder / "source.pdf")
            pages = parse_page_range(job.page_range, len(scanned))
            scanned = [scanned[number - 1] for number in pages]
            if job.output_mode == "layout":
                kept = self._layout_kept(job_id, folder, pages)
                problem = "Result could not be written"
                results = [(kept, f"{job.name} (es, diseño).pdf")]
            else:
                translated, failed, failure = self._translate_pages(job_id, folder, pages)
                problem = "Result could not be written"
                stem = f"{job.name} (es)"
                results = [
                    (result, f"{stem}{result.suffix}") for result in write_reading_version(translated, folder, stem)
                ]
            if self._stopping(job_id):
                raise Closing
            # A retry of the Failed Pages lands beside the first results, numbered: she may have edited those.
            delivered = [deliver(result, self._documents_dir, job.name, filename) for result, filename in results]
        except Closing:
            return
        except Exception as error:
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            details = reports.describe(error)
            notice = self._report(job_id, problem, details, scanned, []) if reports.is_bug(error) else ""
            self._update(job_id, state="failed", notice=notice)
            return
        notice = (
            self._report(job_id, "Failed Pages", reports.ErrorDetails(**failure), scanned, failed) if failure else ""
        )
        self._update(
            job_id,
            state="done",
            results=[d.name for d in delivered],
            folder=str(delivered[0].parent),
            failed_pages=failed,
            notice=notice,
        )
        try:
            self._open_folder(delivered[0].parent)
        except OSError:
            # Her files are written and listed; a file browser that did not open is no reason to fail the Job.
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")

    def _translate_pages(
        self, job_id: str, folder: Path, pages: Sequence[int]
    ) -> tuple[Sequence[TranslatedPage], Sequence[int], dict | None]:
        """Each page's Spanish for the Reading Version, its Failed Pages and what the first of them reports."""
        source = read_pages(folder / "source.pdf", folder, set(pages))
        self._update(job_id, state="running", pages=len(source))
        terms: dict[str, str] = {}
        translated = []
        failed = []
        failure = None
        for index, page in enumerate(source):
            done = _page_file(folder, page.number)
            if not done.exists():
                if self._stopping(job_id):
                    raise Closing
                batch = _page_batch(source[index:], folder) if page.scanned else [page]
                answers = (
                    self._read_scanned(job_id, batch, folder, terms)
                    if page.scanned
                    else [self._translate(job_id, page, terms)]
                )
                for batch_page, answer in zip(batch, answers, strict=True):
                    write_atomically(_page_file(folder, batch_page.number), json.dumps(answer, ensure_ascii=False))
            saved = json.loads(done.read_text(encoding="utf-8"))
            terms = saved["terms"]
            if saved.get("failed"):
                image = Path("pages") / f"{page.number:04d}.png"
                render_page(folder / "source.pdf", page.number, folder / image)
                failed.append(page.number)
                failure = failure or saved.get("report")
                translated.append(TranslatedPage(page.number, [], failed_image=image))
            else:
                blocks = [Block.from_dict(block) for block in saved["blocks"]]
                translated.append(TranslatedPage(page.number, blocks))
            self._update(job_id, page=len(translated))
        return translated, failed, failure

    def _layout_kept(self, job_id: str, folder: Path, pages: Sequence[int]) -> Path:
        """The Layout-kept Translation's PDF. BabelDOC lays out every page before it asks Gemini anything, so the
        progress moves only when it is done. Each prompt waits out a Quota Pause, a lost connection or a refused key
        while BabelDOC waits for its answer."""
        self._update(job_id, state="running", pages=len(pages))
        (folder / "layout").mkdir(exist_ok=True)

        def answer(prompt: str) -> str:
            waited = 0

            def ask() -> str:
                nonlocal waited
                try:
                    return self._translator.answer_layout_prompt(prompt, waited)
                except QuotaExhausted:
                    waited += 1
                    raise

            return self._ask(job_id, ask)

        stopping = lambda: self._stopping(job_id)  # noqa: E731
        try:
            result = write_layout_kept(folder / "source.pdf", pages, folder / "layout", answer, stopping)
        except Cancelled as cancelled:
            raise Closing from cancelled
        self._update(job_id, page=len(pages))
        return result

    def _translate(self, job_id: str, page: SourcePage, terms: dict[str, str]) -> dict:
        """What a page's `pages/NNNN.json` holds once it is done, Failed Page or not."""
        for _ in range(_ATTEMPTS):
            try:
                spanish, after = self._ask(job_id, lambda: translate_page(page, self._translator, terms))
            except Closing:
                raise
            except Exception as exception:
                last = exception
                continue
            return {"blocks": [asdict(block) for block in spanish.blocks], "terms": after}
        return _failed_page(last, terms)

    def _read_scanned(
        self, job_id: str, pages: Sequence[SourcePage], folder: Path, terms: dict[str, str]
    ) -> Sequence[dict]:
        """What each scanned page's `pages/NNNN.json` holds once it is done, Failed Page or not."""
        for _ in range(_ATTEMPTS if len(pages) == 1 else 1):
            try:
                translated, after = self._ask(
                    job_id, lambda: read_page_batch(pages, folder / "source.pdf", folder, self._translator, terms)
                )
            except Closing:
                raise
            except Exception as exception:
                last = exception
                continue
            return [{"blocks": [asdict(block) for block in page.blocks], "terms": after} for page in translated]
        if len(pages) == 1:
            return [_failed_page(last, terms)]
        # A Page Batch that failed is read again a page at a time: alone, a page cannot go missing or be merged.
        answers = []
        for page in pages:
            answers += self._read_scanned(job_id, [page], folder, terms)
            terms = answers[-1]["terms"]
        return answers

    def _report(
        self, job_id: str, problem: str, details: reports.ErrorDetails, scanned: Sequence[bool], failed: Sequence[int]
    ) -> str:
        """Files an Error Report about this Job, whose pages are scanned or not as `scanned` says; returns what she
        is told about it."""
        with_text = sum(not page for page in scanned)
        if not scanned:
            kind = "unknown"
        elif with_text == len(scanned):
            kind = "born-digital"
        else:
            kind = "mixed" if with_text else "scanned"
        mode = "Layout-kept Translation" if self.get(job_id).output_mode == "layout" else "Reading Version"
        return self._reporter.report(problem, details, Context(mode, kind, len(scanned), list(failed)))

    def _ask(self, job_id: str, request: Callable[[], _Answer]) -> _Answer:
        """The request's answer, waiting out a Quota Pause, a lost connection or a refused key as often as it takes:
        none is the page's fault, so none makes it a Failed Page. The Job is running again from the moment the
        request is asked again; a Job that waits lets the next one translate meanwhile."""
        while True:
            keys = self._keys
            try:
                return request()
            except QuotaExhausted as exhausted:
                self._wait(job_id, QUOTA_PAUSE_NOTICE, exhausted.retry_after or QUOTA_PAUSE)
            except Offline:
                self._wait(job_id, OFFLINE_NOTICE, OFFLINE_WAIT)
            except KeyRefused:
                self._wait_for_key(job_id, keys)
            self._update(job_id, state="running", notice="")

    def _wait(self, job_id: str, notice: str, seconds: float) -> None:
        _log.info("Job %s paused %g s: %s", job_id, seconds, notice)
        self._update(job_id, state="paused", notice=notice)
        self._give_turn(job_id)
        if self._sleep:
            self._sleep(seconds)
        else:
            with self._turn:
                self._turn.wait_for(lambda: self._stopping(job_id), timeout=seconds)
        self._resume(job_id)

    def _wait_for_key(self, job_id: str, keys: int) -> None:
        """Asks Gemini nothing until she saves a key after the `keys`-th, the one it refused."""
        _log.info("Job %s paused until a new key is saved", job_id)
        self._update(job_id, state="paused", notice=KEY_REFUSED_NOTICE)
        self._give_turn(job_id)
        with self._key_saved:
            self._key_saved.wait_for(lambda: self._keys != keys or self._stopping(job_id))
        self._resume(job_id)

    def _resume(self, job_id: str) -> None:
        """Takes the turn back after a pause, in the queue meanwhile when another Job translates."""
        if self._stopping(job_id):
            raise Closing
        self._take_turn(job_id, lambda: self._update(job_id, state="queued", notice=""))

    def open_folder(self, job_id: str) -> bool:
        """Shows a finished Job's results folder; False when there is no such folder yet."""
        job = self.get(job_id)
        if not job or not job.folder:
            return False
        self._open_folder(Path(job.folder))
        return True

    def _update(self, job_id: str, **changes) -> None:
        """Raises `Closing` once she cancelled the Job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise Closing
            for key, value in changes.items():
                setattr(job, key, value)
            self._write(job)

    def _save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._write(job)

    def _write(self, job: Job) -> None:
        # Under the lock: a cancelled Job's `job.json` is never written again after `cancel` removed it.
        write_atomically(self._folder(job.id) / "job.json", json.dumps(asdict(job), ensure_ascii=False, indent=2))

    def _folder(self, job_id: str) -> Path:
        return self._jobs_dir / job_id


def _page_file(folder: Path, number: int) -> Path:
    return folder / "pages" / f"{number:04d}.json"


def _page_batch(pages: Sequence[SourcePage], folder: Path) -> list[SourcePage]:
    """The next Page Batch: the scanned pages not done yet that follow one another from the first of `pages`."""
    batch = []
    for page in pages[:PAGE_BATCH]:
        if not page.scanned or _page_file(folder, page.number).exists():
            break
        batch.append(page)
    return batch


def _failed_page(error: Exception, terms: dict[str, str]) -> dict:
    """What a Failed Page's `pages/NNNN.json` holds."""
    trace = "".join(traceback.format_exception(error))
    # What its Error Report will say, redacted now; a page that failed on a 503 or a refused key gets none.
    report = asdict(reports.describe(error)) if reports.is_bug(error) else None
    return {"failed": True, "error": trace, "report": report, "terms": terms}
