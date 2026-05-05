# reader-service

Tiny FastAPI service that turns any public article URL into a clean reading-mode HTML document.

## API

### `POST /reader`

Request:

```json
{
  "url": "https://example.com/article"
}
```

Response:

- `200 text/html` with a simplified reading-mode page
- keeps the main article body and images
- strips scripts, forms, and noisy markup
- rewrites image URLs to absolute URLs
- rejects private / localhost targets to avoid SSRF surprises

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then hit it with:

```bash
curl -X POST http://127.0.0.1:8000/reader \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com"}'
```

## Test

```bash
pytest -q
```
