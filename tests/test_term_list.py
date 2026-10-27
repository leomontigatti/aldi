"""The Term List goes with every Page Batch, comes back with the terms it adds, and survives a restart (#10)."""

from conftest import FakeTranslator, build_scanned_pdf

CEJ = {"CEJ": "unión amelocementaria (CEJ, cementoenamel junction)"}
PULPOTOMY = {"pulpotomy": "pulpotomía"}


class AppClosed(BaseException):
    """The app's process ending in the middle of a request: nothing after it runs, not even an `except Exception`."""


def scanned_book(tmp_path, pages):
    return build_scanned_pdf(tmp_path / "Endodoncia.pdf", [[] for _ in range(pages)])


def test_each_page_batch_is_sent_the_terms_of_the_batches_before_it(app, translator, tmp_path):
    translator.scanned_terms = {2: CEJ, 6: PULPOTOMY}

    job = app.wait_for(app.upload(scanned_book(tmp_path, 9))["id"])

    assert job["state"] == "done"
    assert translator.batches == [[1, 2, 3, 4], [5, 6, 7, 8], [9]]
    assert translator.terms_given == [{}, CEJ, CEJ | PULPOTOMY]


def test_a_resumed_job_keeps_its_term_list(start_app, tmp_path):
    before = FakeTranslator()
    before.scanned_terms = {2: CEJ}
    read = before.read_page_batch

    def closes_on_the_second_batch(pages, terms):
        if pages[0][0] == 5:
            raise AppClosed
        return read(pages, terms)

    before.read_page_batch = closes_on_the_second_batch
    app = start_app(before)
    job_id = app.upload(scanned_book(tmp_path, 8))["id"]
    app.wait_for_page(job_id, 4)
    app.stop()

    after = FakeTranslator()
    job = start_app(after).wait_for(job_id)

    assert job["state"] == "done"
    assert after.batches == [[5, 6, 7, 8]]
    assert after.terms_given == [CEJ]
