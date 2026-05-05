from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, HttpUrl
from readability import Document

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 ReaderService/1.0"
)
BLOCKED_IMAGE_SCHEMES = {"data", "javascript", "file"}

app = FastAPI(title="reader-service", version="0.1.0")


class ReaderRequest(BaseModel):
    url: HttpUrl


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not any(
        [
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ]
    )


def assert_public_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL must include a hostname.")

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Could not resolve hostname.") from exc

    addresses = {item[4][0] for item in addrinfo}
    if not addresses:
        raise HTTPException(status_code=400, detail="Could not resolve hostname.")

    for address in addresses:
        if not _is_public_ip(address):
            raise HTTPException(status_code=400, detail="Target host is not publicly routable.")


async def fetch_source_html(url: str) -> tuple[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise HTTPException(status_code=400, detail="URL did not return an HTML document.")
        return str(response.url), response.text


def make_images_absolute(fragment: BeautifulSoup, base_url: str) -> None:
    for tag_name, attr_name in (("img", "src"), ("source", "srcset")):
        for node in fragment.find_all(tag_name):
            if attr_name == "srcset":
                srcset = node.get("srcset")
                if not srcset:
                    continue
                parts = []
                for candidate in srcset.split(","):
                    candidate = candidate.strip()
                    if not candidate:
                        continue
                    bits = candidate.split()
                    raw_url = bits[0]
                    parsed = urlparse(raw_url)
                    if parsed.scheme in BLOCKED_IMAGE_SCHEMES:
                        continue
                    bits[0] = urljoin(base_url, raw_url)
                    parts.append(" ".join(bits))
                if parts:
                    node["srcset"] = ", ".join(parts)
                else:
                    node.attrs.pop("srcset", None)
                continue

            raw_url = node.get(attr_name)
            if not raw_url:
                continue
            parsed = urlparse(raw_url)
            if parsed.scheme in BLOCKED_IMAGE_SCHEMES:
                node.decompose()
                continue
            node[attr_name] = urljoin(base_url, raw_url)
            node.attrs.pop("loading", None)
            node.attrs.pop("decoding", None)


def sanitize_fragment(html_fragment: str, base_url: str) -> str:
    fragment = BeautifulSoup(html_fragment, "lxml")

    for tag in fragment.find_all(["script", "style", "noscript", "iframe", "form", "button", "input", "aside", "nav", "footer"]):
        tag.decompose()

    for node in fragment.find_all(True):
        if node.get("role") in {"navigation", "complementary", "contentinfo", "banner"}:
            node.decompose()
            continue
        attrs_to_remove = []
        for attr in list(node.attrs):
            if attr.startswith("on"):
                attrs_to_remove.append(attr)
                continue
            if attr in {"style", "class", "id", "data-testid", "aria-hidden"}:
                attrs_to_remove.append(attr)
        for attr in attrs_to_remove:
            node.attrs.pop(attr, None)

    make_images_absolute(fragment, base_url)

    body = fragment.body or fragment
    return "".join(str(child) for child in body.contents).strip()


def build_reader_html(title: str, article_html: str, source_url: str) -> str:
    safe_title = BeautifulSoup(title or "Reading Mode", "html.parser").get_text(" ", strip=True)
    return f"""<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>{safe_title}</title>
    <style>
      body {{
        margin: 0;
        background: #faf9f7;
        color: #181818;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        max-width: 760px;
        margin: 0 auto;
        padding: 40px 20px 72px;
        line-height: 1.7;
        font-size: 18px;
      }}
      header {{ margin-bottom: 32px; }}
      h1, h2, h3, h4 {{ line-height: 1.2; }}
      img {{ max-width: 100%; height: auto; display: block; margin: 24px auto; }}
      figure {{ margin: 24px 0; }}
      figcaption {{ color: #666; font-size: 14px; }}
      pre {{ overflow-x: auto; background: #f1efe9; padding: 16px; border-radius: 12px; }}
      blockquote {{ border-left: 4px solid #d8cfc4; margin: 24px 0; padding-left: 16px; color: #4a4a4a; }}
      a {{ color: #0f62fe; }}
      .source {{ color: #666; font-size: 14px; }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div class=\"source\">Source: <a href=\"{source_url}\">{source_url}</a></div>
        <h1>{safe_title}</h1>
      </header>
      <article>
        {article_html}
      </article>
    </main>
  </body>
</html>
"""


async def extract_reader_html(url: str) -> str:
    assert_public_target(url)
    final_url, source_html = await fetch_source_html(url)
    document = Document(source_html)
    title = document.short_title() or document.title() or final_url
    article_fragment = document.summary(html_partial=True)
    cleaned = sanitize_fragment(article_fragment, final_url)
    if not cleaned:
        raise HTTPException(status_code=422, detail="Could not extract readable content from the page.")
    return build_reader_html(title=title, article_html=cleaned, source_url=final_url)


def build_form_page() -> str:
    return """<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Reader Service</title>
    <style>
      body {
        margin: 0;
        background: #faf9f7;
        color: #181818;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      main {
        max-width: 760px;
        margin: 0 auto;
        padding: 48px 20px 72px;
      }
      h1 { line-height: 1.1; margin-bottom: 12px; }
      p { color: #555; }
      form {
        margin-top: 24px;
        display: grid;
        gap: 12px;
      }
      input, button {
        font: inherit;
        padding: 14px 16px;
        border-radius: 12px;
        border: 1px solid #d4d0c7;
      }
      button {
        cursor: pointer;
        background: #111;
        color: white;
        border: none;
      }
      button:hover { background: #222; }
      .hint, code { color: #666; }
      iframe {
        margin-top: 24px;
        width: 100%;
        min-height: 70vh;
        border: 1px solid #e4dfd5;
        border-radius: 16px;
        background: white;
      }
      pre {
        background: #f1efe9;
        padding: 16px;
        border-radius: 12px;
        overflow-x: auto;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Reader Service</h1>
      <p>Paste a public article URL and this service returns a clean reading-mode HTML version.</p>
      <form id=\"reader-form\">
        <input id=\"url\" name=\"url\" type=\"url\" placeholder=\"https://example.com/article\" required />
        <button type=\"submit\">Open in reader mode</button>
      </form>
      <p class=\"hint\">API endpoint: <code>POST /reader</code> with JSON like <code>{\"url\":\"https://example.com\"}</code></p>
      <pre id=\"error\" hidden></pre>
      <iframe id=\"result\" title=\"Reader output\"></iframe>
    </main>
    <script>
      const form = document.getElementById('reader-form');
      const input = document.getElementById('url');
      const frame = document.getElementById('result');
      const error = document.getElementById('error');

      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        error.hidden = true;
        frame.srcdoc = '<p style="font-family: sans-serif; padding: 24px;">Loading…</p>';
        try {
          const response = await fetch('/reader', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ url: input.value })
          });
          const text = await response.text();
          if (!response.ok) {
            error.textContent = text;
            error.hidden = false;
            frame.srcdoc = '';
            return;
          }
          frame.srcdoc = text;
        } catch (err) {
          error.textContent = err.message || String(err);
          error.hidden = false;
          frame.srcdoc = '';
        }
      });
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
@app.get("/reader", response_class=HTMLResponse)
async def reader_form() -> HTMLResponse:
    return HTMLResponse(content=build_form_page())


@app.post("/reader", response_class=HTMLResponse)
async def reader(request: ReaderRequest) -> HTMLResponse:
    try:
        html = await extract_reader_html(str(request.url))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch the requested URL.") from exc

    return HTMLResponse(content=html)
