# tf-volumes — the bucket store: file volumes, handles, errors

`tf.bucket_store(name)` is the agent-facing API for a factory **volume** — a named file space an operator defines in `factory.yml` (see the factory.yml schema reference, `read_reference_factory_yaml`, § Volumes). Use it instead of raw `open()` / `os.scandir` / `os.walk` against `/app/volumes/...`; that direct access is retired (it only works on the docker backend, and binds you to a filesystem the k8s backend doesn't mount).

```python
pdfs = tf.bucket_store('agreements')

for path in pdfs.list():               # ['ae400398.pdf', 'sub/dir.pdf', ...] — recursive, forward-slash
    data = pdfs.read(path)             # bytes
    import fitz
    doc = fitz.open(stream=data, filetype='pdf')

pdfs.write('summary.txt', b'...')      # str is utf-8 encoded for you
if pdfs.exists('report.pdf'):
    pdfs.delete('stale.pdf')

with pdfs.open('huge.pdf') as f:       # streaming context manager for large objects
    head = f.read(4096)
```

## Handle methods

| Method | Returns | Notes |
|---|---|---|
| `list(prefix='')` | `list[str]` | object paths relative to the volume root, forward-slash separated. **Recursive on docker** (filesystem walk); **single-level on k8s** (object-store delimiter listing) — don't rely on deep recursion across backends. A missing prefix returns `[]`. |
| `read(path)` | `bytes` | whole-object read. Raises `BucketNotFoundError` if absent. |
| `open(path)` | binary stream | context-managed file-like object; closing releases the connection. For large objects. |
| `write(path, data)` | `None` | `data` is `bytes` or `str` (utf-8). Raises if the agent's attachment is read-only. |
| `delete(path)` | `None` | docker: raises `BucketNotFoundError` if absent. k8s: idempotent (a missing target is a no-op). |
| `exists(path)` | `bool` | |
| `url(path)` | `str` | docker: a `file://` reference (not browser-openable). **k8s: not available this release — raises `BucketStoreError`.** For browser links go through the explorer's file API, not agent code. |

## Errors — `tf.bucket_store` SURFACES failures (unlike `tf.secrets`)

This is the deliberate opposite of `tf.secrets` / the clearance gate, which **fail-open** (silently fall back). File ops are correctness-critical — silently returning empty or pretending a write succeeded would let an agent process the **wrong** data, which is worse than a loud failure. So every failure raises:

| Condition | Exception |
|---|---|
| no such file/prefix | `BucketNotFoundError` |
| attachment denies this op (unattached volume **or** write to a read-only attachment) | `BucketPermissionError` |
| write payload over the size cap (k8s) | `BucketConflictError` |
| bad path / any other 4xx/5xx, network, timeout, feature-off | `BucketStoreError` |

All four derive from `BucketStoreError`, so `except tf.BucketStoreError` catches the whole family. Catch the specific subclass when you want to branch (e.g. treat a missing file as skippable but a permission error as fatal).

## Backends (transparent to agent code)

The same agent code runs on both backends — selected per-container by the orchestrator via the `VOLUME_BACKEND` env var:

- **docker → `local`**: operate directly on the bind-mounted volume at `/app/volumes/{name}`.
- **k8s → `remote`**: the agent holds no storage creds and mounts nothing. Every op is an HTTP call to the orchestrator's internal `:8998` listener (same trust anchor as `tf.secrets`), which performs the S3 op with its creds and enforces the agent's per-volume read/write attachment from `factory.yml`. The orchestrator advertises the agent's allowed volumes via `TF_VOLUME_ATTACHMENTS`, but enforcement is server-side.

Object-store semantics (relevant on k8s): no atomic rename, no append, no file locking; large-prefix `list()` is slower. Fine for documents/PDFs. If a future agent needs append/locking, flag it — that's outside the `bucket_store` contract.
