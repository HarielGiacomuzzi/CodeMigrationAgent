from pathlib import Path

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_index_has_every_workflow_region():
    html = client.get("/").text
    for marker in [
        'name="viewport"', 'id="migrate-form"', 'id="source-framework"', 'id="target-framework"',
        'data-phase="analysis"', 'data-phase="planning"', 'data-phase="execution"', 'data-phase="verification"',
        'id="plan"', 'id="verification"', 'id="file-tabs"', 'id="output"', 'value="diff"',
    ]:
        assert marker in html, marker


def test_assets_are_served():
    js = client.get("/app.js")
    assert js.status_code == 200 and "/migrate/stream" in js.text
    assert client.get("/style.css").status_code == 200


def test_api_routes_still_take_precedence():
    assert client.get("/health").json() == {"status": "ok"}


def test_frontend_never_injects_html():
    js = Path("static/app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in js, sink
