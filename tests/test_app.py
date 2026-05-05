from fastapi.testclient import TestClient

from app.main import app, build_form_page, build_reader_html, sanitize_fragment


def test_sanitize_fragment_removes_junk_and_keeps_absolute_images():
    fragment = """
    <div>
      <script>alert('x')</script>
      <aside class='sidebar'>ignore me</aside>
      <article>
        <h2 class='headline'>Hello</h2>
        <p onclick='track()'>World</p>
        <img src='/hero.jpg' loading='lazy' />
      </article>
    </div>
    """

    cleaned = sanitize_fragment(fragment, "https://example.com/story")

    assert "script" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()
    assert "sidebar" not in cleaned.lower()
    assert "https://example.com/hero.jpg" in cleaned
    assert "Hello" in cleaned
    assert "World" in cleaned


def test_form_page_is_available_for_browser_use():
    client = TestClient(app)

    response = client.get("/reader")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Reader Service" in response.text
    assert "Open in reader mode" in response.text
    assert build_form_page()[:80] in response.text


def test_reader_endpoint_returns_html(monkeypatch):
    client = TestClient(app)

    async def fake_extract_reader_html(url: str) -> str:
        assert url == "https://example.com/post"
        return build_reader_html(
            title="Example Post",
            article_html="<p>Readable body</p>",
            source_url=url,
        )

    monkeypatch.setattr("app.main.extract_reader_html", fake_extract_reader_html)

    response = client.post("/reader", json={"url": "https://example.com/post"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Readable body" in response.text
    assert "Example Post" in response.text
