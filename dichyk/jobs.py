"""Quản lý các lượt dịch (job): chạy nền, tiến độ, huỷ, tiếp tục, lưu lịch sử."""

from __future__ import annotations

import json
import queue
import re
import secrets
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import extract, paths, pipeline
from . import settings as settings_mod
from .cache import TranslationCache
from .engines import EngineError, make_engine
from .glossary import Glossary

ACTIVE = {"queued", "extracting", "translating"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "tai-lieu"


class Job:
    def __init__(self, **data):
        self.id: str = data.get("id") or datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        self.type: str = data.get("type", "pdf")
        self.status: str = data.get("status", "queued")
        self.phase: str = data.get("phase", "Đang chờ")
        self.created: str = data.get("created") or _now()
        self.finished: str = data.get("finished", "")
        self.file_name: str = data.get("file_name", "")
        self.file_path: str = data.get("file_path", "")
        self.title: str = data.get("title", "")
        self.pages_spec: str = data.get("pages_spec", "")
        self.scope: str = data.get("scope", "")
        self.options: dict = data.get("options", {})
        self.engine: str = data.get("engine", "")
        self.done: int = data.get("done", 0)
        self.total: int = data.get("total", 0)
        self.cached: int = data.get("cached", 0)
        self.flagged: int = data.get("flagged", 0)
        self.current_page: int = data.get("current_page", 0)
        self.last_text: str = data.get("last_text", "")
        self.eta: int = data.get("eta", 0)
        self.output_path: str = data.get("output_path", "")
        self.error: str = data.get("error", "")
        self.text_input: str = data.get("text_input", "")
        self.cancel_event = threading.Event()

    @property
    def dir(self) -> Path:
        return paths.sub("jobs") / self.id

    def to_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "cancel_event"}
        if self.type == "text":
            data["text_input"] = self.text_input[:20000]
        return data

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / "job.json")


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self.cache = TranslationCache(paths.data_dir() / "cache.db")
        self.glossary = Glossary()
        self._load_history()
        threading.Thread(target=self._worker, daemon=True, name="job-worker").start()

    # ------------------------------------------------------------------ #
    def _load_history(self) -> None:
        for file in sorted(paths.sub("jobs").glob("*/job.json")):
            try:
                job = Job(**json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if job.status in ACTIVE:
                job.status = "error"
                job.error = "App đã đóng khi đang dịch. Bấm “Tiếp tục” để dịch nốt (phần đã dịch được giữ lại)."
                job.save()
            self.jobs[job.id] = job

    def list(self) -> list[dict]:
        return [j.to_dict() for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    # ------------------------------------------------------------------ #
    def create_pdf_job(self, file_path: str, file_name: str, pages_spec: str, options: dict) -> Job:
        info = extract.pdf_info(file_path)
        pages = extract.parse_page_range(pages_spec, info.pages)  # báo lỗi khoảng trang ngay
        scope = "Toàn bộ tài liệu" if len(pages) == info.pages else f"Trang {_describe_pages(pages)}"
        job = Job(
            type="pdf", file_path=file_path, file_name=file_name, title=info.title or Path(file_name).stem,
            pages_spec=pages_spec.strip(), scope=scope, options=options, total=0,
        )
        self._register(job)
        self._queue.put(job.id)
        return job

    def create_text_job(self, text: str, options: dict) -> Job:
        if not text.strip():
            raise ValueError("Chưa có nội dung để dịch")
        if len(text) > 60000:
            raise ValueError("Đoạn văn quá dài (tối đa khoảng 60.000 ký tự). Hãy dùng chế độ dịch tài liệu.")
        job = Job(type="text", text_input=text, title="Dịch nhanh", scope=f"{len(text.split())} từ", options=options)
        self._register(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _register(self, job: Job) -> None:
        with self._lock:
            self.jobs[job.id] = job
        job.save()

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        job.cancel_event.set()
        if job.status == "queued":
            job.status, job.phase = "cancelled", "Đã huỷ"
            job.save()
        return job

    def resume(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status in ACTIVE:
            return job
        if job.type == "pdf" and not Path(job.file_path).is_file():
            raise ValueError("Không còn file PDF gốc để dịch tiếp. Hãy tải file lên lại.")
        job.cancel_event = threading.Event()
        job.status, job.phase, job.error = "queued", "Đang chờ", ""
        job.done = job.cached = job.flagged = 0
        job.save()
        if job.type == "text":
            threading.Thread(target=self._run, args=(job,), daemon=True).start()
        else:
            self._queue.put(job.id)
        return job

    # ------------------------------------------------------------------ #
    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.jobs.get(job_id)
            if job and job.status == "queued" and not job.cancel_event.is_set():
                self._run(job)

    def _run(self, job: Job) -> None:
        conf = settings_mod.load()
        job.options = {**job.options}
        bilingual = bool(job.options.get("bilingual_terms", conf["bilingual_terms"]))
        keep_source = bool(job.options.get("keep_source", conf["keep_source"]))
        detect_tables = bool(job.options.get("detect_tables", conf["detect_tables"]))
        agent_mode = conf["engine"] == "agent"
        job.engine = "Agent (dịch qua file)" if agent_mode else f"{conf['model']}"
        started = time.monotonic()
        try:
            # 1. Tách nội dung
            job.status, job.phase = "extracting", "Đang đọc tài liệu"
            job.save()
            if job.type == "pdf":
                info = extract.pdf_info(job.file_path)
                pages = extract.parse_page_range(job.pages_spec, info.pages)

                def on_page(done, total):
                    job.phase = f"Đang đọc trang {done}/{total}"
                    job.current_page = pages[done - 1]

                blocks = extract.extract_blocks(
                    job.file_path, pages, detect_tables=detect_tables,
                    progress=on_page, cancelled=job.cancel_event.is_set,
                )
            else:
                blocks = pipeline.text_to_blocks(job.text_input)
            if job.cancel_event.is_set():
                raise pipeline.Cancelled()

            self.glossary.reload()
            segments = pipeline.build_segments(blocks, self.glossary, bilingual)
            job.total = len(segments)
            pipeline.save_state(job.dir, blocks, segments)

            # 2a. Chế độ agent: xuất file rồi chờ
            if agent_mode:
                pipeline.export_agent_task(job.dir, segments, source=job.file_name or "văn bản dán vào", scope=job.scope)
                job.status = "waiting_agent"
                job.phase = "Chờ agent dịch"
                job.save()
                return

            # 2b. Dịch bằng model
            engine = make_engine(conf)
            job.status, job.phase = "translating", "Đang dịch"
            job.save()
            translated_fresh = 0
            fresh_started = time.monotonic()

            def on_segment(done, total, segment):
                nonlocal translated_fresh
                job.done, job.total = done, total
                job.current_page = segment.page
                job.last_text = (segment.dst or "")[:280]
                if segment.cached:
                    job.cached += 1
                else:
                    translated_fresh += 1
                if segment.flags:
                    job.flagged += 1
                if translated_fresh:
                    rate = (time.monotonic() - fresh_started) / translated_fresh
                    job.eta = int(rate * (total - done))
                job.phase = f"Đang dịch đoạn {done}/{total}"
                if done % 10 == 0 or done == total:
                    job.save()

            try:
                pipeline.translate_segments(
                    segments, engine, self.cache,
                    workers=int(conf["workers"]), progress=on_segment, cancelled=job.cancel_event.is_set,
                )
            finally:
                engine.close()
            self._finish(job, blocks, segments, keep_source)
        except pipeline.Cancelled:
            job.status, job.phase = "cancelled", "Đã huỷ — phần đã dịch được giữ trong bộ nhớ đệm"
        except (EngineError, ValueError) as exc:
            job.status, job.phase, job.error = "error", "Lỗi", str(exc)
        except Exception as exc:  # lỗi ngoài dự kiến: ghi chi tiết để báo cáo
            job.status, job.phase = "error", "Lỗi"
            job.error = f"{type(exc).__name__}: {exc}"
            (job.dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            if job.status not in ACTIVE and job.status != "waiting_agent":
                job.finished = _now()
            job.eta = 0
            job.phase = job.phase if job.status != "done" else f"Hoàn tất trong {_fmt_duration(time.monotonic() - started)}"
            job.save()

    def assemble_agent(self, job_id: str) -> Job:
        job = self.get(job_id)
        if not (job.dir / "state.json").is_file():
            raise ValueError("Job này không có dữ liệu để ghép")
        blocks, segments = pipeline.load_state(job.dir)
        missing = pipeline.load_agent_translations(job.dir, segments)
        job.done = len(segments) - missing
        job.total = len(segments)
        job.flagged = sum(1 for s in segments if s.flags)
        conf = settings_mod.load()
        self._finish(job, blocks, segments, bool(job.options.get("keep_source", conf["keep_source"])))
        if missing:
            job.phase = f"Đã ghép — còn {missing} đoạn agent chưa dịch (giữ nguyên tiếng Anh)"
        job.finished = _now()
        job.save()
        return job

    def _finish(self, job: Job, blocks, segments, keep_source: bool) -> None:
        markdown = pipeline.render_markdown(
            blocks, segments,
            title=job.title if job.type == "pdf" else "Bản dịch",
            source_name=job.file_name if job.type == "pdf" else "",
            scope=job.scope if job.type == "pdf" else "",
            engine_desc=job.engine,
            keep_source=keep_source,
            header=job.type == "pdf",
        )
        if job.type == "pdf":
            out_dir = Path(settings_mod.load()["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = _safe_name(Path(job.file_name).stem)
            suffix = "" if job.scope == "Toàn bộ tài liệu" else "_" + _safe_name(job.scope.replace("Trang ", "tr").replace(", ", "_").replace("–", "-"))[:60]
            target = Path(job.output_path) if job.output_path else out_dir / f"{stem}{suffix}.vi.md"
            if not job.output_path:
                n = 2
                while target.exists():
                    target = out_dir / f"{stem}{suffix} ({n}).vi.md"
                    n += 1
        else:
            target = job.dir / "result.md"
        target.write_text(markdown, encoding="utf-8")
        (job.dir / "review.json").write_text(
            json.dumps(pipeline.review_items(segments), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        job.output_path = str(target)
        job.flagged = sum(1 for s in segments if s.flags)
        job.status = "done"


def _describe_pages(pages: list[int]) -> str:
    ranges, start, prev = [], pages[0], pages[0]
    for p in pages[1:] + [None]:
        if p is not None and p == prev + 1:
            prev = p
            continue
        ranges.append(f"{start}–{prev}" if start != prev else str(start))
        if p is not None:
            start = prev = p
    return ", ".join(ranges)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} giây"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} phút {seconds} giây"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} giờ {minutes} phút"
