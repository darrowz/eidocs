from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import DocumentPolicyError
from .ids import sha256_file


ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
}

REJECTED_EXTENSIONS = {
    ".doc",
    ".xls",
    ".ppt",
    ".docm",
    ".xlsm",
    ".pptm",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".html",
    ".js",
    ".sh",
    ".exe",
    ".bin",
}


@dataclass(frozen=True)
class FileAssessment:
    path: str
    ext: str
    size_bytes: int
    mime_hint: str
    sha256: str
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "ext": self.ext,
            "size_bytes": self.size_bytes,
            "mime_hint": self.mime_hint,
            "sha256": self.sha256,
            "pages": self.pages,
            "warnings": list(self.warnings),
        }


@dataclass
class DocumentPolicy:
    max_file_bytes: int = 25 * 1024 * 1024
    max_pdf_pages: int = 120
    allowed_extensions: set[str] = field(default_factory=lambda: set(ALLOWED_EXTENSIONS))

    @classmethod
    def operator_large(cls) -> "DocumentPolicy":
        return cls(max_file_bytes=200 * 1024 * 1024, max_pdf_pages=1000)

    def validate_path(self, path: Path) -> FileAssessment:
        original = Path(path)
        if original.is_symlink():
            raise DocumentPolicyError("symbolic links are not accepted")
        resolved = original.expanduser().resolve()
        if not resolved.exists():
            raise DocumentPolicyError(f"file not found: {path}")
        if not resolved.is_file():
            raise DocumentPolicyError(f"not a regular file: {path}")
        ext = resolved.suffix.lower()
        if ext in REJECTED_EXTENSIONS:
            raise DocumentPolicyError(f"extension is explicitly rejected: {ext}")
        if ext not in self.allowed_extensions:
            raise DocumentPolicyError(f"unsupported extension: {ext}")
        size = resolved.stat().st_size
        if size <= 0:
            raise DocumentPolicyError("empty documents are not accepted")
        if size > self.max_file_bytes:
            raise DocumentPolicyError(f"file is too large: {size} > {self.max_file_bytes}")

        head = read_prefix(resolved, 4096)
        mime_hint = detect_magic(head)
        self._validate_magic(ext, mime_hint)
        warnings: list[str] = []
        pages = None
        if ext == ".pdf":
            sample = read_prefix(resolved, min(size, 2 * 1024 * 1024))
            if b"/Encrypt" in sample:
                raise DocumentPolicyError("encrypted PDFs are not accepted")
            pages = max(1, sample.count(b"/Type /Page"))
            if pages > self.max_pdf_pages:
                raise DocumentPolicyError(f"PDF page estimate exceeds limit: {pages} > {self.max_pdf_pages}")
            if pages == 1 and sample.count(b"/Type /Page") == 0:
                warnings.append("pdf_page_count_estimate_unavailable")
        return FileAssessment(
            path=str(resolved),
            ext=ext,
            size_bytes=size,
            mime_hint=mime_hint,
            sha256=sha256_file(resolved),
            pages=pages,
            warnings=warnings,
        )

    def _validate_magic(self, ext: str, mime_hint: str) -> None:
        if ext == ".pdf" and mime_hint != "application/pdf":
            raise DocumentPolicyError(f"PDF extension does not match magic: {mime_hint}")
        if ext in {".docx", ".pptx", ".xlsx"} and mime_hint != "application/zip":
            raise DocumentPolicyError(f"Office extension does not match zip magic: {mime_hint}")
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"} and not mime_hint.startswith("image/"):
            raise DocumentPolicyError(f"image extension does not match magic: {mime_hint}")
        if ext in {".txt", ".md", ".csv", ".json"} and mime_hint == "application/octet-stream":
            raise DocumentPolicyError("text-like document looks binary")


def read_prefix(path: Path, limit: int) -> bytes:
    with Path(path).open("rb") as fh:
        return fh.read(max(0, limit))


def detect_magic(head: bytes) -> str:
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return "application/zip"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image/tiff"
    if b"\x00" in head:
        return "application/octet-stream"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"
