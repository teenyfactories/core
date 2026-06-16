"""
tf.bucket_store(name) — file storage for a factory volume.

A "volume" is a named file space an operator defines in factory.yml; agents
read/write its contents through ``tf.bucket_store(name)``. The handle exposes
object-store-flavoured ops (list / read / open / write / delete / exists / url)
deliberately NOT a POSIX mount: no append, no rename, no locking. The name
``bucket_store`` signals those semantics so factory authors don't reach for
filesystem idioms the remote backend can't honour.

Two backends, selected at runtime by env (see ``_backend_kind``):

  LOCAL  (docker): operate directly on the bind-mounted volume at
                   ``/app/volumes/{name}/...``. This is the dev/local path —
                   the orchestrator bind-mounts the factory's volume folder
                   into the agent container exactly as before.

  REMOTE (k8s):    the agent holds no storage creds and mounts nothing. Every
                   op is an HTTP call to the orchestrator's internal-only
                   listener at ``http://orchestrator:8998`` (same trust anchor
                   as ``tf.secrets`` / the clearance gate — reachable only from
                   inside the private agent network; the orchestrator resolves
                   the caller's factory from the source IP). The orchestrator
                   performs the S3 op with ITS creds and enforces the agent's
                   per-volume read/write attachment from factory.yml.

Failure-mode policy (locked by tf-framework-architect — DELIBERATELY DIFFERENT
from tf.secrets / cost_clearance, which fail-OPEN):

    tf.bucket_store SURFACES errors. A missing file, a denied write, an
    unreachable orchestrator, or a 5xx all RAISE. Silently returning empty /
    succeeding (the secrets/clearance posture) would let an agent process the
    WRONG data — worse than a loud failure. File ops are correctness-critical,
    not best-effort enrichment. So:

        200 / 204      → success (200 read/list/exists; 204 write/delete)
        403            → BucketPermissionError (attachment denies this op,
                         incl. a write to a read-only attachment — the remote
                         backend collapses read-only-write into 403)
        404            → BucketNotFoundError (no such file/prefix)
        413            → BucketConflictError (write payload over the cap)
        other 4xx/5xx  → BucketStoreError
        network/timeout→ BucketStoreError
        feature off    → BucketStoreError (NOT a silent latch — if an operator
                         put the factory on the remote backend, the volume
                         endpoint MUST be there; its absence is a real fault)

All errors derive from ``BucketStoreError`` so callers can catch the family.

Usage:
    pdfs = tf.bucket_store('agreements')
    for path in pdfs.list():               # ['ae400398.pdf', 'sub/dir.pdf', ...]
        data = pdfs.read(path)             # bytes
        import fitz
        doc = fitz.open(stream=data, filetype='pdf')

    pdfs.write('summary.txt', b'...')      # raises if the attachment is read-only
    if pdfs.exists('report.pdf'):
        ...
    pdfs.delete('stale.pdf')

    # Streaming for large objects (context-manager; bytes-like file object):
    with pdfs.open('huge.pdf') as f:
        first = f.read(4096)
"""

import io
import os
from urllib.parse import quote

import requests

from .config import FACTORY_NAME, AGENT_SLUG

_DEFAULT_BASE_URL = 'http://orchestrator:8998'
# Generous vs the 2s secrets timeout — file payloads (PDFs) take longer than a
# key lookup. Still bounded so a wedged orchestrator surfaces as an error.
_TIMEOUT_SECONDS = 30.0
_LOCAL_ROOT = '/app/volumes'


# ── Error family ────────────────────────────────────────────────────────────


class BucketStoreError(Exception):
    """Base class for every tf.bucket_store failure. Catch this to handle the
    whole family."""


class BucketNotFoundError(BucketStoreError):
    """The requested path / prefix does not exist (404)."""


class BucketPermissionError(BucketStoreError):
    """The agent's attachment does not permit this op on this volume (403)."""


class BucketConflictError(BucketStoreError):
    """The op cannot be satisfied as requested — on the remote backend this is
    a write payload over the size cap (413). (A write to a read-only attachment
    surfaces as BucketPermissionError/403, not this.)"""


