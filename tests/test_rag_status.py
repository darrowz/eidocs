from eidocs.rag_worker import _map_query_mode


def test_map_query_mode_uses_hybrid_for_raganything():
    assert _map_query_mode("raganything") == "hybrid"
    assert _map_query_mode("hybrid") == "hybrid"
    assert _map_query_mode("local") == "local"
