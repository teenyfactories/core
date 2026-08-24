"""
Text chunking — pure stdlib, no tokenizer, no dependency, provider-neutral.

`tf.chunk(text)` returns a splitter; a preset method produces the chunks as
``list[{'content': str, 'meta': dict}]`` — the exact shape
``collection.set_vectors`` consumes. No embedding, no DB access here; splitting
is a pure text transform.

    import teenyfactories as tf

    chunks = tf.chunk(doc).by_paragraphs()              # prose, paragraph-aware
    chunks = tf.chunk(doc).by_chars(1000).overlap(100)  # fixed window + overlap
    chunks = tf.chunk(md).by_markdown()                 # heading-sectioned

    tf.collection('documents').set_vectors(key, chunks)

Why no tokenizer: tiktoken is OpenAI-only and cannot match every provider's
tokenizer, so a character/paragraph splitter is the honest provider-neutral
choice (and adds no dependency). Chunk sizes are in CHARACTERS.

All three presets run over ONE size-bounded recursive engine:
  - `by_paragraphs(max_chars, min_chars, on=[...])` — `on` is a PRIORITY LADDER
    of separators: split on the first (blank line), and only descend to the
    next (line, then space) for a piece that still exceeds `max_chars`; then
    glue adjacent pieces back up toward `max_chars`. Normal prose never
    word-chunks — the space rung is the fallback for one giant unbroken block.
  - `by_chars(size).overlap(k)` — fixed character window with optional overlap.
  - `by_markdown()` — heading-sectioned (`#`..`######`, auto-detected); each
    section carries its heading + heading path in `meta`; a section over
    `max_chars` is size-split by the same engine; a table longer than
    `max_chars` becomes header + one chunk per row. Fenced-code awareness is
    DEFERRED (for now a code fence splits like ordinary text).
"""

import re
from typing import List, Dict, Optional

__all__ = ["chunk"]

_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')


# ── recursive size-bounded engine ────────────────────────────────────────────
def _recursive_split(text: str, max_chars: int, seps: List[str]) -> List[str]:
    """Break `text` into atomic pieces each <= max_chars where possible, trying
    each separator in `seps` as a priority ladder; a piece still too big after
    the last separator is hard-cut on character boundaries."""
    if len(text) <= max_chars:
        return [text] if text else []
    if not seps:
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    sep, rest = seps[0], seps[1:]
    if sep == "":
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    pieces: List[str] = []
    for part in text.split(sep):
        if len(part) <= max_chars:
            if part:
                pieces.append(part)
        else:
            pieces.extend(_recursive_split(part, max_chars, rest))
    return pieces


def _merge(pieces: List[str], max_chars: int, min_chars: int, joiner: str) -> List[str]:
    """Glue adjacent atomic pieces back up toward `max_chars`, then fold any
    trailing chunk shorter than `min_chars` into its predecessor."""
    chunks: List[str] = []
    cur = ""
    for p in pieces:
        if not p:
            continue
        candidate = (cur + joiner + p) if cur else p
        if len(candidate) <= max_chars or not cur:
            cur = candidate
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    # Fold an undersized chunk into the previous one when it still fits under
    # max_chars (min_chars is a floor for readability; max_chars stays the hard
    # ceiling, so a short tail that can't fit is left standing alone).
    merged: List[str] = []
    for c in chunks:
        if merged and len(c) < min_chars and len(merged[-1]) + len(joiner) + len(c) <= max_chars:
            merged[-1] = merged[-1] + joiner + c
        else:
            merged.append(c)
    return merged


def _plain(content: str) -> Dict:
    return {"content": content, "meta": {}}


