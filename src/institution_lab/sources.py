from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from .config import ResearchConfig
from .recording import redact

BYTE_LIMIT = 1_048_576
TEXT_LIMIT = 60000


class SourceRejected(ValueError):
    pass


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)

    def handle_data(self, data: str) -> None:
        if not self.suppressed and data.strip():
            self.parts.append(data.strip())


def approved_url(url: str, config: ResearchConfig) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SourceRejected("malformed_public_source_url") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or port not in {None, 80, 443}
        or not parsed.hostname
    ):
        raise SourceRejected("url_requires_public_http_or_https_without_credentials")
    if any(
        key.lower()
        in {
            "token",
            "access_token",
            "api_key",
            "apikey",
            "password",
            "secret",
            "authorization",
            "signature",
            "sig",
        }
        for key, _ in parse_qsl(parsed.query)
    ):
        raise SourceRejected("public_source_query_must_not_contain_credentials")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost"):
            raise SourceRejected("source_host_is_a_nonpublic_address")
    else:
        if not address.is_global:
            raise SourceRejected("source_host_is_a_nonpublic_address")
    return url


def public_address(host: str) -> None:
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise SourceRejected("source_host_resolves_to_a_nonpublic_address")


class SourceReader:
    def __init__(self, config: ResearchConfig, directory: Path) -> None:
        self.config = config
        self.directory = directory

    async def fetch(self, url: str) -> dict:
        import asyncio

        original = approved_url(url, self.config)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                for _ in range(4):
                    approved_url(url, self.config)
                    host = urlsplit(url).hostname
                    assert host is not None
                    await asyncio.to_thread(public_address, host)
                    async with client.stream(
                        "GET", url, headers={"Accept": "text/html,text/plain"}
                    ) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SourceRejected("source_redirect_without_location")
                            url = urljoin(url, location)
                            continue
                        response.raise_for_status()
                        mime = response.headers.get("content-type", "").split(";")[0]
                        if mime not in {"text/html", "text/plain", "application/json"}:
                            raise SourceRejected("source_must_be_html_plaintext_or_json")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > BYTE_LIMIT:
                                raise SourceRejected("source_exceeds_read_limit")
                        content = bytes(body).decode(response.encoding or "utf-8")
                        break
                else:
                    raise SourceRejected("source_redirect_limit")
        except (httpx.HTTPError, UnicodeError, OSError) as error:
            raise SourceRejected(
                f"public_source_read_failed:{type(error).__name__}:{error}"
            ) from error
        page = PageText()
        if mime == "text/html":
            page.feed(content)
            content = "\n".join(page.parts)
        links = sorted({urljoin(url, link) for link in page.links})[:150]
        identifier = hashlib.sha256(bytes(body)).hexdigest()
        result = {
            "source_id": identifier,
            "requested_url": original,
            "url": url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "sha256": identifier,
            "content": content[:TEXT_LIMIT],
            "truncated": len(content) > TEXT_LIMIT,
            "links": links,
            "bytes": len(body),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        file = self.directory / f"{identifier}.json"
        if not file.exists():
            file.write_text(json.dumps(redact(result), indent=2), encoding="utf-8")
        return result
