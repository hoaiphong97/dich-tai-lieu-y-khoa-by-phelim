from __future__ import annotations

import re

import pymupdf

from dichyk import extract, pdfout, pipeline
from tests.sample_pdf import build
from tests.test_core import FakeEngine


def _with_figure_and_background(tmp_path) -> str:
    """PDF mẫu + một ảnh raster và một ô nền màu phía sau chữ trên trang 3."""
    path = build(str(tmp_path / "book.pdf"))
    doc = pymupdf.open(path)
    page = doc[2]
    page.draw_rect(pymupdf.Rect(45, 200, 430, 225), color=None, fill=(1, 0.95, 0.8), overlay=False)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), False)
    pix.clear_with(120)
    page.insert_image(pymupdf.Rect(300, 300, 460, 420), pixmap=pix)
    out = str(tmp_path / "book2.pdf")
    doc.save(out)
    return out


def test_split_by_weights():
    text = "một hai ba bốn năm sáu bảy tám chín mười"
    parts = pdfout.split_by_weights(text, [50, 50])
    assert len(parts) == 2 and " ".join(parts) == text
    assert abs(len(parts[0]) - len(parts[1])) < 8
    assert pdfout.split_by_weights("abc", [1]) == ["abc"]
    assert len(pdfout.split_by_weights("không có", [10, 10, 10])) == 3  # ít khoảng trắng vẫn không lỗi


def test_figures_detected(tmp_path):
    pdf = _with_figure_and_background(tmp_path)
    blocks = extract.extract_blocks(pdf, [3])
    figures = [b for b in blocks if b.kind == "figure"]
    assert len(figures) == 1
    x0, y0, x1, y1 = figures[0].parts[0][1:5]
    assert 290 < x0 < 320 and 290 < y0 < 310


def test_render_pdf_keeps_images_and_places_vietnamese(tmp_path):
    pdf = _with_figure_and_background(tmp_path)
    pages = [1, 2, 3]
    blocks = extract.extract_blocks(pdf, pages)
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    pipeline.translate_segments(segments, FakeEngine(), None)
    out = tmp_path / "out.vi.pdf"
    stats = pdfout.render_pdf(pdf, pages, blocks, segments, out)
    assert stats["placed"] > 10

    doc = pymupdf.open(out)
    assert doc.page_count == 3
    page = doc[2]
    text = page.get_text()
    assert "từ" in text  # chữ Việt (FakeEngine thay từ tiếng Anh bằng "từ")
    assert "Occlusal" not in text and "summarizes" not in text  # chữ gốc đã bị xoá
    assert "150" in text and "600" in text  # số trong bảng giữ nguyên
    assert len(page.get_images()) == 1  # ảnh còn nguyên
    # nền màu phía sau chữ vẫn còn (không bị xoá trắng)
    fills = [d.get("fill") for d in page.get_drawings() if d.get("fill")]
    assert any(abs(f[0] - 1) < 0.01 and abs(f[2] - 0.8) < 0.01 for f in fills)
    # header lặp lại và số trang không bị đụng tới
    assert "Management of Temporomandibular Disorders" in text


def test_render_pdf_subset_and_interleave(tmp_path):
    pdf = build(str(tmp_path / "book.pdf"))
    blocks = extract.extract_blocks(pdf, [3])
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    pipeline.translate_segments(segments, FakeEngine(), None)
    out = tmp_path / "x.pdf"
    pdfout.render_pdf(pdf, [3], blocks, segments, out, interleave=True)
    doc = pymupdf.open(out)
    assert doc.page_count == 2
    assert "Occlusal Forces" in doc[0].get_text() and "Occlusal Forces" not in doc[1].get_text()


def test_markdown_with_figures(tmp_path):
    pdf = _with_figure_and_background(tmp_path)
    blocks = extract.extract_blocks(pdf, [3])
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    pipeline.translate_segments(segments, FakeEngine(), None)
    folder = tmp_path / "hinh-book"
    names = pdfout.export_figures(pdf, blocks, folder)
    assert len(names) == 1 and (folder / next(iter(names.values()))).stat().st_size > 100
    md = pipeline.render_markdown(
        blocks, segments, title="t", source_name="book.pdf",
        figure_links={i: f"hinh-book/{n}" for i, n in names.items()},
    )
    assert re.search(r"!\[Hình — trang 3\]\(hinh-book/tr3-1\.png\)", md)
    assert pdfout.slug("Sách Giải Phẫu (tập 2)") == "sach-giai-phau-tap-2"


def test_render_pdf_in_small_batches_keeps_pages_and_toc(tmp_path):
    pdf = build(str(tmp_path / "book.pdf"))
    pages = [1, 2, 3]
    blocks = extract.extract_blocks(pdf, pages)
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    pipeline.translate_segments(segments, FakeEngine(), None)
    out = tmp_path / "batched.pdf"
    pdfout.render_pdf(pdf, pages, blocks, segments, out, batch_size=1)
    doc = pymupdf.open(out)
    assert doc.page_count == 3
    assert "Occlusal" not in doc[2].get_text() and "từ" in doc[2].get_text()
    assert [t[1] for t in doc.get_toc()] == ["Chapter 1 Functional Anatomy", "Occlusal Forces"]
    assert [t[2] for t in doc.get_toc()] == [1, 3]
    assert not (tmp_path / "batched.pdf.parts").exists()  # file tạm đã dọn


