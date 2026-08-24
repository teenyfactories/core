"""Tests for tf.chunk — the pure-stdlib text splitter (no DB, no embedding)."""

import teenyfactories as tf


def test_by_paragraphs_respects_max_chars():
    doc = "Para one is short.\n\n" + "B" * 3000 + "\n\nPara three.\n\nPara four is short."
    chunks = tf.chunk(doc).by_paragraphs(max_chars=500, min_chars=50)
    assert chunks, "expected at least one chunk"
    assert all(len(c["content"]) <= 500 for c in chunks), "a chunk exceeded max_chars"
    assert all(c["meta"] == {} for c in chunks), "plain chunks carry empty meta"


def test_by_paragraphs_keeps_normal_prose_whole():
    doc = "Short one.\n\nShort two.\n\nShort three."
    chunks = tf.chunk(doc).by_paragraphs(max_chars=2000, min_chars=10)
    # all three paragraphs fit in one chunk (glued back toward max_chars)
    assert len(chunks) == 1
    assert "Short one." in chunks[0]["content"] and "Short three." in chunks[0]["content"]


def test_by_chars_window_and_overlap():
    base = "abcdefghij" * 10  # 100 chars
    chunks = tf.chunk(base).by_chars(30).overlap(5)
    assert all(len(c["content"]) <= 30 for c in chunks)
    # step = size - overlap = 25 → second window starts at index 25
    assert chunks[1]["content"][0] == base[25]


def test_by_chars_rejects_bad_overlap():
    import pytest

    with pytest.raises(ValueError):
        tf.chunk("x" * 50).by_chars(10).overlap(10)


def test_by_markdown_headings_and_path():
    md = "# Title\nIntro.\n\n## Section A\nBody of A.\n\n## Section B\nBody of B."
    chunks = tf.chunk(md).by_markdown(max_chars=2000)
    headings = [c["meta"]["heading"] for c in chunks]
    assert "Title" in headings and "Section A" in headings and "Section B" in headings
    a = next(c for c in chunks if c["meta"]["heading"] == "Section A")
    assert a["meta"]["path"] == "Title > Section A"


def test_by_markdown_big_table_splits_per_row_with_header():
    rows = "\n".join("| %d | %d |" % (i, i) for i in range(200))
    md = "# T\n| a | b |\n| - | - |\n" + rows
    chunks = tf.chunk(md).by_markdown(max_chars=300)
    table_chunks = [c for c in chunks if "|" in c["content"] and c["content"] != "# T"]
    assert len(table_chunks) >= 100
    assert all("| a | b |" in c["content"] for c in table_chunks), "each table row keeps the header"


def test_by_markdown_no_headings_falls_back_to_paragraphs():
    chunks = tf.chunk("just prose\n\nwith two paras").by_markdown()
    assert len(chunks) >= 1


def test_empty_text():
    assert tf.chunk("").by_paragraphs() == []
    assert tf.chunk("").by_chars(100) == []
