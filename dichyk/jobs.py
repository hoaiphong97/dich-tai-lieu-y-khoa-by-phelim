"""Quản lý các lượt dịch (job): chạy nền, tiến độ, huỷ, tiếp tục, lưu lịch sử."""

from __future__ import annotations

import json
import queue
import re
import secrets
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import extract, figtext, paths, pdfout, pipeline
from . import settings as settings_mod
from .cache import TranslationCache
from .engines import EngineError, make_engine
from .glossary import Glossary

ACTIVE = {"queued", "extracting", "translating", "assembling"}


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
        self.outputs: dict = data.get("outputs") or ({"md": self.output_path} if self.output_path else {})
        self.page_list: list = data.get("page_list", [])
        self.pdf_parts: list = data.get("pdf_parts", [])  # [[file lô, [trang…]]] đã đặt chữ xong
        self.pdf_pages_ready: int = data.get("pdf_pages_ready", 0)
        self.agent_parts: int = data.get("agent_parts", 1)
        # Tiến độ của bước đang chạy sau khi dịch (đặt chữ vào PDF, cắt hình); 0/0 = chưa đo được
        self.step_done: int = data.get("step_done", 0)
        self.step_total: int = data.get("step_total", 0)
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
            if job.status == "assembling":  # bản dịch của agent vẫn còn nguyên: chỉ cần ghép lại
                job.status, job.phase = "waiting_agent", "Chờ ghép lại"
                job.error = "App đã đóng khi đang ghép bản dịch. Bấm “Ghép bản dịch” để ghép lại."
                job.pdf_parts, job.pdf_pages_ready = [], 0
                job.save()
            elif job.status in ACTIVE:
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
                job.page_list = pages

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
                figures = []
                if job.type == "pdf" and _figure_text(job, conf):
                    def on_figure(done, total):
                        job.phase = f"Đang xuất ảnh hình cho agent đọc chữ {done}/{total}"

                    figures = figtext.export_images(
                        job.file_path, blocks, job.dir, progress=on_figure, cancelled=job.cancel_event.is_set,
                    )
                    if job.cancel_event.is_set():
                        raise pipeline.Cancelled()
                tasks = pipeline.export_agent_task(
                    job.dir, segments, source=job.file_name or "văn bản dán vào", scope=job.scope,
                    parts=int(conf.get("agent_parts", 1)), figures=figures,
                )
                job.agent_parts = len(tasks)
                job.status = "waiting_agent"
                job.phase = "Chờ agent dịch"
                job.save()
                return

            # 2b. Dịch bằng model; lô trang nào đủ bản dịch thì đặt chữ vào PDF ngay (chạy chồng lên nhau)
            engine = make_engine(conf)
            job.status, job.phase = "translating", "Đang dịch"
            renderer = None
            if job.type == "pdf" and bool(job.options.get("output_pdf", conf["output_pdf"])):
                renderer = PartRenderer(
                    job, blocks, segments, job.page_list,
                    interleave=bool(job.options.get("pdf_interleave", conf["pdf_interleave"])),
                    figure_notes=_figure_text(job, conf),
                )
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
                # Tốc độ thực của cả job (gồm cả thời gian đặt chữ vào PDF xen giữa)
                job.eta = int((time.monotonic() - fresh_started) / max(done, 1) * (total - done))
                job.phase = f"Đang dịch đoạn {done}/{total}"
                if renderer and renderer.mark(segment):
                    job.save()  # vừa xong một lô PDF: lưu để giao diện cho xem ngay
                elif done % 10 == 0 or done == total:
                    job.save()

            try:
                pipeline.translate_segments(
                    segments, engine, self.cache,
                    workers=int(conf["workers"]), progress=on_segment, cancelled=job.cancel_event.is_set,
                )
            finally:
                engine.close()
            if renderer:
                renderer.flush(force=True)
            self._finish(job, blocks, segments, keep_source, parts=renderer.parts if renderer else None)
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
        """Bắt đầu ghép bản dịch của agent (chạy nền). Giao diện theo dõi tiến độ qua job.phase/step_*."""
        job = self.get(job_id)
        if job.status == "assembling":
            return job
        if job.status in ACTIVE:
            raise ValueError("Job đang chạy, chưa ghép được")
        if not (job.dir / "state.json").is_file():
            raise ValueError("Job này không có dữ liệu để ghép")
        job.cancel_event = threading.Event()
        job.status, job.phase, job.error = "assembling", "Đang nạp bản dịch của agent", ""
        job.step_done = job.step_total = job.eta = 0
        job.save()
        threading.Thread(target=self._assemble, args=(job,), daemon=True, name=f"assemble-{job.id}").start()
        return job

    def _assemble(self, job: Job) -> None:
        started = time.monotonic()
        conf = settings_mod.load()
        missing = 0
        try:
            blocks, segments = pipeline.load_state(job.dir)
            missing = pipeline.load_agent_translations(job.dir, segments)
            job.done, job.total = len(segments) - missing, len(segments)
            job.flagged = sum(1 for s in segments if s.flags)
            texts, figures_unread = None, 0
            if _figure_text(job, conf):
                agent_items, read = figtext.load_agent(job.dir)
                texts = figtext.collect(blocks, segments, agent_items)
                if (job.dir / figtext.IMAGE_DIR).is_dir():  # job có xuất ảnh hình cho agent đọc
                    figures_unread = sum(1 for i, _ in figtext.figures(blocks) if i not in read)
            parts = None
            if job.type == "pdf" and bool(job.options.get("output_pdf", conf["output_pdf"])):
                job.page_list = job.page_list or sorted({b.page for b in blocks})
                job.step_done, job.step_total = 0, len(job.page_list)
                render_started = time.monotonic()

                def on_page(page_no: int) -> None:
                    job.step_done += 1
                    job.current_page = page_no
                    job.phase = f"Đang đặt chữ vào PDF — trang {job.step_done}/{job.step_total}"
                    rate = (time.monotonic() - render_started) / job.step_done
                    job.eta = int(rate * (job.step_total - job.step_done))

                renderer = PartRenderer(
                    job, blocks, segments, job.page_list,
                    interleave=bool(job.options.get("pdf_interleave", conf["pdf_interleave"])),
                    on_page=on_page, figure_texts=texts,
                )
                job.save()
                while renderer.render_next():
                    job.save()  # một lô xong: giao diện xem được các trang này ngay
                    if job.cancel_event.is_set():
                        raise pipeline.Cancelled()
                parts = renderer.parts
            self._finish(
                job, blocks, segments, bool(job.options.get("keep_source", conf["keep_source"])),
                parts=parts, figure_texts=texts,
            )
            took = _fmt_duration(time.monotonic() - started)
            left = [
                f"{missing} đoạn agent chưa dịch (giữ nguyên tiếng Anh)" if missing else "",
                f"{figures_unread} hình agent chưa đọc chữ" if figures_unread else "",
            ]
            left = [x for x in left if x]
            job.phase = f"Đã ghép trong {took} — còn {' và '.join(left)}" if left else f"Đã ghép xong trong {took}"
            job.finished = _now()
        except pipeline.Cancelled:
            job.status, job.phase = "waiting_agent", "Đã huỷ ghép"
            self._drop_parts(job)
        except Exception as exc:  # bản dịch của agent vẫn còn: quay lại chờ ghép, báo lỗi rõ ràng
            job.status, job.phase = "waiting_agent", "Ghép bị lỗi"
            job.error = f"Ghép bản dịch bị lỗi — {type(exc).__name__}: {exc}"
            (job.dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            self._drop_parts(job)
        finally:
            job.eta = 0
            job.save()

    @staticmethod
    def _drop_parts(job: Job) -> None:
        folder = job.dir / "pdf_parts"
        job.pdf_parts, job.pdf_pages_ready = [], 0
        shutil.rmtree(folder, ignore_errors=True)

    def _finish(self, job: Job, blocks, segments, keep_source: bool, parts: list | None = None,
                figure_texts: dict | None = None) -> None:
        conf = settings_mod.load()
        if figure_texts is None and job.type == "pdf" and _figure_text(job, conf):
            figure_texts = figtext.collect(blocks, segments)  # nhãn chữ dạng ký tự đã dịch
        render = dict(
            title=job.title if job.type == "pdf" else "Bản dịch",
            source_name=job.file_name if job.type == "pdf" else "",
            scope=job.scope if job.type == "pdf" else "",
            engine_desc=job.engine,
            keep_source=keep_source,
            header=job.type == "pdf",
        )
        (job.dir / "review.json").write_text(
            json.dumps(pipeline.review_items(segments), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        job.flagged = sum(1 for s in segments if s.flags)

        if job.type != "pdf":
            target = job.dir / "result.md"
            target.write_text(pipeline.render_markdown(blocks, segments, **render), encoding="utf-8")
            job.outputs = {"md": str(target)}
            job.output_path = str(target)
            job.status = "done"
            return

        want_pdf = bool(job.options.get("output_pdf", conf["output_pdf"]))
        want_md = bool(job.options.get("output_md", conf["output_md"])) or not want_pdf
        base = self._output_base(job, Path(conf["output_dir"]))
        outputs: dict[str, str] = {}
        pages = job.page_list or sorted({b.page for b in blocks})
        if want_pdf:
            pdf_path = base.with_name(base.name + ".vi.pdf")
            interleave = bool(job.options.get("pdf_interleave", conf["pdf_interleave"]))

            def on_page(done, total):
                job.phase = f"Đang tạo PDF trang {done}/{total}"
                job.step_done, job.step_total = done, total

            if parts:  # các lô đã được đặt chữ trong lúc dịch: chỉ cần ghép
                job.phase = f"Đang ghép {len(pages)} trang thành file PDF"
                job.step_done = job.step_total = job.eta = 0
                job.save()
                pdfout.merge_parts(parts, pdf_path, source=job.file_path, pages=pages, interleave=interleave)
                job.pdf_parts = []  # từ giờ xem trang từ file PDF hoàn chỉnh
                shutil.rmtree(Path(parts[0]).parent, ignore_errors=True)
            else:
                pdfout.render_pdf(
                    job.file_path, pages, blocks, segments, pdf_path, interleave=interleave, progress=on_page,
                    notes=figtext.pdf_notes(blocks, figure_texts or {}, pages),
                )
            job.pdf_pages_ready = len(pages)
            outputs["pdf"] = str(pdf_path)
            job.outputs = {**job.outputs, "pdf": str(pdf_path)}  # xem trang từ file này trong lúc cắt hình
        if want_md:
            md_path = base.with_name(base.name + ".vi.md")
            figure_dir = base.with_name("hinh-" + pdfout.slug(base.name))
            job.phase = "Đang cắt hình cho Markdown"
            job.step_done = job.step_total = job.eta = 0
            job.save()

            def on_figure(done, total):
                job.phase = f"Đang cắt hình cho Markdown — hình {done}/{total}"
                job.step_done, job.step_total = done, total

            if want_pdf:  # cắt hình từ PDF đã dịch: nhãn chữ trong hình là tiếng Việt
                index = {p: (2 * i + 1 if interleave else i) for i, p in enumerate(pages)}
                names = pdfout.export_figures(pdf_path, blocks, figure_dir, page_index=index, progress=on_figure)
            else:
                names = pdfout.export_figures(job.file_path, blocks, figure_dir, progress=on_figure)
            job.phase = "Đang ghi file Markdown"
            links = {i: f"{figure_dir.name}/{n}" for i, n in names.items()}
            md_path.write_text(
                pipeline.render_markdown(blocks, segments, figure_links=links, figure_texts=figure_texts, **render),
                encoding="utf-8",
            )
            outputs["md"] = str(md_path)
        job.outputs = outputs
        job.output_path = outputs.get("pdf") or outputs.get("md", "")
        job.status = "done"

    @staticmethod
    def _output_base(job: Job, out_dir: Path) -> Path:
        """Tên file kết quả (chưa có đuôi). Dịch lại cùng job thì ghi đè; job mới thì không đè file cũ."""
        if job.outputs:
            first = Path(next(iter(job.outputs.values())))
            return first.with_name(first.name.split(".vi.")[0])
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(Path(job.file_name).stem)
        suffix = "" if job.scope == "Toàn bộ tài liệu" else "_" + _safe_name(
            job.scope.replace("Trang ", "tr").replace(", ", "_").replace("–", "-")
        )[:60]
        base, n = out_dir / f"{stem}{suffix}", 2
        while any(base.with_name(base.name + ext).exists() for ext in (".vi.md", ".vi.pdf")):
            base = out_dir / f"{stem}{suffix} ({n})"
            n += 1
        return base


class PartRenderer:
    """Đặt chữ vào PDF theo lô ngay khi mọi đoạn chạm tới các trang của lô đã dịch xong.

    Chạy trong luồng của job (callback tiến độ), xen kẽ với các luồng đang gọi model dịch.
    Lô được làm theo đúng thứ tự trang để ghép cuối cùng chỉ là nối các file.
    """

    def __init__(self, job: Job, blocks, segments, pages: list[int], *, interleave: bool, on_page=None,
                 figure_texts: dict | None = None, figure_notes: bool = False):
        """figure_texts: danh sách chữ trong hình đã có sẵn (lúc ghép bản dịch agent).
        figure_notes: chưa có sẵn thì gom từ nhãn đã dịch ngay lúc đặt chữ cho từng lô (lúc dịch bằng model)."""
        self.job, self.blocks, self.segments, self.interleave = job, blocks, segments, interleave
        self.on_page = on_page
        self.figure_texts, self.figure_notes = figure_texts, figure_notes
        self.batches = pdfout.batches(pages)
        self.folder = job.dir / "pdf_parts"
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        batch_of = {p: k for k, chunk in enumerate(self.batches) for p in chunk}
        self.needed: list[set[int]] = [set() for _ in self.batches]
        for s in segments:
            touched = {int(part[0]) for part in blocks[s.block].parts} or {s.page}
            for page in touched:
                if page in batch_of:
                    self.needed[batch_of[page]].add(s.id)
        self.done: set[int] = set()
        self.next = 0
        self.parts: list[str] = []
        job.pdf_parts, job.pdf_pages_ready = [], 0

    def mark(self, segment) -> bool:
        self.done.add(segment.id)
        return self.flush()

    def flush(self, force: bool = False) -> bool:
        rendered = False
        while self.next < len(self.batches) and (force or self.needed[self.next] <= self.done):
            self.render_next()
            rendered = True
        return rendered

    def render_next(self) -> bool:
        """Đặt chữ cho lô kế tiếp (không chờ bản dịch). Trả về False khi đã hết lô."""
        if self.next >= len(self.batches):
            return False
        chunk = self.batches[self.next]
        target = self.folder / f"part_{self.next:04d}.pdf"
        texts = self.figure_texts
        if texts is None and self.figure_notes:
            texts = figtext.collect(self.blocks, self.segments)
        pdfout.render_part(
            self.job.file_path, chunk, self.blocks, self.segments, target,
            interleave=self.interleave, on_page=self.on_page,
            notes=figtext.pdf_notes(self.blocks, texts, chunk) if texts else None,
        )
        self.parts.append(str(target))
        self.job.pdf_parts.append([str(target), chunk])
        self.job.pdf_pages_ready += len(chunk)
        self.next += 1
        return True


def _figure_text(job: Job, conf: dict) -> bool:
    return bool(job.options.get("figure_text", conf.get("figure_text", True)))


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
