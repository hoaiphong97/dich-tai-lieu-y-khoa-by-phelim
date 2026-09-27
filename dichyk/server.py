"""API cho giao diện (FastAPI). Chỉ lắng nghe trên 127.0.0.1."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import markdown as md_lib
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import extract, paths
from . import settings as settings_mod
from .engines import EngineError, make_engine
from .jobs import JobManager
from .prompts import SYSTEM, build_user

app = FastAPI(title=paths.APP_TITLE, docs_url=None, redoc_url=None)
manager: JobManager | None = None


def jobs() -> JobManager:
    global manager
    if manager is None:
        manager = JobManager()
    return manager


def _bad(message: str, code: int = 400):
    raise HTTPException(status_code=code, detail=message)


# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    return (paths.web_dir() / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(paths.web_dir())), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "version": paths.APP_VERSION, "data_dir": str(paths.data_dir())}


# ----------------------------- Cài đặt ------------------------------------- #
@app.get("/api/settings")
def get_settings():
    return {"settings": settings_mod.public(settings_mod.load()), "presets": settings_mod.PRESETS}


class SettingsIn(BaseModel):
    values: dict
    clear_api_key: bool = False


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    values = dict(body.values)
    if not values.get("api_key") and not body.clear_api_key:
        values.pop("api_key", None)  # để trống = giữ key cũ
    elif body.clear_api_key:
        values["api_key"] = ""
    return {"settings": settings_mod.public(settings_mod.save(values))}


class EngineIn(BaseModel):
    values: dict = {}


def _merged(values: dict) -> dict:
    conf = settings_mod.load()
    conf.update({k: v for k, v in values.items() if k in settings_mod.DEFAULTS and (k != "api_key" or v)})
    return conf


@app.post("/api/engine/models")
def engine_models(body: EngineIn):
    conf = _merged(body.values)
    if conf["engine"] == "agent":
        return {"models": []}
    conf["model"] = conf["model"] or "_"
    try:
        engine = make_engine(conf)
        try:
            return {"models": engine.list_models()}
        finally:
            engine.close()
    except Exception as exc:
        _bad(f"Không lấy được danh sách model: {exc}")


@app.post("/api/engine/test")
def engine_test(body: EngineIn):
    conf = _merged(body.values)
    if conf["engine"] == "agent":
        return {"ok": True, "message": "Chế độ agent không cần kết nối model."}
    sample = "The mandible articulates with the temporal bone at the temporomandibular joint."
    try:
        engine = make_engine(conf)
        try:
            result = engine.translate(SYSTEM, build_user(sample, "para", "", "", []))
        finally:
            engine.close()
    except EngineError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "message": "Kết nối thành công", "source": sample, "result": result}


# ----------------------------- Tài liệu ------------------------------------ #
@app.post("/api/files")
async def upload(file: UploadFile = File(...)):
    name = Path(file.filename or "tai-lieu.pdf").name
    if not name.lower().endswith(".pdf"):
        _bad("Chỉ hỗ trợ file PDF")
    folder = paths.sub("uploads") / secrets.token_hex(6)
    folder.mkdir(parents=True)
    target = folder / name
    with open(target, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    return _file_info(target, name)


class PathIn(BaseModel):
    path: str


@app.post("/api/files/local")
def open_local(body: PathIn):
    """Mở PDF theo đường dẫn trên máy (dùng hộp thoại chọn file của app, không cần chép file)."""
    target = Path(body.path)
    if not target.is_file() or target.suffix.lower() != ".pdf":
        _bad("Không tìm thấy file PDF")
    return _file_info(target, target.name)


def _file_info(target: Path, name: str) -> dict:
    try:
        info = extract.pdf_info(target)
    except Exception as exc:
        _bad(f"Không mở được PDF: {exc}")
    return {
        "path": str(target), "name": name, "pages": info.pages, "title": info.title,
        "toc": info.toc, "scanned_pages": info.scanned_pages, "size": target.stat().st_size,
    }


# ----------------------------- Job ----------------------------------------- #
class PdfJobIn(BaseModel):
    path: str
    name: str
    pages: str = ""
    options: dict = {}


@app.post("/api/jobs")
def create_job(body: PdfJobIn):
    if not Path(body.path).is_file():
        _bad("File không còn tồn tại, hãy chọn lại")
    try:
        return jobs().create_pdf_job(body.path, body.name, body.pages, body.options).to_dict()
    except ValueError as exc:
        _bad(str(exc))


class TextJobIn(BaseModel):
    text: str
    options: dict = {}


@app.post("/api/jobs/text")
def create_text_job(body: TextJobIn):
    try:
        return jobs().create_text_job(body.text, body.options).to_dict()
    except ValueError as exc:
        _bad(str(exc))


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": [j for j in jobs().list() if j["type"] == "pdf"]}


def _job(job_id: str):
    try:
        return jobs().get(job_id)
    except KeyError:
        _bad("Không tìm thấy job", 404)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _job(job_id)
    data = job.to_dict()
    data["job_dir"] = str(job.dir)
    if job.status == "done" and job.type == "text" and Path(job.output_path).is_file():
        data["result"] = Path(job.output_path).read_text(encoding="utf-8")
    review = job.dir / "review.json"
    if job.status == "done" and review.is_file():
        data["review"] = json.loads(review.read_text(encoding="utf-8"))[:300]
    return data


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    _job(job_id)
    return jobs().cancel(job_id).to_dict()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    _job(job_id)
    try:
        return jobs().resume(job_id).to_dict()
    except ValueError as exc:
        _bad(str(exc))


@app.post("/api/jobs/{job_id}/assemble")
def assemble_job(job_id: str):
    _job(job_id)
    try:
        return jobs().assemble_agent(job_id).to_dict()
    except (ValueError, OSError) as exc:
        _bad(str(exc))


def _output(job_id: str) -> Path:
    job = _job(job_id)
    path = Path(job.output_path) if job.output_path else None
    if not path or not path.is_file():
        _bad("Chưa có file kết quả", 404)
    return path


@app.get("/api/jobs/{job_id}/markdown", response_class=PlainTextResponse)
def job_markdown(job_id: str):
    return _output(job_id).read_text(encoding="utf-8")


@app.get("/api/jobs/{job_id}/preview", response_class=HTMLResponse)
def job_preview(job_id: str):
    text = _output(job_id).read_text(encoding="utf-8")
    # Chỉ giữ lại chú thích của app (dấu trang, cảnh báo); mọi thẻ HTML khác từ bản dịch bị vô hiệu.
    keep: list[str] = []

    def stash(match):
        keep.append(match.group(0))
        return f"DYKKEEP{len(keep) - 1}DYKKEEP"

    text = re.sub(r"<!-- (?:Trang \d+|⚠ Cần kiểm tra: .*?) -->", stash, text)
    text = text.replace("&", "&amp;").replace("<", "&lt;")
    html = md_lib.markdown(text, extensions=["tables", "sane_lists"])
    return re.sub(r"(?:<p>)?DYKKEEP(\d+)DYKKEEP(?:</p>)?", lambda m: keep[int(m.group(1))], html)


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str):
    path = _output(job_id)
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)


@app.post("/api/jobs/{job_id}/open-folder")
def open_folder(job_id: str):
    job = _job(job_id)
    target = Path(job.output_path).parent if job.output_path else job.dir
    if job.status == "waiting_agent":
        target = job.dir
    _reveal(target)
    return {"ok": True, "path": str(target)}


@app.post("/api/open-output-folder")
def open_output_folder():
    target = Path(settings_mod.load()["output_dir"])
    target.mkdir(parents=True, exist_ok=True)
    _reveal(target)
    return {"ok": True, "path": str(target)}


def _reveal(target: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        _bad(f"Không mở được thư mục: {exc}")


# ----------------------------- Thuật ngữ ----------------------------------- #
@app.get("/api/terms")
def list_terms(q: str = ""):
    g = jobs().glossary
    return {"terms": g.search(q), "total": len(g.terms), "user_file": str(g.user_file)}


@app.post("/api/terms")
def save_term(body: dict):
    try:
        return jobs().glossary.upsert(body).to_dict()
    except ValueError as exc:
        _bad(str(exc))


# ----------------------------- Bộ nhớ đệm ---------------------------------- #
@app.post("/api/cache/clear")
def clear_cache():
    return {"removed": jobs().cache.clear()}
