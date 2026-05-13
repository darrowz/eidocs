"""Tests for eidocs schema models and raganything payload conversion."""

from pathlib import Path

import pytest

from eidocs.schema import ContentBlock, to_raganything_content_list


def test_content_block_to_raganything_requires_absolute_image_path():
    block = ContentBlock(block_id="b1", doc_id="d1", type="image", page_idx=0, order=0, img_path="relative.png")
    with pytest.raises(ValueError):
        block.to_raganything_content()


def test_content_blocks_convert_to_raganything_payload(tmp_path):
    image = tmp_path / "plot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    blocks = [
        ContentBlock(block_id="t", doc_id="d", type="text", page_idx=0, order=0, text="hello"),
        ContentBlock(block_id="i", doc_id="d", type="image", page_idx=0, order=1, img_path=str(image.resolve()), caption=["plot"]),
    ]
    payload = to_raganything_content_list(blocks)
    assert payload[0]["type"] == "text"
    assert payload[1]["img_path"] == str(image.resolve())
