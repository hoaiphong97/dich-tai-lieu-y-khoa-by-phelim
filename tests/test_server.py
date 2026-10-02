from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.sample_pdf import build


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DICHYK_DATA", str(tmp_path / "data"))
    from dichyk import server

    server.manager = None  # JobManager mới cho thư mục dữ liệu tạm
    with TestClient(server.app) as c:
        c.put("/api/settings", json={"values": {"engine": "agent", "preset": "agent", "output_dir": str(tmp_path / "out")}})
        yield c


def _wait(client, job_id, until=("done", "error", "waiting_agent", "cancelled")):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.05)
    raise AssertionError(job)


def test_index_and_settings(client):
    assert "Dịch Y Khoa" in client.get("/").text
    data = client.get("/api/settings").json()
    assert data["settings"]["engine"] == "agent"
    assert "ollama" in data["presets"]
    # API key không bao giờ trả về giao diện
    client.put("/api/settings", json={"values": {"api_key": "secret"}})
    s = client.get("/api/settings").json()["settings"]
    assert s["api_key"] == "" and s["api_key_set"] is True
    client.put("/api/settings", json={"values": {"api_key": ""}})  # để trống = giữ nguyên
    assert client.get("/api/settings").json()["settings"]["api_key_set"] is True


def test_upload_range_and_agent_flow(client, tmp_path):
    pdf = build(str(tmp_path / "book.pdf"))
    with open(pdf, "rb") as f:
        info = client.post("/api/files", files={"file": ("book.pdf", f, "application/pdf")}).json()
    assert info["pages"] == 3 and info["toc"]

    bad = client.post("/api/jobs", json={"path": info["path"], "name": "book.pdf", "pages": "9-12"})
    assert bad.status_code == 400

    job = client.post("/api/jobs", json={"path": info["path"], "name": "book.pdf", "pages": "3"}).json()
    job = _wait(client, job["id"])
    assert job["status"] == "waiting_agent" and job["scope"] == "Trang 3"

    done = client.post(f"/api/jobs/{job['id']}/assemble").json()
    assert done["status"] == "done"
    assert done["outputs"]["md"].endswith("book_tr3.vi.md") and done["outputs"]["pdf"].endswith("book_tr3.vi.pdf")
    assert client.get(f"/api/jobs/{job['id']}/download?fmt=pdf").content[:4] == b"%PDF"
    for side in ("src", "dst"):
        page = client.get(f"/api/jobs/{job['id']}/page?side={side}&i=0")
        assert page.status_code == 200 and page.content[:4] == b"\x89PNG"
    assert client.get(f"/api/jobs/{job['id']}/asset?path=../../etc/passwd").status_code == 404
    md = client.get(f"/api/jobs/{job['id']}/markdown").text
    assert "Occlusal Forces" in md  # agent chưa dịch → giữ nguyên bản gốc
    preview = client.get(f"/api/jobs/{job['id']}/preview").text
    assert "<table>" in preview and "<!-- Trang 3 -->" in preview
    assert client.get("/api/jobs").json()["jobs"][0]["id"] == job["id"]


def test_preview_neutralises_html(client, tmp_path):
    from dichyk import server

    job = client.post("/api/jobs/text", json={"text": "Hello world, this is a test."}).json()
    job = _wait(client, job["id"])
    j = server.jobs().get(job["id"])
    out = tmp_path / "x.md"
    out.write_text("<script>alert(1)</script>\n\n<!-- Trang 2 -->\n\nXin chào", encoding="utf-8")
    j.output_path, j.status = str(out), "done"
    html = client.get(f"/api/jobs/{job['id']}/preview").text
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<!-- Trang 2 -->" in html


def test_terms_api(client):
    r = client.post("/api/terms", json={"en": "condyle", "vi": "lồi cầu xương", "status": "approved"})
    assert r.status_code == 200
    found = client.get("/api/terms?q=condyle").json()["terms"]
    assert found[0]["vi"] == "lồi cầu xương" and found[0]["source"] == "user"
    assert client.post("/api/terms", json={"en": "x"}).status_code == 400
