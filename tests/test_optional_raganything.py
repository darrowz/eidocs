import pytest

from eidocs.adapters import is_raganything_available
from eidocs.adapters.raganything_adapter import RAGAnythingAdapter
from eidocs.errors import RAGAnythingUnavailable


def test_import_without_raganything_is_safe(tmp_path):
    available = is_raganything_available()
    if available:
        pytest.skip("raganything is installed in this environment")
    with pytest.raises(RAGAnythingUnavailable):
        RAGAnythingAdapter(tmp_path)
