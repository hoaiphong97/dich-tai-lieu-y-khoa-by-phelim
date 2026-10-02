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
