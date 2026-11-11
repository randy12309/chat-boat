"""Extract plain text from uploaded PDF, DOCX and TXT files and split it into chunks for embedding."""
import io
import re
import zipfile

from pypdf import PdfReader

ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt")
MAX_PDF_PAGES = 500
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # a DOCX is a zip; refuse decompression bombs
MAX_CHARS = 2_000_000


class DocumentError(ValueError):
    """The file could not be read as the document type its extension claims."""


def _pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise DocumentError("Encrypted PDFs are not supported")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentError(f"PDF has more than {MAX_PDF_PAGES} pages")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _docx(data: bytes) -> str:
    from docx import Document

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DocumentError("DOCX is too large when unpacked")
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        parts.extend(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows)
    return "\n\n".join(parts)


def _txt(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def extract_text(filename: str, data: bytes) -> str:
    """Text of the file, chosen by extension. Raises DocumentError when it is unreadable or has no text."""
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    reader = {".pdf": _pdf, ".docx": _docx, ".txt": _txt}.get(extension)
    if reader is None:
        raise DocumentError("Unsupported file type")
    try:
        text = reader(data)
    except DocumentError:
        raise
    except Exception as exc:  # corrupt or hostile files raise many library-specific errors
        raise DocumentError(f"Could not read the {extension[1:].upper()} file") from exc
    text = text.replace("\x00", "")
    if not text.strip():
        raise DocumentError("The file contains no extractable text")
    if len(text) > MAX_CHARS:
        raise DocumentError("The file has too much text")
    return text


def chunk_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Pack paragraphs into chunks of about `size` characters, repeating `overlap` characters between neighbours."""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        while len(paragraph) > size:
            cut = paragraph.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            units.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        if paragraph:
            units.append(paragraph)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 1 + len(unit) > size:
            chunks.append(current)
            tail = current[-overlap:]
            current = tail[tail.find(" ") + 1:] if " " in tail else tail
        current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    return chunks