# ── Backend selection ───────────────────────────────────────────────────────


def _base_url() -> str:
    # Shares the single internal base-URL override knob with secrets.py /
    # cost_clearance.py — one address for every agent→orchestrator :8998 call.
    return os.getenv('TF_SECRETS_URL', _DEFAULT_BASE_URL).rstrip('/')


def _backend_kind() -> str:
    """Return 'local' or 'remote'.

    Signal: VOLUME_BACKEND, injected per-container by the orchestrator. The
    docker backend injects 'local' (bind mount present); the kubernetes backend
    injects 'remote' (no mount; proxy through :8998). If unset, infer from the
    presence of the bind-mounted root: a real /app/volumes dir ⇒ local, else
    remote. The explicit env wins so an operator can force either path.

    Naming coordinated with @environment-variable-architect (the BUCKET_* /
    VOLUME_BACKEND family).
    """
    explicit = (os.getenv('VOLUME_BACKEND') or '').strip().lower()
    if explicit in ('local', 'remote'):
        return explicit
    return 'local' if os.path.isdir(_LOCAL_ROOT) else 'remote'


def bucket_store(name: str):
    """Return a handle for the named factory volume.

    The volume must be declared in factory.yml's top-level ``volumes:`` and
    attached to this agent. Selecting the backend is automatic (see
    ``_backend_kind``). Raises ValueError on an empty name.
    """
    if not name or not isinstance(name, str):
        raise ValueError('bucket_store(name): name must be a non-empty string')
    if _backend_kind() == 'local':
        return _LocalBucket(name)
    return _RemoteBucket(name)


# ── Path hygiene (shared) ───────────────────────────────────────────────────


def _clean_path(path: str) -> str:
    """Normalise a caller-supplied object path. Forward slashes, no leading
    slash, reject traversal. The LOCAL backend additionally resolves+confines
    under the volume root; the REMOTE backend leaves prefix confinement to the
    orchestrator (defence in depth on both sides)."""
    p = (path or '').replace('\\', '/').lstrip('/')
    parts = [seg for seg in p.split('/') if seg not in ('', '.')]
    if any(seg == '..' for seg in parts):
        raise BucketStoreError(f'path traversal is not allowed: {path!r}')
    return '/'.join(parts)


# ── LOCAL backend (docker bind mount) ───────────────────────────────────────


