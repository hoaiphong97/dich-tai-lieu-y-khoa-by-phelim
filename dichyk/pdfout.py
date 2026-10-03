"""Xuất bản dịch có hình.

- render_pdf: đặt bản dịch vào chính PDF gốc. Chữ gốc bị xoá, còn hình ảnh, nét vẽ, nền màu
  giữ nguyên. Chữ tiếng Việt (font Noto Sans) được đặt vào đúng khung cũ, tự co cỡ chữ cho vừa.
- export_figures: cắt các hình ra file PNG để chèn vào Markdown.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from . import figtext
from .extract import MUPDF_LOCK, Block

try:
    import pymupdf_fonts
except ImportError:  # pragma: no cover - thiếu gói font thì dùng font dựng sẵn của MuPDF
    pymupdf_fonts = None

FONT_CSS = (
    "@font-face {font-family: vn; src: url(r.ttf);}"
    "@font-face {font-family: vn; src: url(b.ttf); font-weight: bold;}"
    "@font-face {font-family: vn; src: url(i.ttf); font-style: italic;}"
    "* {font-family: vn; margin: 0; padding: 0;}"
)
TEXT_KINDS = {"heading", "para", "list", "caption", "note", "label"}


def _archive() -> pymupdf.Archive | None:
    if pymupdf_fonts is None:
        return None
    archive = pymupdf.Archive()
    archive.add(pymupdf_fonts.myfont("notos"), "r.ttf")
    archive.add(pymupdf_fonts.myfont("notosbo"), "b.ttf")
    archive.add(pymupdf_fonts.myfont("notosit"), "i.ttf")
    return archive


@dataclass
class Placement:
    page: int
    rect: pymupdf.Rect
    text: str
    size: float  # 0 = đo từ chữ gốc trong khung
    color: int
    bold: bool
    kind: str


# --------------------------------------------------------------------------- #
# Chia bản dịch cho các phần của một đoạn (đoạn vắt qua cột/trang)
# --------------------------------------------------------------------------- #

def split_by_weights(text: str, weights: list[float]) -> list[str]:
    """Chia văn bản thành len(weights) phần theo tỉ lệ, cắt ở khoảng trắng gần nhất."""
    if len(weights) <= 1:
        return [text]
    total = sum(weights) or 1
    spaces = [m.start() for m in re.finditer(r" ", text)]
    cuts: list[int] = []
    acc = 0.0
    for w in weights[:-1]:
        acc += w
        target = len(text) * acc / total
        candidates = [s for s in spaces if not cuts or s > cuts[-1]]
        cut = min(candidates, key=lambda s: abs(s - target)) if candidates else len(text)
        cuts.append(cut)
    pieces, start = [], 0
    for cut in cuts:
        pieces.append(text[start:cut].strip())
        start = cut
    pieces.append(text[start:].strip())
    return pieces


# --------------------------------------------------------------------------- #
# Đặt chữ vào PDF
# --------------------------------------------------------------------------- #

def _placements(blocks: list[Block], segments) -> tuple[list[Placement], dict[int, list[pymupdf.Rect]]]:
    by_block: dict[int, object] = {}
    cells: dict[int, dict[str, str]] = {}
    for s in segments:
        if s.kind == "cell":
            if s.dst:
                cells.setdefault(s.block, {})[s.text] = s.dst
        else:
            by_block[s.block] = s

    placements: list[Placement] = []
    obstacles: dict[int, list[pymupdf.Rect]] = {}  # hình và bảng: không được tràn chữ lên
    for index, block in enumerate(blocks):
        if block.kind in ("figure", "table"):
            for part in block.parts:
                obstacles.setdefault(int(part[0]), []).append(pymupdf.Rect(part[1:5]))
        if block.kind == "table":
            table_cells = cells.get(index, {})
            for row, boxes in zip(block.rows, block.cell_boxes):
                for text, box in zip(row, boxes):
                    if box and text in table_cells and table_cells[text] != text:
                        rect = pymupdf.Rect(box) + (1.5, 1, -1.5, -1)
                        placements.append(Placement(block.page, rect, table_cells[text], 0, 0, False, "cell"))
            continue
        segment = by_block.get(index)
        if block.kind not in TEXT_KINDS or segment is None or not block.parts:
            continue
        dst = (segment.dst or "").strip()
        if not dst or dst == segment.text.strip():
            continue  # chưa dịch: giữ nguyên chữ gốc
        if block.kind == "list":
            pieces = ["\n".join("• " + line for line in dst.split("\n"))]
            parts = block.parts[:1]
        else:
            parts = block.parts
            pieces = split_by_weights(dst, [p[8] for p in parts])
        for part, piece in zip(parts, pieces):
            page, x0, y0, x1, y1, size, color, bold, _ = part
            placements.append(Placement(
                int(page), pymupdf.Rect(x0, y0, x1, y1), piece, float(size), int(color),
                bool(bold) or block.kind == "heading", block.kind,
            ))
    return placements, obstacles


def _measure_size(page, rect: pymupdf.Rect) -> float:
    sizes = [
        span["size"]
        for b in page.get_text("dict", clip=rect)["blocks"] if b.get("type") == 0
        for line in b["lines"] for span in line["spans"] if span["text"].strip()
    ]
    return max(set(sizes), key=sizes.count) if sizes else 9.0


def _html(p: Placement) -> str:
    color = f"#{p.color & 0xFFFFFF:06x}"
    align = "justify" if p.kind == "para" else "left"
    line_height = 1.15 if p.kind in ("heading", "label", "cell") else 1.22
    style = (
        f"font-size: {p.size:.2f}px; line-height: {line_height}; color: {color}; text-align: {align};"
        + (" font-weight: bold;" if p.bold else "")
        + (" font-style: italic;" if p.kind == "caption" else "")
    )
    body = html.escape(unicodedata.normalize("NFC", p.text)).replace("\n", "<br>")
    return f'<div style="{style}">{body}</div>'


def _room_below(rect: pymupdf.Rect, page_rect: pymupdf.Rect, others: list[pymupdf.Rect]) -> pymupdf.Rect:
    """Nới khung xuống tới vật cản gần nhất bên dưới (khối chữ khác, hình, bảng) hoặc lề dưới."""
    limit = page_rect.y1 - page_rect.height * 0.04
    for o in others:
        overlaps_x = o.x0 < rect.x1 - 2 and o.x1 > rect.x0 + 2
        if overlaps_x and o.y0 >= rect.y1 - 1:
            limit = min(limit, o.y0 - 2)
    return pymupdf.Rect(rect.x0, rect.y0, rect.x1, max(rect.y1, limit))


def _fit(page, p: Placement, others: list[pymupdf.Rect], archive) -> float:
    """Đặt chữ vào khung. Trả về tỉ lệ co chữ (1 = giữ nguyên cỡ chữ)."""
    html_text = _html(p)
    options = {"css": FONT_CSS, "archive": archive} if archive else {}
    spare, scale = page.insert_htmlbox(p.rect, html_text, scale_low=0.7, **options)
    if spare >= 0:
        return scale
    bigger = _room_below(p.rect, page.rect, others)
    for low in (0.55, 0):
        spare, scale = page.insert_htmlbox(bigger, html_text, scale_low=low, **options)
        if spare >= 0:
            return scale
    return 0.0


# Mỗi lô được đặt chữ rồi lưu ra file riêng: RAM chỉ phụ thuộc cỡ lô, không phụ thuộc độ dày sách
# (file 258 MB / 400 trang: 2,7 GB khi làm một lượt → ~440 MB khi chia lô 25 trang).
BATCH_PAGES = 25


def batches(pages: list[int], size: int | None = None) -> list[list[int]]:
    size = size or BATCH_PAGES  # đọc lúc gọi để chỉnh được cỡ lô
    return [pages[i:i + size] for i in range(0, len(pages), size)]


def render_part(
    source: str | Path,
    pages: list[int],
    blocks: list[Block],
    segments,
    target: str | Path,
    *,
    interleave: bool = False,
    placements: tuple | None = None,
    on_page=None,
    notes: dict | None = None,
) -> dict:
    """Đặt bản dịch vào một lô trang và lưu thành một file PDF riêng.

    on_page(số trang gốc): gọi sau mỗi trang đặt chữ xong, để báo tiến độ.
    notes: {số trang: [(khung hình, nội dung)]} — ghi chú "chữ trong hình" gắn vào góc mỗi hình.
    """
    placements_list, obstacles = placements or _placements(blocks, segments)
    wanted = set(pages)
    by_page: dict[int, list[Placement]] = {}
    for p in placements_list:
        if p.page in wanted:
            by_page.setdefault(p.page, []).append(
                Placement(p.page, pymupdf.Rect(p.rect), p.text, p.size, p.color, p.bold, p.kind)
            )

    with MUPDF_LOCK:
        archive = _archive()
        doc = pymupdf.open(source)
        doc.select([p - 1 for p in pages])
        shrunk = 0
        for index, page_no in enumerate(pages):
            page = doc[index]
            items = by_page.get(page_no, [])
            if items:
                for p in items:
                    if p.size <= 0:
                        p.size = _measure_size(page, p.rect)
                for p in items:
                    page.add_redact_annot(p.rect + (0.4, 0.4, -0.4, -0.4))
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                    text=pymupdf.PDF_REDACT_TEXT_REMOVE,
                )
                rects = [p.rect for p in items] + obstacles.get(page_no, [])
                for p in items:
                    others = [r for r in rects if r is not p.rect]
                    if _fit(page, p, others, archive) < 0.7:
                        shrunk += 1
            if notes and notes.get(page_no):
                figtext.add_notes(page, notes[page_no])
            if on_page:
                on_page(page_no)

        if interleave:
            original = pymupdf.open(source)
            out = pymupdf.open()
            for index, page_no in enumerate(pages):
                out.insert_pdf(original, from_page=page_no - 1, to_page=page_no - 1)
                out.insert_pdf(doc, from_page=index, to_page=index)
            doc.close()
            original.close()
            doc = out

        try:
            doc.subset_fonts()
        except Exception:
            pass
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(target), garbage=3, deflate=True)
        doc.close()
    return {"placed": sum(len(v) for v in by_page.values()), "shrunk": shrunk}


def merge_parts(
    parts: list[str | Path], target: str | Path, *, source: str | Path | None = None,
    pages: list[int] | None = None, interleave: bool = False,
) -> None:
    """Ghép các lô thành PDF hoàn chỉnh; mang theo mục lục (bookmark) của bản gốc nếu có."""
    with MUPDF_LOCK:
        out = pymupdf.open()
        for part in parts:
            with pymupdf.open(part) as d:
                out.insert_pdf(d)
        if source and pages:
            try:
                with pymupdf.open(source) as src:
                    toc = src.get_toc(simple=True)
                position = {p: (2 * i + 2 if interleave else i + 1) for i, p in enumerate(pages)}
                new_toc, last_level = [], 0
                for level, title, page_no in toc:
                    if page_no in position and level <= last_level + 1:
                        new_toc.append([level, title, position[page_no]])
                        last_level = level
                if new_toc and new_toc[0][0] == 1:
                    out.set_toc(new_toc)
            except Exception:
                pass  # mục lục hỏng không được làm hỏng cả file
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        out.save(str(target), garbage=3, deflate=True)
        out.close()


def render_pdf(
    source: str | Path,
    pages: list[int],
    blocks: list[Block],
    segments,
    target: str | Path,
    *,
    interleave: bool = False,
    progress=None,
    batch_size: int | None = None,
    notes: dict | None = None,
) -> dict:
    """Ghi PDF đã dịch chỉ gồm các trang trong `pages` (làm theo lô để tiết kiệm RAM)."""
    target = Path(target)
    work = target.with_name(target.name + ".parts")
    work.mkdir(parents=True, exist_ok=True)
    precomputed = _placements(blocks, segments)
    parts, placed, shrunk, done = [], 0, 0, 0
    try:
        for k, chunk in enumerate(batches(pages, batch_size)):
            part = work / f"part_{k:04d}.pdf"
            stats = render_part(
                source, chunk, blocks, segments, part, interleave=interleave, placements=precomputed, notes=notes,
            )
            parts.append(part)
            placed += stats["placed"]
            shrunk += stats["shrunk"]
            done += len(chunk)
            if progress:
                progress(done, len(pages))
        merge_parts(parts, target, source=source, pages=pages, interleave=interleave)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)
        try:
            work.rmdir()
        except OSError:
            pass
    return {"placed": placed, "shrunk": shrunk}


def render_page_png(path: str | Path, index: int, dpi: int = 110) -> bytes:
    """Ảnh một trang (đếm từ 0) để xem so sánh trong app."""
    with MUPDF_LOCK, pymupdf.open(path) as doc:
        index = max(0, min(index, doc.page_count - 1))
        return doc[index].get_pixmap(dpi=dpi).tobytes("png")


# --------------------------------------------------------------------------- #
# Hình cho Markdown
# --------------------------------------------------------------------------- #

def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).replace("đ", "d").replace("Đ", "D")
    ascii_text = ascii_text.encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()[:60] or "tai-lieu"


def export_figures(
    source: str | Path, blocks: list[Block], folder: Path, dpi: int = 150,
    page_index: dict[int, int] | None = None, progress=None,
) -> dict[int, str]:
    """Cắt từng hình thành PNG. Trả về {chỉ số block: tên file}.

    page_index: {số trang gốc: chỉ số trang trong `source`} khi cắt từ PDF đã dịch
    (để nhãn chữ trong hình là tiếng Việt). Bỏ trống = `source` là PDF gốc.
    """
    figures = [(i, b) for i, b in enumerate(blocks) if b.kind == "figure" and b.parts]
    if not figures:
        return {}
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[int, str] = {}
    counter: dict[int, int] = {}
    with MUPDF_LOCK, pymupdf.open(source) as doc:
        for index, block in figures:
            page_no = int(block.parts[0][0])
            rect = pymupdf.Rect(block.parts[0][1:5]) + (-4, -4, 4, 4)
            counter[page_no] = counter.get(page_no, 0) + 1
            name = f"tr{page_no}-{counter[page_no]}.png"
            page = doc[page_index[page_no] if page_index else page_no - 1]
            page.get_pixmap(dpi=dpi, clip=rect & page.rect, annots=False).save(str(folder / name))
            out[index] = name
            if progress:
                progress(len(out), len(figures))
    return out
