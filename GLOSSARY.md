# Aldi

An app on the user's own laptop that translates English or Portuguese PDFs (dentistry books, journal articles,
course readings) into Spanish she can read, highlight and edit.

Each term has an English name, used in discussion and in all code identifiers, and a Spanish translation, used
only where the user reads it.

## Language

### Documents

**Source Document**:
The PDF the user drops into the app, in English or Portuguese. Its language is detected, never asked.
_Spanish_: Documento original
_Avoid_: Input, upload, file (in code)

**Born-digital PDF**:
A **Source Document** whose pages carry a text layer (the text can be selected). Can be translated in either
**Output Mode**.
_Spanish_: PDF digital
_Avoid_: Native PDF, text PDF

**Scanned PDF**:
A **Source Document** whose pages are images with no text layer, such as a photographed book. Gemini reads it;
it gets only the **Reading Version**.
_Spanish_: PDF escaneado
_Avoid_: Image PDF, OCR PDF

**Page Range**:
The pages of the **Source Document** a **Job** translates, by the original's page numbers. Empty means every page.
_Spanish_: Páginas
_Avoid_: Selection, chapter

### Output

**Output Mode**:
What the user chooses for a **Job**: the **Layout-kept Translation** or the **Reading Version**. The app
pre-selects the one that fits the **Source Document**.
_Spanish_: Formato del resultado

**Layout-kept Translation**:
The **Output Mode** that writes the Spanish over the original pages, keeping columns, figures and fonts. Only for
a **Born-digital PDF**. Produced by `pdf2zh-next`.
_Spanish_: Mantener el diseño
_Avoid_: Overlay, mono PDF

**Reading Version**:
The **Output Mode** that rebuilds the content as a new, clean document: headings, paragraphs, lists, the
**Figures** cropped from the original with their captions translated, and a **Page Marker** where each original
page starts. Written as a PDF and a DOCX from one Markdown.
_Spanish_: Versión de lectura con imágenes
_Avoid_: Reflow, rebuild (in the UI)

**Figure**:
A photo, diagram or chart on an original page, carried into the **Reading Version** as an image cropped from that
page. Text inside it stays as printed.
_Spanish_: Figura
_Avoid_: Image (for the whole page)

**Page Marker**:
The note in the **Reading Version** where an original page starts (`p. 130 del original`), so citations to the
original still work.
_Spanish_: Marca de página

### Running

**Job**:
One translation of one **Source Document**: its **Page Range**, its **Output Mode**, its progress page by page, and
its results. Survives closing the app.
_Spanish_: Traducción
_Avoid_: Task, run

**Page Batch**:
The pages of a **Scanned PDF** sent to Gemini in one request (four by default): it returns each page's text in the
original and in Spanish, and where its **Figures** are.
_Spanish_: (not shown)
_Avoid_: Chunk

**Model Chain**:
The Gemini models a **Job** uses in order. Each has its own free quota, so when one runs out the **Job** moves to
the next on the same key.
_Spanish_: (not shown)
_Avoid_: Fallback keys

**Quota Pause**:
The state of a **Job** when every model in the **Model Chain** is out of quota: it stops after its last finished
page and resumes from there, by itself, once quota is back.
_Spanish_: En pausa hasta que Gemini vuelva a estar disponible
_Avoid_: Failure, error
