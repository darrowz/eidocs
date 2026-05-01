from eidocs.parsers.rag_subprocess import _blocks_from_content_list


def test_blocks_from_raganything_content_list_preserves_modalities():
    blocks = _blocks_from_content_list(
        "doc_1",
        [
            {"type": "text", "text": "hello", "page_idx": 0},
            {"type": "table", "table_body": "| a | b |", "table_caption": ["cap"], "page_idx": 1},
            {"type": "equation", "latex": "x+y", "page_idx": 2},
        ],
    )
    assert [block.type for block in blocks] == ["text", "table", "equation"]
    assert blocks[1].caption == ["cap"]
    assert blocks[2].latex == "x+y"
