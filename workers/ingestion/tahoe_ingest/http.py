"""Bounded public-source retrieval with verified TLS and a native curl fallback."""
from __future__ import annotations

import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse, quote

USER_AGENT = 'TahoePublicMeetings/0.1 (+https://tahoepublicmeetings.org)'


def public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('source URL must be public HTTPS without embedded credentials')
    if parsed.hostname == 'granicus_production_attachments.s3.amazonaws.com':
        parsed = urlparse('https://s3.amazonaws.com/granicus_production_attachments' + parsed.path + ('?' + parsed.query if parsed.query else ''))
    return urlunparse(parsed._replace(path=quote(parsed.path, safe="/%:@()+,;=-_~."), query=quote(parsed.query, safe="%&=,():;'/+?[]!$*~-_")))


def _native_fetch(url: str, max_bytes: int, accept: str, payload: bytes | None = None) -> tuple[bytes, str]:
    if not shutil.which('curl'):
        raise RuntimeError('Native TLS fallback requires curl; install curl and system CA certificates')
    with tempfile.TemporaryDirectory(prefix='tahoe-fetch-') as scratch:
        output = Path(scratch) / 'body'
        extra = []
        if payload is not None:
            body = Path(scratch) / 'request.json'
            body.write_bytes(payload)
            extra = ['--header', 'Content-Type: application/json', '--data-binary', '@' + str(body)]
        run = subprocess.run(['curl', '--globoff', '--fail', '--silent', '--show-error', '--location',
            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15', '--max-time', '60',
            '--max-filesize', str(max_bytes), '--user-agent', USER_AGENT,
            '--header', 'Accept: ' + accept, '--output', str(output), '--write-out', '%{content_type}', *extra, url],
            capture_output=True, text=True, timeout=65)
        if run.returncode:
            raise RuntimeError(f'Public source download failed: {run.stderr.strip()[:400]}')
        if output.stat().st_size > max_bytes:
            raise ValueError(f'source exceeds {max_bytes} byte limit')
        return output.read_bytes(), run.stdout.strip()


def fetch_bytes(url: str, *, max_bytes: int, accept: str = '*/*', payload: bytes | None = None) -> tuple[bytes, str]:
    url = public_url(url)
    headers = {'User-Agent': USER_AGENT, 'Accept': accept}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, headers=headers, data=payload)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if urlparse(response.geturl()).scheme != 'https':
                raise ValueError('source redirected outside HTTPS')
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f'source exceeds {max_bytes} byte limit')
            return data, response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as exc:
        # One attempt with native transport may work on public DocAccess sites.
        # Authentication failures and rate limits are surfaced, never retried.
        if exc.code != 403:
            raise
        return _native_fetch(url, max_bytes, accept, payload)
    except (ssl.SSLError, urllib.error.URLError) as exc:
        reason = getattr(exc, 'reason', exc)
        if not isinstance(reason, ssl.SSLError):
            raise
        return _native_fetch(url, max_bytes, accept, payload)
