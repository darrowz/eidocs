import os

import pytest

from eidocs.errors import DocumentPolicyError
from eidocs.security import DocumentPolicy


def test_policy_rejects_magic_mismatch(tmp_path):
    doc = tmp_path / "fake.pdf"
    doc.write_text("not pdf", encoding="utf-8")
    with pytest.raises(DocumentPolicyError):
        DocumentPolicy().validate_path(doc)


def test_policy_rejects_oversized_file(tmp_path):
    doc = tmp_path / "big.txt"
    doc.write_text("hello", encoding="utf-8")
    with pytest.raises(DocumentPolicyError):
        DocumentPolicy(max_file_bytes=1).validate_path(doc)


def test_policy_rejects_symlink(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(DocumentPolicyError):
        DocumentPolicy().validate_path(link)
