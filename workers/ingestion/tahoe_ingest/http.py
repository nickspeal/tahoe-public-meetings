"""Bounded public-source retrieval with verified TLS and a native curl fallback."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse, urlunparse, quote

USER_AGENT = 'TahoePublicMeetings/0.1 (+https://tahoepublicmeetings.org)'
SKIPPED_ENDPOINTS = 'config/skipped-endpoints.json'
CHUNK_BYTES = 1024 * 1024  # a streamed download holds at most this much of the body in memory


class EndpointSkipped(RuntimeError):
    """The URL is listed in the skip configuration, so no request was made."""


class SourceTooLarge(ValueError):
    """The source is over a size limit; fetching it again would fail the same way.

    Raised for a response larger than the caller's ``max_bytes`` (from its
    Content-Length header before the body is read, or while reading), and as
    ``source.TooManyPages`` for a PDF over the page limit.
    """


def _skip_file() -> Path:
    return Path(os.environ.get('TAHOE_SOURCE_ROOT', '.')) / SKIPPED_ENDPOINTS


def skipped_endpoint(url: str) -> str | None:
    """The reason a URL is configured to be skipped, or None.

    Entries in `config/skipped-endpoints.json` match by URL prefix. Delete an
    entry (or the file) to try the endpoint again. A missing file skips nothing.
    """
    try:
        entries = json.loads(_skip_file().read_text()).get('skipped', [])
    except FileNotFoundError:
        return None
    for entry in entries:
        if url.startswith(entry['prefix']):
            return f"{entry['prefix']} is skipped (HTTP {entry.get('status', '?')} since {entry.get('since', '?')}): {entry.get('reason', 'no reason recorded')}"
    return None


def public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('source URL must be public HTTPS without embedded credentials')
    if parsed.hostname == 'granicus_production_attachments.s3.amazonaws.com':
        parsed = urlparse('https://s3.amazonaws.com/granicus_production_attachments' + parsed.path + ('?' + parsed.query if parsed.query else ''))
    return urlunparse(parsed._replace(path=quote(parsed.path, safe="/%:@()+,;=-_~."), query=quote(parsed.query, safe="%&=,():;'/+?[]!$*~-_")))


def _curl(url: str, output: Path, max_bytes: int, accept: str, payload: bytes | None = None) -> str:
    """Download ``url`` into ``output`` with native curl (verified TLS, HTTPS only) and return its content type."""
    if not shutil.which('curl'):
        raise RuntimeError('Native TLS fallback requires curl; install curl and system CA certificates')
    extra = []
    if payload is not None:
        body = output.with_name(output.name + '.request.json')
        body.write_bytes(payload)
        extra = ['--header', 'Content-Type: application/json', '--data-binary', '@' + str(body)]
    run = subprocess.run(['curl', '--globoff', '--fail', '--silent', '--show-error', '--location',
        '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15', '--max-time', '60',
        '--max-filesize', str(max_bytes), '--user-agent', USER_AGENT,
        '--header', 'Accept: ' + accept, '--output', str(output), '--write-out', '%{content_type}', *extra, url],
        capture_output=True, text=True, timeout=65)
    if run.returncode == 63:  # curl: --max-filesize exceeded
        raise SourceTooLarge(f'source exceeds {max_bytes} byte limit')
    if run.returncode:
        raise RuntimeError(f'Public source download failed: {run.stderr.strip()[:400]}')
    if output.stat().st_size > max_bytes:
        raise SourceTooLarge(f'source exceeds {max_bytes} byte limit')
    return run.stdout.strip()


def _native_fetch(url: str, max_bytes: int, accept: str, payload: bytes | None = None) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory(prefix='tahoe-fetch-') as scratch:
        output = Path(scratch) / 'body'
        content_type = _curl(url, output, max_bytes, accept, payload)
        return output.read_bytes(), content_type


def _checked_url(url: str) -> str:
    url = public_url(url)
    if reason := skipped_endpoint(url):
        raise EndpointSkipped(reason)
    return url


def _check_response(response, max_bytes: int) -> None:
    """Refuse a redirect off HTTPS, and a declared Content-Length over the limit before any of the body is read."""
    if urlparse(response.geturl()).scheme != 'https':
        raise ValueError('source redirected outside HTTPS')
    declared = response.headers.get('Content-Length', '')
    if declared.strip().isdigit() and int(declared) > max_bytes:
        # Servers that omit the header are bounded while the body is read.
        raise SourceTooLarge(f'source exceeds {max_bytes} byte limit ({int(declared)} bytes declared)')


def _native_fallback(exc: Exception) -> bool:
    """One attempt with native transport may work on public DocAccess sites (HTTP 403) and hosts whose TLS chain
    Python can't verify. Authentication failures and rate limits are surfaced, never retried."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 403
    if isinstance(exc, urllib.error.URLError):
        return isinstance(getattr(exc, 'reason', exc), ssl.SSLError)
    return isinstance(exc, ssl.SSLError)