def test_part_renderer_renders_batches_in_order(tmp_path, monkeypatch):
    from dichyk import jobs, pdfout as po

    monkeypatch.setenv("DICHYK_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(po, "BATCH_PAGES", 1)
    pdf = build(str(tmp_path / "book.pdf"))
    pages = [1, 2, 3]
    blocks = extract.extract_blocks(pdf, pages)
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    job = jobs.Job(type="pdf", file_path=pdf, file_name="book.pdf", page_list=pages)
    renderer = jobs.PartRenderer(job, blocks, segments, pages, interleave=False)
    assert len(renderer.batches) == 3
    engine = FakeEngine()
    rendered_after = []
    for s in segments:  # dịch tuần tự, theo dõi lúc nào mỗi lô được đặt chữ
        pipeline.translate_one(engine, None, s)
        if renderer.mark(s):
            rendered_after.append((s.id, job.pdf_pages_ready))
    renderer.flush(force=True)
    assert job.pdf_pages_ready == 3 and len(renderer.parts) == 3
    # trang 1 xong trước khi dịch hết (đoạn vắt sang trang 2 phải chờ), các lô theo đúng thứ tự
    assert rendered_after[0][0] < len(segments) - 1
    assert [p for _, p in rendered_after] == sorted(p for _, p in rendered_after)
    pdfout.merge_parts(renderer.parts, tmp_path / "m.pdf", source=pdf, pages=pages)
    assert pymupdf.open(tmp_path / "m.pdf").page_count == 3


def test_assemble_reports_page_progress_and_can_cancel(tmp_path, monkeypatch):
    from dichyk import jobs, pdfout as po, settings

    monkeypatch.setenv("DICHYK_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(po, "BATCH_PAGES", 1)
    settings.save({"engine": "agent", "preset": "agent", "output_dir": str(tmp_path / "out")})
    pdf = build(str(tmp_path / "book.pdf"))
    pages = [1, 2, 3]
    blocks = extract.extract_blocks(pdf, pages)
    segments = pipeline.build_segments(blocks, None, bilingual=False)
    manager = jobs.JobManager()
    job = jobs.Job(type="pdf", file_path=pdf, file_name="book.pdf", page_list=pages,
                   scope="Toàn bộ tài liệu", status="waiting_agent")
    manager._register(job)
    pipeline.save_state(job.dir, blocks, segments)

    seen = []
    real = po.render_part

    def spy(*args, on_page=None, **kwargs):  # ghi lại tiến độ mà giao diện sẽ thấy sau mỗi trang
        def hook(page_no):
            on_page(page_no)
            seen.append((page_no, job.step_done, job.step_total, job.phase))
        return real(*args, on_page=hook, **kwargs)

    monkeypatch.setattr(po, "render_part", spy)
    manager._assemble(job)
    assert job.status == "done", job.error
    assert [s[:3] for s in seen] == [(1, 1, 3), (2, 2, 3), (3, 3, 3)]
    assert "trang 2/3" in seen[1][3]
    assert job.phase.startswith("Đã ghép")
    assert pymupdf.open(job.outputs["pdf"]).page_count == 3
    assert not (job.dir / "pdf_parts").exists()

    # Huỷ giữa chừng: quay về chờ ghép, dọn các lô tạm, không báo lỗi
    job.status = "waiting_agent"
    job.cancel_event.set()
    manager._assemble(job)
    assert job.status == "waiting_agent" and job.phase == "Đã huỷ ghép" and not job.error
    assert not (job.dir / "pdf_parts").exists() and job.pdf_pages_ready == 0


def test_agent_task_split_and_load(tmp_path):
    blocks = pipeline.text_to_blocks("First paragraph here.\n\nSecond one.\n\nThird one.\n\nFourth one.")
    segs = pipeline.build_segments(blocks, None, bilingual=False)
    tasks = pipeline.export_agent_task(tmp_path, segs, source="x", scope="y", parts=2)
    assert [t.name for t in tasks] == ["AGENT_TASK_1.md", "AGENT_TASK_2.md"]
    assert "segments_2.jsonl" in tasks[1].read_text(encoding="utf-8")
    assert "phần 2/2" in tasks[1].read_text(encoding="utf-8")
    assert len((tmp_path / "segments_1.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    (tmp_path / "translations_1.jsonl").write_text('{"id": 0, "dst": "Đoạn một."}\n', encoding="utf-8")
    (tmp_path / "translations_2.jsonl").write_text('{"id": 3, "dst": "Đoạn bốn."}\n', encoding="utf-8")
    assert pipeline.load_agent_translations(tmp_path, segs) == 2
    assert segs[0].dst == "Đoạn một." and segs[3].dst == "Đoạn bốn."
    # xuất lại với 1 phần: file phần cũ bị dọn, bản dịch giữ lại
    pipeline.export_agent_task(tmp_path, segs, source="x", scope="y", parts=1)
    assert not (tmp_path / "segments_1.jsonl").exists() and (tmp_path / "translations_1.jsonl").exists()
