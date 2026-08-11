"""Secure local, upload, URL, and arXiv paper source resolution."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import urllib.parse
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Protocol

from backend.app.domain import PaperReference, PaperSourceType
from backend.app.services import (
    InvalidPaperSourceError,
    PaperDownloadError,
    PaperIngestionSettings,
    PaperSourceResolver,
    ResolvedPaperSource,
    UnsafePaperSourceError,
)

_ARXIV_ID = re.compile(
    r"^(?:arxiv:)?((?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v\d+)?)$",
    re.IGNORECASE,
)
_ARXIV_URL = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([^?#]+?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: BinaryIO


class HttpTransport(Protocol):
    def open(self, url: str, timeout: float, resolved_ip: str) -> HttpResponse: ...


class PinnedIpHttpTransport:
    """Pinned-IP HTTP adapter; redirects remain visible for re-validation."""

    def open(self, url: str, timeout: float, resolved_ip: str) -> HttpResponse:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        default_port = 443 if parsed.scheme == "https" else 80
        host_header = host if port == default_port else f"{host}:{port}"
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _PinnedHTTPSConnection(host, resolved_ip, port, timeout)
        else:
            connection = _PinnedHTTPConnection(host, resolved_ip, port, timeout)
        try:
            connection.request(
                "GET",
                target,
                headers={"Accept": "application/pdf", "User-Agent": "PaperReproAgent/0.1", "Host": host_header},
            )
            response = connection.getresponse()
        except (http.client.HTTPException, TimeoutError, OSError, ssl.SSLError) as exc:
            connection.close()
            raise PaperDownloadError(f"paper download failed: {exc}") from exc
        return HttpResponse(response.status, dict(response.getheaders()), response)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, resolved_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._resolved_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, resolved_ip: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._resolved_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


# Compatibility name retained for Task 04 callers that imported the first adapter name.
UrllibHttpTransport = PinnedIpHttpTransport


class SecurePaperSourceResolver(PaperSourceResolver):
    def __init__(
        self,
        settings: PaperIngestionSettings | None = None,
        *,
        http: HttpTransport | None = None,
        dns_resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    ) -> None:
        self.settings = settings or PaperIngestionSettings()
        self.http = http or PinnedIpHttpTransport()
        self._dns_resolver = dns_resolver

    def resolve(
        self,
        paper: PaperReference,
        *,
        upload: bytes | bytearray | memoryview | BinaryIO | None = None,
    ) -> ResolvedPaperSource:
        if paper.source_type is PaperSourceType.LOCAL_FILE:
            data = self._read_local(Path(paper.source_uri or ""))
            uri = str(Path(paper.source_uri or "").resolve())
            filename = Path(uri).name
        elif paper.source_type is PaperSourceType.PDF_UPLOAD:
            if upload is None:
                raise InvalidPaperSourceError("PDF upload bytes or stream are required")
            data = self._read_bounded(upload)
            uri = paper.source_uri or f"upload:{paper.id}"
            filename = self._safe_filename(uri, f"{paper.id}.pdf")
        elif paper.source_type is PaperSourceType.ARXIV:
            arxiv_id = self._normalize_arxiv_id(paper.arxiv_id or paper.source_uri or "")
            uri = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            data = self._download(uri)
            filename = f"{arxiv_id}.pdf"
        elif paper.source_type is PaperSourceType.URL:
            uri = paper.source_uri or ""
            data = self._download(uri)
            filename = self._safe_filename(uri, f"{paper.id}.pdf")
        else:
            raise InvalidPaperSourceError(f"unsupported paper source: {paper.source_type}")

        self._validate_pdf(data)
        return ResolvedPaperSource(
            data=data,
            source_uri=uri,
            filename=filename,
            content_hash=hashlib.sha256(data).hexdigest(),
        )

    def _read_local(self, path: Path) -> bytes:
        try:
            if not path.is_file():
                raise InvalidPaperSourceError(f"paper file does not exist: {path}")
            if path.stat().st_size > self.settings.max_file_size_bytes:
                raise InvalidPaperSourceError("paper exceeds maximum file size")
            with path.open("rb") as stream:
                return self._read_bounded(stream)
        except OSError as exc:
            raise InvalidPaperSourceError(f"cannot read paper file: {exc}") from exc

    def _read_bounded(self, source: bytes | bytearray | memoryview | BinaryIO) -> bytes:
        limit = self.settings.max_file_size_bytes
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
        else:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = source.read(min(64 * 1024, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    break
            data = b"".join(chunks)
        if len(data) > limit:
            raise InvalidPaperSourceError("paper exceeds maximum file size")
        return data

    def _download(self, initial_url: str) -> bytes:
        url = initial_url
        for redirect_count in range(self.settings.max_redirects + 1):
            validated_addresses = self._validate_remote_url(url)
            response = self.http.open(url, self.settings.download_timeout_seconds, validated_addresses[0])
            try:
                if 300 <= response.status < 400:
                    location = self._header(response.headers, "location")
                    if not location:
                        raise PaperDownloadError("redirect response has no Location header")
                    if redirect_count == self.settings.max_redirects:
                        raise PaperDownloadError("paper download exceeded redirect limit")
                    url = urllib.parse.urljoin(url, location)
                    continue
                if response.status != 200:
                    raise PaperDownloadError(f"paper download returned HTTP {response.status}")
                content_type = self._header(response.headers, "content-type").split(";", 1)[0].strip().lower()
                if content_type != "application/pdf":
                    raise PaperDownloadError(f"unexpected Content-Type: {content_type or 'missing'}")
                length = self._header(response.headers, "content-length")
                if length:
                    try:
                        if int(length) > self.settings.max_file_size_bytes:
                            raise PaperDownloadError("paper download exceeds maximum file size")
                    except ValueError as exc:
                        raise PaperDownloadError("invalid Content-Length header") from exc
                try:
                    return self._read_bounded(response.body)
                except InvalidPaperSourceError as exc:
                    raise PaperDownloadError(str(exc)) from exc
            finally:
                response.body.close()
        raise PaperDownloadError("paper download redirect loop")

    def _validate_remote_url(self, url: str) -> tuple[str, ...]:
        parsed = urllib.parse.urlsplit(url)
        allowed = {"https"} | ({"http"} if self.settings.allow_http else set())
        if parsed.scheme.lower() not in allowed:
            raise UnsafePaperSourceError("only permitted HTTP(S) schemes may be downloaded")
        if parsed.username or parsed.password or not parsed.hostname:
            raise UnsafePaperSourceError("URL credentials and missing hostnames are forbidden")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise UnsafePaperSourceError("localhost paper URLs are forbidden")
        try:
            literal_ip = ipaddress.ip_address(hostname.split("%", 1)[0])
        except ValueError:
            literal_ip = None
        if literal_ip is not None:
            if not literal_ip.is_global:
                raise UnsafePaperSourceError(f"non-public destination is forbidden: {literal_ip}")
            return (str(literal_ip),)
        try:
            addresses = self._dns_resolver(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise PaperDownloadError(f"paper hostname cannot be resolved: {hostname}") from exc
        if not addresses:
            raise PaperDownloadError(f"paper hostname has no addresses: {hostname}")
        validated: list[str] = []
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
            if not ip.is_global:
                raise UnsafePaperSourceError(f"non-public destination is forbidden: {ip}")
            value = str(ip)
            if value not in validated:
                validated.append(value)
        return tuple(validated)

    @staticmethod
    def _normalize_arxiv_id(value: str) -> str:
        match = _ARXIV_ID.fullmatch(value.strip()) or _ARXIV_URL.fullmatch(value.strip())
        if not match:
            raise InvalidPaperSourceError("invalid arXiv identifier or URL")
        arxiv_id = match.group(1)
        if not _ARXIV_ID.fullmatch(arxiv_id):
            raise InvalidPaperSourceError("invalid arXiv identifier")
        return arxiv_id

    @staticmethod
    def _validate_pdf(data: bytes) -> None:
        if not data.startswith(b"%PDF-"):
            raise InvalidPaperSourceError("source does not have a PDF magic header")

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        return next((value for key, value in headers.items() if key.lower() == name), "")

    @staticmethod
    def _safe_filename(uri: str, default: str) -> str:
        name = Path(urllib.parse.unquote(urllib.parse.urlsplit(uri).path)).name
        return name if name.lower().endswith(".pdf") else default
