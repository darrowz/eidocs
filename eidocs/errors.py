class EiDocsError(Exception):
    """Base error for eidocs."""


class UnsupportedDocumentType(EiDocsError):
    """Raised when the configured parser cannot parse a document."""


class DocumentPolicyError(EiDocsError):
    """Raised when a document violates ingest policy."""


class RAGAnythingUnavailable(EiDocsError):
    """Raised when RAG-Anything features are requested but unavailable."""
