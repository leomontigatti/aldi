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
What the user chooses for a **Job**: the **Reading Version**, pre-selected for every **Source Document**, or the
**Layout-kept Translation**, offered only when every page in the **Page Range** has a text layer.
_Spanish_: Formato del resultado

**Layout-kept Translation**:
The **Output Mode** that writes the Spanish over the original pages, keeping columns, figures and fonts. Only for
a **Born-digital PDF**. Produced by `pdf2zh-next`.
_Spanish_: Mantener el diseño
_Avoid_: Overlay, mono PDF

**Reading Version**:
The **Output Mode** that rebuilds the content as a new, clean document in one column, left-aligned: headings,
paragraphs, lists, the **Figures** taken from the original with their captions translated, and a **Page Marker**
where each original page starts, with the references left in their original language. Written as a PDF and a DOCX.
_Spanish_: Versión de lectura con imágenes
_Avoid_: Reflow, rebuild (in the UI)

**Figure**:
A photo, diagram or chart on an original page, carried into the **Reading Version** as an image cropped from that
page. Text inside it stays as printed.
_Spanish_: Figura
_Avoid_: Image (for the whole page)

**Release**:
A version of the app the user has checked and tagged. Her laptop offers to update only to a **Release**.
_Spanish_: Versión nueva (`Hay una versión nueva — Actualizar`)
_Avoid_: Build, commit

**Page Marker**:
The note in the **Reading Version** where an original page starts (`p. 130 del original`), so citations to the
original still work.
_Spanish_: Marca de página

### Running

**Job**:
One translation of one **Source Document**: its **Page Range**, its **Output Mode**, its progress page by page, and
its results. Survives closing the app. Jobs translate one at a time, in the order dropped; until one finishes,
`Cancelar` stops it and drops it with its working files.
_Spanish_: Traducción
_Avoid_: Task, run

**Page Batch**:
The pages of a **Scanned PDF** that Gemini reads and translates in one request (four by default): it returns
each page's Spanish and where its **Figures** are.
_Spanish_: (not shown)
_Avoid_: Chunk

**Term List**:
The technical terms and acronyms a **Job** has translated so far, with the Spanish it chose, so a term reads the
same on every page.
_Spanish_: (not shown)
_Avoid_: Glossary (that is this file)

**Failed Page**:
A page that still fails after its retries. The result carries the original page's image with `Esta página no se
pudo traducir`, and the **Job** offers to try those pages again.
_Spanish_: Página fallida (`Reintentar páginas fallidas`)
_Avoid_: Skipped page

**Model Chain**:
The Gemini models a **Job** uses in order. Each has its own free quota, so when one runs out the **Job** moves to
the next on the same key.
_Spanish_: (not shown)
_Avoid_: Fallback keys

**Quota Pause**:
The state of a **Job** when every model in the **Model Chain** is out of quota: it stops after its last finished
page and resumes from there, by itself, once quota is back. Meanwhile the next **Job** in line translates.
_Spanish_: En pausa hasta que Gemini vuelva a estar disponible
_Avoid_: Failure, error

**Error Report**:
What the app files on its own when something goes wrong that is a bug, such as a crash or a **Failed Page**: a
public issue on the app's repo with what is needed to fix it and nothing from the document. A **Quota Pause** is
not one.
_Spanish_: Informe (`Ya se envió un informe para que lo arreglen.`)
_Avoid_: Crash report, telemetry
