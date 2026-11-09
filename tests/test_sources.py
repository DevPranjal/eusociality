import json

import httpx
import pytest

from institution_lab.config import ResearchConfig
from institution_lab.sources import SourceReader, SourceRejected, approved_url, public_address


def test_source_urls_allow_public_hosts_and_queries_but_not_credentials(monkeypatch):
    config = ResearchConfig(enabled=True, allowed_hosts=["docs.example.org"])
    assert approved_url("https://docs.example.org/path", config)
    assert approved_url("https://other.example.org/path?q=research", config)
    assert approved_url("http://other.example.org/path?q=research", config)
    assert approved_url("https://other.example.org/path", ResearchConfig(enabled=False))
    for url in (
        "https://localhost/path",
        "https://docs.example.org:999/path",
        "https://user:password@docs.example.org/path",
        "https://docs.example.org/path?token=x",
        "https://127.0.0.1/",
    ):
        with pytest.raises(SourceRejected):
            approved_url(url, config)
    monkeypatch.setattr(
        "institution_lab.sources.socket.getaddrinfo",
        lambda *_a, **_k: [
            (0, 0, 0, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(SourceRejected, match="nonpublic"):
        public_address("docs.example.org")


@pytest.mark.asyncio
async def test_public_reads_retain_provenance_and_allow_public_redirect(tmp_path, monkeypatch):
    """Controlled HTTP fixture, not a live web observation."""
    import institution_lab.sources as sources

    original = httpx.AsyncClient
    redirect = False

    def endpoint(request):
        if redirect:
            if request.url.host == "docs.example.org":
                return httpx.Response(302, headers={"location": "https://other.example.org/path"})
        return httpx.Response(
            200,
            text=(
                "<html><h1>Controlled source</h1><p>release information</p>"
                "<script>untrusted script</script><a href='/next'>Next</a></html>"
            ),
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(sources, "public_address", lambda _host: None)
    monkeypatch.setattr(
        sources.httpx,
        "AsyncClient",
        lambda **options: original(
            **options,
            transport=httpx.MockTransport(endpoint),
        ),
    )
    reader = SourceReader(
        ResearchConfig(enabled=True, allowed_hosts=["docs.example.org"]), tmp_path
    )
    result = await reader.fetch("https://docs.example.org/path")
    assert result["url"] == "https://docs.example.org/path"
    assert result["retrieved_at"] and len(result["sha256"]) == 64
    assert (
        "release information" in result["content"] and "untrusted script" not in result["content"]
    )
    assert result["links"] == ["https://docs.example.org/next"]
    saved = json.loads((tmp_path / f"{result['source_id']}.json").read_text())
    assert saved == result
    redirect = True
    assert (await reader.fetch("https://docs.example.org/path"))["url"] == (
        "https://other.example.org/path"
    )
