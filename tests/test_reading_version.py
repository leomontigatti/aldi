"""A Born-digital PDF dropped through the app's API comes back as a Spanish Reading Version (#4)."""

import json
import re
import urllib.error
import zipfile

import pymupdf
import pytest

from conftest import build_born_digital_pdf


def docx_text(path):
    with zipfile.ZipFile(path) as docx:
        xml = docx.read("word/document.xml").decode("utf-8")
    return " ".join(re.sub(r"<[^>]+>", " ", xml).split())


def test_born_digital_pdf_becomes_a_spanish_pdf_and_docx(app, documents_dir, tmp_path):
    source = build_born_digital_pdf(
        tmp_path / "Visão geral.pdf",
        [["Dental caries is a disease.", "Enamel covers the crown."], ["Corrêa studied the pulp."]],
    )

    job = app.wait_for(app.upload(source)["id"])

    assert job["state"] == "done"
    folder = documents_dir / "Aldi" / "Visão geral"
    pdf, docx = folder / "Visão geral (es).pdf", folder / "Visão geral (es).docx"
    pdf_text = "".join(page.get_text() for page in pymupdf.open(pdf))
    pdf_text = " ".join(pdf_text.split())
    for expected in ("ES: Dental caries is a disease.", "ES: Enamel covers the crown.", "ES: Corrêa studied the pulp."):
        assert expected in pdf_text
        assert expected in docx_text(docx)
    assert pdf_text.index("p. 1 del original") < pdf_text.index("ES: Dental caries")
    assert pdf_text.index("ES: Enamel covers") < pdf_text.index("p. 2 del original") < pdf_text.index("ES: Corrêa")
    assert "p. 2 del original" in docx_text(docx)


def test_reading_version_is_left_aligned_not_justified(app, documents_dir, tmp_path):
    long_paragraph = " ".join(["The periodontal ligament anchors each tooth to the alveolar bone."] * 12)
    source = build_born_digital_pdf(tmp_path / "ligament.pdf", [[long_paragraph]])

    app.wait_for(app.upload(source)["id"])

    page = pymupdf.open(documents_dir / "Aldi" / "ligament" / "ligament (es).pdf")[0]
    lines = [line for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]
    body = [line for line in lines if "del original" not in "".join(span["text"] for span in line["spans"])]
    inner_lines = body[:-1]  # the last line of a paragraph is short either way
    assert len(inner_lines) >= 4
    right_edges = {round(line["bbox"][2]) for line in inner_lines}
    assert max(right_edges) - min(right_edges) > 5


def test_a_file_that_is_not_a_pdf_is_refused_in_spanish(app, tmp_path):
    notes = tmp_path / "apuntes.pdf"
    notes.write_text("just some notes", encoding="utf-8")

    with pytest.raises(urllib.error.HTTPError) as refused:
        app.upload(notes)

    assert refused.value.code == 400
    assert app.get("/api/jobs")["jobs"] == []


def test_a_second_translation_does_not_overwrite_the_first(app, documents_dir, tmp_path):
    source = build_born_digital_pdf(tmp_path / "Pulpa.pdf", [["The pulp is soft tissue."]])

    first = app.wait_for(app.upload(source)["id"])
    second = app.wait_for(app.upload(source)["id"])

    assert first["results"] == ["Pulpa (es).pdf", "Pulpa (es).docx"]
    assert second["results"] == ["Pulpa (es) (2).pdf", "Pulpa (es) (2).docx"]
    assert sorted(path.name for path in (documents_dir / "Aldi" / "Pulpa").iterdir()) == [
        "Pulpa (es) (2).docx",
        "Pulpa (es) (2).pdf",
        "Pulpa (es).docx",
        "Pulpa (es).pdf",
    ]


def test_markdown_characters_in_the_text_stay_as_written(app, documents_dir, tmp_path):
    source = build_born_digital_pdf(tmp_path / "dose.pdf", [["# 2. Mix 5*3 g with *care* and [x]_y_ <b>"]])

    app.wait_for(app.upload(source)["id"])

    pdf = pymupdf.open(documents_dir / "Aldi" / "dose" / "dose (es).pdf")
    assert "ES: # 2. Mix 5*3 g with *care* and [x]_y_ <b>" in " ".join(pdf[0].get_text().split())


def test_a_broken_pdf_is_refused_before_it_starts(app, documents_dir, tmp_path):
    source = tmp_path / "Esmalte.pdf"
    source.write_bytes(b"%PDF-1.7\n" + b"\x00broken" * 50)

    with pytest.raises(urllib.error.HTTPError) as refused:
        app.upload(source)

    assert refused.value.code == 400
    assert json.load(refused.value)["error"] == "Este archivo no es un PDF."
    assert app.get("/api/jobs")["jobs"] == []
    assert not (documents_dir / "Aldi" / "Esmalte").exists()


def test_results_are_named_safely_for_windows(app, documents_dir, tmp_path):
    source = build_born_digital_pdf(tmp_path / "source.pdf", [["Caries is common."]])

    job = app.wait_for(app.upload(source, filename=r"C:\Users\ana\Desktop\Caries: guía?.PDF")["id"])

    assert job["results"] == ["Caries guía (es).pdf", "Caries guía (es).docx"]
    assert (documents_dir / "Aldi" / "Caries guía" / "Caries guía (es).pdf").is_file()