class _LocalBucket:
    """Operates directly on the bind-mounted volume at /app/volumes/{name}."""

    def __init__(self, name: str):
        self._name = name
        self._root = os.path.realpath(os.path.join(_LOCAL_ROOT, name))

    def _abs(self, path: str) -> str:
        rel = _clean_path(path)
        full = os.path.realpath(os.path.join(self._root, rel))
        # Confinement: resolved path must stay under the volume root.
        if full != self._root and not full.startswith(self._root + os.sep):
            raise BucketStoreError(f'path escapes volume {self._name!r}: {path!r}')
        return full

    def list(self, prefix: str = ''):
        """Return object paths (relative to the volume root) under ``prefix``.
        Recurses — a directory tree is flattened to forward-slash paths, the
        same shape the remote (key-prefix) backend returns."""
        base = self._abs(prefix) if prefix else self._root
        if not os.path.isdir(base):
            # A prefix that names a file → just that file; missing → empty.
            if os.path.isfile(base):
                return [os.path.relpath(base, self._root).replace(os.sep, '/')]
            return []
        out = []
        for dirpath, _dirs, files in os.walk(base):
            for fname in files:
                full = os.path.join(dirpath, fname)
                out.append(os.path.relpath(full, self._root).replace(os.sep, '/'))
        return sorted(out)

    def read(self, path: str) -> bytes:
        full = self._abs(path)
        try:
            with open(full, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            raise BucketNotFoundError(f'{self._name}:{path} not found')
        except IsADirectoryError:
            raise BucketStoreError(f'{self._name}:{path} is a directory, not a file')

    def open(self, path: str):
        """Return a readable binary stream. Caller should use as a context
        manager. Raises BucketNotFoundError if absent."""
        full = self._abs(path)
        try:
            return open(full, 'rb')
        except FileNotFoundError:
            raise BucketNotFoundError(f'{self._name}:{path} not found')
        except IsADirectoryError:
            raise BucketStoreError(f'{self._name}:{path} is a directory, not a file')

    def write(self, path: str, data) -> None:
        full = self._abs(path)
        if isinstance(data, str):
            data = data.encode('utf-8')
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(data)

    def delete(self, path: str) -> None:
        full = self._abs(path)
        try:
            os.remove(full)
        except FileNotFoundError:
            raise BucketNotFoundError(f'{self._name}:{path} not found')
        except IsADirectoryError:
            raise BucketStoreError(f'{self._name}:{path} is a directory; delete files individually')

    def exists(self, path: str) -> bool:
        return os.path.isfile(self._abs(path))

    def url(self, path: str) -> str:
        """LOCAL has no externally-fetchable URL — return a file:// reference.
        Callers wanting a browser-openable link should go through the
        orchestrator file API (the explorer), not agent code."""
        return 'file://' + self._abs(path)


# ── REMOTE backend (k8s → orchestrator :8998 → S3) ──────────────────────────


class _RemoteBucket:
    """Proxies every op to the orchestrator's internal :8998 volume endpoint.

    Contract (LOCKED with @security-architect — matches secretsServer.js
    /volumes/* and reference_bucket_store_wire_contract.md):

        Base:   http://orchestrator:8998/volumes/{volume}/{op}
        Headers (defence-in-depth; factory is network-derived & trusted):
                X-Factory-Name, X-Agent-Slug
        Scope:  the orchestrator resolves the caller's factory from peer-IP,
                looks up {volume} + this agent's attachment mode in factory.yml,
                and enforces read/write before touching the fs / S3.

        GET    /volumes/{volume}/list?prefix=    → 200 {entries: [{name, path,
                                                       type, size, mtime}, ...]}
                                                   (single-level, not recursive)
        GET    /volumes/{volume}/read?path=      → 200 octet-stream (body=bytes)
        GET    /volumes/{volume}/exists?path=    → 200 {exists: bool}
        PUT    /volumes/{volume}/write?path=     → 204 (body=octet-stream)
        DELETE /volumes/{volume}/entry?path=     → 204 (idempotent)
        url()  → 501 (no presigned URL this release) ⇒ BucketStoreError

        Errors (generic envelope, never trusted for internals): 400 bad/traversal
        path · 403 denied attachment / read-only-write · 404 missing · 413 too
        large · 5xx/network/timeout. All RAISE (see module docstring).
    """

    def __init__(self, name: str):
        self._name = name

    def _headers(self):
        # Factory is network-derived (peer-IP) and authoritative; the slug is
        # defence-in-depth so the orchestrator can evaluate the per-agent
        # attachment. AGENT_SLUG (factory.yml key) — NOT the display name.
        return {
            'X-Factory-Name': FACTORY_NAME or '',
            'X-Agent-Slug': AGENT_SLUG or '',
        }

    def _op_url(self, op: str) -> str:
        # NOTE: path segment is /volumes/ (plural) to match secretsServer.js.
        return f'{_base_url()}/volumes/{quote(self._name, safe="")}/{op}'

    def _request(self, method: str, op: str, *, params=None, data=None, stream=False):
        url = self._op_url(op)
        try:
            resp = requests.request(
                method, url,
                params=params, data=data, stream=stream,
                headers=self._headers(), timeout=_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout as e:
            raise BucketStoreError(f'{self._name}: volume endpoint timed out') from e
        except requests.exceptions.RequestException as e:
            raise BucketStoreError(
                f'{self._name}: volume endpoint unreachable ({type(e).__name__})'
            ) from e

        # 200 (read/list/exists) and 204 (write/delete) are both success.
        if resp.status_code in (200, 204):
            return resp
        self._raise_for_status(resp, op)

    @staticmethod
    def _raise_for_status(resp, op):
        code = resp.status_code
        # Body is a generic error envelope; never trusted for internals.
        # 403 covers both "unattached / denied" and "write to a read-only
        # attachment" — the server collapses the read-only case into 403, so
        # map it to BucketPermissionError (no distinct 409 on this backend).
        if code == 403:
            raise BucketPermissionError(f'volume op {op!r} denied (403)')
        if code == 404:
            raise BucketNotFoundError(f'volume op {op!r} target not found (404)')
        if code == 400:
            raise BucketStoreError(f'volume op {op!r} rejected: bad path (400)')
        if code == 413:
            raise BucketConflictError(f'volume op {op!r} payload too large (413)')
        raise BucketStoreError(f'volume op {op!r} failed (HTTP {code})')

    def list(self, prefix: str = ''):
        """Return object PATHS (relative, forward-slash) under ``prefix``.

        The remote endpoint returns rich entry objects
        ``{name, path, type, size, mtime}`` and is single-level (delimiter
        depth, like an S3 listing). We project file entries to their ``path``
        so the handle's contract — a flat ``list[str]`` of object paths — is
        identical to the local backend's.
        """
        resp = self._request('GET', 'list', params={'prefix': _clean_path(prefix)})
        try:
            payload = resp.json()
        except ValueError as e:
            raise BucketStoreError(f'{self._name}: malformed list response') from e
        entries = payload.get('entries')
        if not isinstance(entries, list):
            raise BucketStoreError(f'{self._name}: list response missing entries')
        out = []
        for e in entries:
            if not isinstance(e, dict):
                raise BucketStoreError(f'{self._name}: malformed list entry')
            # Skip directory placeholders — list() yields object paths only.
            if e.get('type') == 'dir':
                continue
            p = e.get('path') or e.get('name')
            if p:
                out.append(p)
        return out

    def read(self, path: str) -> bytes:
        resp = self._request('GET', 'read', params={'path': _clean_path(path)})
        return resp.content

    def open(self, path: str):
        resp = self._request('GET', 'read', params={'path': _clean_path(path)}, stream=True)
        # Wrap the streamed response as a file-like object. raw is a urllib3
        # stream; decode_content makes gzip transparent. Buffer for .read(n).
        resp.raw.decode_content = True
        return _StreamingFile(resp)

    def write(self, path: str, data) -> None:
        if isinstance(data, str):
            data = data.encode('utf-8')
        # Raw octet-stream body via PUT; server replies 204 on success.
        self._request('PUT', 'write', params={'path': _clean_path(path)}, data=data)

    def delete(self, path: str) -> None:
        # DELETE /entry — idempotent (a missing target still 204s, mirroring
        # S3 DeleteObject), so delete() never raises BucketNotFoundError on
        # the remote backend.
        self._request('DELETE', 'entry', params={'path': _clean_path(path)})

    def exists(self, path: str) -> bool:
        resp = self._request('GET', 'exists', params={'path': _clean_path(path)})
        try:
            return bool(resp.json().get('exists'))
        except ValueError as e:
            raise BucketStoreError(f'{self._name}: malformed exists response') from e

    def url(self, path: str) -> str:
        # No presigned-URL support on the remote backend this release (the
        # server returns 501). Browser-openable links go through the
        # orchestrator file API (the explorer), not agent code.
        raise BucketStoreError(
            f'{self._name}: url() is not available on the remote backend '
            f'(no presigned URLs this release); use the file explorer'
        )


class _StreamingFile(io.RawIOBase):
    """Minimal read-only binary file wrapper over a streamed requests Response.
    Closing it releases the underlying connection."""

    def __init__(self, resp):
        self._resp = resp
        self._raw = resp.raw

    def readable(self) -> bool:
        return True

    def read(self, size=-1):
        if size is None or size < 0:
            return self._raw.read()
        return self._raw.read(size)

    def close(self):
        try:
            self._resp.close()
        finally:
            super().close()