# ── markdown helpers ─────────────────────────────────────────────────────────
def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _split_markdown_section(body: str, max_chars: int, min_chars: int) -> List[str]:
    """Size-split one heading section. Table blocks stay atomic when they fit;
    a table longer than max_chars becomes its header + one chunk per row. Prose
    falls through to the recursive engine."""
    lines = body.split("\n")
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _is_table_row(lines[i]):
            j = i
            while j < n and _is_table_row(lines[j]):
                j += 1
            table = lines[i:j]
            block = "\n".join(table)
            if len(block) <= max_chars:
                out.append(block)
            else:
                # header = first two rows (column names + separator); each
                # remaining row becomes its own chunk, prefixed with the header
                # so a matched row is self-describing.
                header = table[:2]
                for row in table[2:]:
                    out.append("\n".join(header + [row]))
            i = j
        else:
            j = i
            while j < n and not _is_table_row(lines[j]):
                j += 1
            prose = "\n".join(lines[i:j]).strip()
            if prose:
                pieces = _recursive_split(prose, max_chars, ["\n\n", "\n", " "])
                out.extend(_merge(pieces, max_chars, min_chars, "\n\n"))
            i = j
    return out


# ── public splitter ──────────────────────────────────────────────────────────
class _CharChunks(list):
    """Result of `by_chars` — a plain list of chunk dicts that also offers a
    fluent `.overlap(k)` returning a re-windowed result."""

    def __init__(self, text: str, size: int, overlap: int = 0):
        self._text = text
        self._size = size
        self._overlap = overlap
        super().__init__(self._window())

    def _window(self) -> List[Dict]:
        size, ov = self._size, self._overlap
        if size <= 0:
            raise ValueError("by_chars size must be > 0")
        if ov < 0 or ov >= size:
            raise ValueError("overlap must be >= 0 and < size")
        step = size - ov
        text = self._text
        return [_plain(text[i:i + size]) for i in range(0, len(text), step)] if text else []

    def overlap(self, k: int) -> "_CharChunks":
        return _CharChunks(self._text, self._size, k)


class _Chunker:
    """Fluent splitter returned by `tf.chunk(text)`."""

    def __init__(self, text: str):
        self._text = text or ""

    def by_paragraphs(
        self,
        max_chars: int = 2000,
        min_chars: int = 200,
        on: Optional[List[str]] = None,
    ) -> List[Dict]:
        seps = list(on) if on else ["\n\n", "\n", " "]
        pieces = _recursive_split(self._text, max_chars, seps)
        joiner = seps[0] if seps and seps[0] else " "
        return [_plain(c) for c in _merge(pieces, max_chars, min_chars, joiner)]

    def by_chars(self, size: int, overlap: int = 0) -> _CharChunks:
        return _CharChunks(self._text, size, overlap)

    def by_markdown(self, max_chars: int = 2000, min_chars: int = 200) -> List[Dict]:
        lines = self._text.split("\n")
        chunks: List[Dict] = []
        stack: List[tuple] = []       # [(level, title), ...] ancestor headings
        buf: List[str] = []           # body lines of the current section
        cur_title = ""

        def flush():
            if not buf:
                return
            path = " > ".join(t for _, t in stack)
            meta = {"heading": cur_title, "path": path}
            section = "\n".join(buf).strip()
            if not section:
                return
            if len(section) <= max_chars:
                chunks.append({"content": section, "meta": meta})
            else:
                for piece in _split_markdown_section(section, max_chars, min_chars):
                    chunks.append({"content": piece, "meta": dict(meta)})

        for line in lines:
            m = _HEADING_RE.match(line)
            if m:
                flush()
                buf = [line]
                level = len(m.group(1))
                cur_title = m.group(2).strip()
                # pop deeper-or-equal headings, then push this one
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, cur_title))
            else:
                buf.append(line)
        flush()
        # A document with no headings at all → fall back to paragraph chunking.
        if not chunks:
            return self.by_paragraphs(max_chars, min_chars)
        return chunks


def chunk(text: str) -> _Chunker:
    """Start a chunking chain over `text`. Pick a preset — `.by_paragraphs()`,
    `.by_chars(n)`, or `.by_markdown()` — to get ``list[{'content','meta'}]``."""
    return _Chunker(text)