def fetch_bytes(url: str, *, max_bytes: int, accept: str = '*/*', payload: bytes | None = None) -> tuple[bytes, str]:
    """The whole response body in memory, for small pages and API responses; large files use ``download``."""
    url = _checked_url(url)
    headers = {'User-Agent': USER_AGENT, 'Accept': accept}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, headers=headers, data=payload)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            _check_response(response, max_bytes)
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise SourceTooLarge(f'source exceeds {max_bytes} byte limit')
            return data, response.headers.get('Content-Type', '')
    except (ssl.SSLError, urllib.error.URLError) as exc:
        if not _native_fallback(exc):
            raise
        return _native_fetch(url, max_bytes, accept, payload)


@dataclass(frozen=True)
class Download:
    """A response body on disk (in the current temp dir), with its SHA-256, size, and declared content type."""
    path: Path
    sha256: str
    size: int
    content_type: str

    def head(self, size: int = 1024) -> bytes:
        with self.path.open('rb') as body:
            return body.read(size)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as body:
        while chunk := body.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stream(url: str, output: Path, max_bytes: int, accept: str) -> tuple[str, int, str]:
    """Write the body to ``output`` in chunks: ``(sha256, size, content type)``; over ``max_bytes`` stops the read."""
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    try:
        with urllib.request.urlopen(request, timeout=45) as response, output.open('wb') as body:
            _check_response(response, max_bytes)
            digest, size = hashlib.sha256(), 0
            while chunk := response.read(CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise SourceTooLarge(f'source exceeds {max_bytes} byte limit')
                digest.update(chunk)
                body.write(chunk)
            return digest.hexdigest(), size, response.headers.get('Content-Type', '')
    except (ssl.SSLError, urllib.error.URLError) as exc:
        if not _native_fallback(exc):
            raise
        content_type = _native_download(url, output, max_bytes, accept)
        return _hash_file(output), output.stat().st_size, content_type


def _native_download(url: str, output: Path, max_bytes: int, accept: str) -> str:
    return _curl(url, output, max_bytes, accept)


@contextmanager
def download(url: str, *, max_bytes: int, accept: str = '*/*') -> Iterator[Download]:
    """Stream a file to a scratch directory under the current temp dir, never holding the whole body in memory.

    The same checks as ``fetch_bytes`` apply (public HTTPS, skipped endpoints, no redirect off
    HTTPS, ``max_bytes`` from Content-Length and while reading, the native curl fallback). The
    file and its directory are removed when the block exits, whether or not it raised.
    """
    url = _checked_url(url)
    with tempfile.TemporaryDirectory(prefix='tahoe-fetch-') as scratch:
        output = Path(scratch) / 'body'
        sha256, size, content_type = _stream(url, output, max_bytes, accept)
        yield Download(output, sha256, size, content_type)
