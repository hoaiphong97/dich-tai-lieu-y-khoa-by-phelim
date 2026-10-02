"""Tách PDF thành các khối có cấu trúc (tiêu đề, đoạn văn, danh sách, chú thích, bảng).

Mục tiêu là văn bản đọc được theo đúng thứ tự, không phải giữ bố cục trang:
- đọc cột trái xong mới tới cột phải,
- bỏ tiêu đề đầu trang / chân trang / số trang lặp lại,
- nối từ bị ngắt gạch nối và đoạn văn bị cắt qua cột hoặc qua trang,
- nhận biết tiêu đề theo cỡ chữ, bảng bằng PyMuPDF find_tables().
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

BULLETS = "•●○◦▪■□▸►‣–*·\uf0b7\uf0a7"
# "Fig. 1.1 Lateral view…" là chú thích; "Table 1.1 summarizes…" là câu văn thường.
CAPTION_RE = re.compile(
    r"^(?i:fig\.?|figure|table|box|chart|plate)\s*[\dIVX]+[\d.\-–A-Za-z]*[.:]?(\s+[A-Z(\d]|\s*$)"
)
TERMINAL_RE = re.compile(r"[.!?:;…”\")\]]\s*$")
PAGE_NUMBER_RE = re.compile(r"^(page\s*)?[\divxlcdm]{1,6}$", re.IGNORECASE)
MARGIN = 0.08  # 8% trên/dưới trang được coi là lề (header/footer)

# PyMuPDF không hỗ trợ dùng từ nhiều luồng cùng lúc (kể cả với các tài liệu khác nhau).
# Mọi thao tác PyMuPDF trong app đi qua khoá này; chỉ giữ khoá trong từng bước ngắn.
MUPDF_LOCK = threading.RLock()


@dataclass
class Block:
    kind: str  # heading | para | list | caption | table | note | figure
    text: str
    page: int  # số trang, đếm từ 1
    level: int = 0  # cấp tiêu đề 1..4
    rows: list[list[str]] = field(default_factory=list)  # chỉ dùng cho bảng
    y: float = 0.0  # vị trí dọc trên trang, để chèn bảng/hình đúng chỗ
    # Vị trí trên PDF gốc để đặt bản dịch vào đúng chỗ. Một đoạn nối qua cột/trang có nhiều phần.
    # Mỗi phần: [trang, x0, y0, x1, y1, cỡ chữ, màu (int RGB), đậm (0/1), số ký tự]
    parts: list[list[float]] = field(default_factory=list)
    cell_boxes: list[list[list[float] | None]] = field(default_factory=list)  # bbox từng ô bảng


@dataclass
class PdfInfo:
    path: str
    pages: int
    title: str
    toc: list[dict]  # [{level, title, start, end}]
    scanned_pages: int


# --------------------------------------------------------------------------- #
# Thông tin tài liệu
# --------------------------------------------------------------------------- #

def pdf_info(path: str | Path) -> PdfInfo:
    with MUPDF_LOCK, pymupdf.open(path) as doc:
        toc_raw = doc.get_toc(simple=True)
        toc = []
        for i, (level, title, start) in enumerate(toc_raw):
            if level > 2 or start < 1:
                continue
            end = doc.page_count
            for later_level, _, later_start in toc_raw[i + 1:]:
                if later_level <= level and later_start >= start:
                    end = max(start, later_start - 1)
                    break
            toc.append({"level": level, "title": title.strip(), "start": start, "end": end})
        sample = list(range(0, doc.page_count, max(1, doc.page_count // 40)))
        scanned = sum(
            1 for i in sample if not doc[i].get_text("text").strip() and doc[i].get_images()
        )
        scanned = round(scanned * doc.page_count / max(1, len(sample)))
        title = (doc.metadata or {}).get("title") or Path(path).stem
        return PdfInfo(str(path), doc.page_count, title.strip(), toc, scanned)


def parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """'1-10, 15, 20-22' -> [1..10, 15, 20, 21, 22]. Rỗng = toàn bộ."""
    if not spec or not spec.strip():
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for part in re.split(r"[,;\s]+", spec.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if match:
            start, end = sorted((int(match.group(1)), int(match.group(2))))
            pages.update(range(start, end + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise ValueError(f"Khoảng trang không hợp lệ: '{part}'")
    valid = sorted(p for p in pages if 1 <= p <= page_count)
    if not valid:
        raise ValueError(f"Không có trang nào nằm trong khoảng 1–{page_count}")
    return valid


# --------------------------------------------------------------------------- #
# Tách khối thô trên từng trang
# --------------------------------------------------------------------------- #

@dataclass
class _Raw:
    text: str
    page: int
    bbox: tuple[float, float, float, float]
    size: float
    bold: float  # tỉ lệ ký tự in đậm
    lines: int
    color: int = 0  # màu chữ chủ đạo (sRGB dạng int)

    def part(self) -> list[float]:
        x0, y0, x1, y1 = self.bbox
        return [self.page, x0, y0, x1, y1, self.size, self.color, int(self.bold > 0.6), len(self.text)]


def _span_is_bold(span: dict) -> bool:
    return bool(span.get("flags", 0) & 16) or "bold" in span.get("font", "").lower()


def _block_from_dict(block: dict, page_no: int) -> _Raw | None:
    lines_text: list[str] = []
    sizes: Counter = Counter()
    colors: Counter = Counter()
    bold_chars = total_chars = 0
    for line in block.get("lines", []):
        parts = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            parts.append(text)
            n = len(text.strip())
            total_chars += n
            sizes[round(span.get("size", 0), 1)] += n
            colors[span.get("color", 0)] += n
            if _span_is_bold(span):
                bold_chars += n
        line_text = "".join(parts).strip()
        if line_text:
            lines_text.append(line_text)
    if not lines_text or total_chars == 0:
        return None
    return _Raw(
        _join_lines(lines_text),
        page_no,
        tuple(block["bbox"]),
        sizes.most_common(1)[0][0],
        bold_chars / total_chars,
        len(lines_text),
        colors.most_common(1)[0][0],
    )


def _join_lines(lines: list[str]) -> str:
    out = lines[0]
    for line in lines[1:]:
        if out.endswith("-") and len(out) > 1 and out[-2].isalpha() and line[:1].islower():
            out = out[:-1] + line  # từ bị ngắt gạch nối cuối dòng
        elif line[:1] in BULLETS and line[1:2] == " ":
            out += "\n" + line  # mục mới của danh sách
        else:
            out += " " + line
    return re.sub(r"[ \t]+", " ", out).strip()


def _reading_order(blocks: list[_Raw], page_width: float) -> list[_Raw]:
    """Sắp khối theo thứ tự đọc cho trang 1 hoặc 2 cột.

    Chia trang thành các dải, ngăn cách bởi khối rộng toàn trang; trong mỗi dải
    đọc hết cột trái rồi mới tới cột phải.
    """
    if not blocks:
        return blocks
    mid = page_width / 2
    full, narrow = [], []
    for b in blocks:
        width = b.bbox[2] - b.bbox[0]
        crosses_mid = b.bbox[0] < mid - 10 and b.bbox[2] > mid + 10
        (full if width > page_width * 0.55 or crosses_mid else narrow).append(b)
    if not any(b.bbox[0] >= mid for b in narrow):
        return sorted(blocks, key=lambda b: (round(b.bbox[1]), b.bbox[0]))

    full.sort(key=lambda b: b.bbox[1])
    ordered: list[_Raw] = []
    remaining = sorted(narrow, key=lambda b: b.bbox[1])
    for separator in full + [None]:
        bottom = separator.bbox[1] if separator else float("inf")
        band = [b for b in remaining if b.bbox[1] < bottom]
        left = [b for b in band if b.bbox[0] < mid]
        right = [b for b in band if b.bbox[0] >= mid]
        ordered.extend(left + right)
        remaining = [b for b in remaining if b.bbox[1] >= bottom]
        if separator:
            ordered.append(separator)
    return ordered


def _normalize_margin_text(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _in_margin(raw: _Raw, height: float) -> bool:
    return raw.bbox[1] < height * MARGIN or raw.bbox[3] > height * (1 - MARGIN)


# --------------------------------------------------------------------------- #
# Chuyển khối thô thành khối có nghĩa
# --------------------------------------------------------------------------- #

def extract_blocks(
    path: str | Path,
    pages: list[int],
    *,
    detect_tables: bool = True,
    progress=None,
    cancelled=None,
) -> list[Block]:
    raw_pages: list[tuple[int, list[_Raw], list[Block], float]] = []
    flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES) | pymupdf.TEXT_DEHYPHENATE
    with MUPDF_LOCK:
        doc = pymupdf.open(path)
    try:
        for count, page_no in enumerate(pages, 1):
            if cancelled and cancelled():
                break
            with MUPDF_LOCK:  # giữ khoá từng trang, để giao diện vẫn mở trang khác được
                page = doc[page_no - 1]
                tables, table_rects = _extract_tables(page, page_no) if detect_tables else ([], [])
                figures = _extract_figures(page, page_no, table_rects)
                raws = []
                for block in page.get_text("dict", flags=flags)["blocks"]:
                    if block.get("type") != 0:
                        continue
                    rect = pymupdf.Rect(block["bbox"])
                    area = rect.get_area()
                    if area and any((rect & t).get_area() > 0.5 * area for t in table_rects):
                        continue
                    raw = _block_from_dict(block, page_no)
                    if raw:
                        raws.append(raw)
                if not raws and not tables and page.get_images():
                    # Trang scan: chưa đọc được chữ, nhưng vẫn giữ cả trang như một hình.
                    r = page.rect
                    figures = [Block("figure", "scan", page_no, parts=[[page_no, r.x0, r.y0, r.x1, r.y1, 0, 0, 0, 0]])]
                raw_pages.append((page_no, _reading_order(raws, page.rect.width), tables + figures, page.rect.height))
            if progress:
                progress(count, len(pages))
        with MUPDF_LOCK:
            margin_keys = _margin_keys(doc, pages)
    finally:
        with MUPDF_LOCK:
            doc.close()

    body_size = _body_font_size(raw_pages)
    heading_levels = _heading_levels(raw_pages, body_size)

    blocks: list[Block] = []
    for _, raws, tables, height in raw_pages:
        page_blocks: list[Block] = []
        figure_rects = [pymupdf.Rect(b.parts[0][1:5]) for b in tables if b.kind == "figure" and b.text != "scan"]
        for raw in raws:
            # Header/footer thường cỡ chữ nhỏ; tiêu đề chương cỡ lớn nằm sát mép trên thì giữ lại.
            if _in_margin(raw, height) and raw.size < body_size * 1.15 and (
                PAGE_NUMBER_RE.match(raw.text) or _normalize_margin_text(raw.text) in margin_keys
            ):
                continue
            rect = pymupdf.Rect(raw.bbox)
            if figure_rects and any((rect & f).get_area() > 0.8 * max(rect.get_area(), 1e-6) for f in figure_rects):
                block = Block("label", raw.text.replace("\n", " "), raw.page)  # nhãn chữ nằm trong hình
            else:
                block = _classify(raw, body_size, heading_levels)
            block.y = raw.bbox[1]
            block.parts = [raw.part()]
            page_blocks.append(block)
        pending = sorted(tables, key=lambda t: t.y)
        for block in page_blocks:
            while pending and pending[0].y <= block.y:
                blocks.append(pending.pop(0))
            blocks.append(block)
        blocks.extend(pending)

    return _merge_continuations(blocks)


def _extract_tables(page, page_no: int) -> tuple[list[Block], list]:
    blocks, rects = [], []
    try:
        found = page.find_tables()
    except Exception:
        return blocks, rects
    for table in found.tables:
        try:
            data = table.extract()
        except Exception:
            continue
        rows, boxes = [], []
        for index, row in enumerate(data):
            cells = [re.sub(r"\s+", " ", cell or "").strip() for cell in row]
            if not any(cells):
                continue
            try:
                row_boxes = [list(b) if b else None for b in table.rows[index].cells]
            except (IndexError, AttributeError):
                row_boxes = [None] * len(cells)
            rows.append(cells)
            boxes.append(row_boxes)
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        x0, y0, x1, y1 = table.bbox
        blocks.append(Block(
            "table", "", page_no, rows=rows, y=y0, cell_boxes=boxes,
            parts=[[page_no, x0, y0, x1, y1, 0, 0, 0, 0]],
        ))
        rects.append(pymupdf.Rect(table.bbox))
    return blocks, rects


def _extract_figures(page, page_no: int, table_rects: list) -> list[Block]:
    """Hình ảnh và hình vẽ (sơ đồ, biểu đồ dạng vector) đủ lớn trên trang."""
    page_area = page.rect.get_area()
    candidates: list[pymupdf.Rect] = []
    try:
        candidates += [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
    except Exception:
        pass
    try:
        candidates += [pymupdf.Rect(r) for r in page.cluster_drawings()]
    except Exception:
        pass
    rects: list[pymupdf.Rect] = []
    for rect in candidates:
        rect &= page.rect
        if rect.is_empty or rect.width < 40 or rect.height < 40 or rect.get_area() < page_area * 0.008:
            continue
        if rect.get_area() > page_area * 0.92:
            continue  # nền trang / khung trang trí phủ cả trang
        if any((rect & t).get_area() > 0.5 * rect.get_area() for t in table_rects):
            continue
        rects.append(rect)
    # gộp các hình chồng lên nhau (ảnh + nét vẽ chú thích phía trên)
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if rects[i].intersects(rects[j]):
                    rects[i] |= rects.pop(j)
                    merged = True
                    break
            if merged:
                break
    rects.sort(key=lambda r: (r.y0, r.x0))
    return [
        Block("figure", "", page_no, y=r.y0, parts=[[page_no, r.x0, r.y0, r.x1, r.y1, 0, 0, 0, 0]])
        for r in rects
    ]


def _margin_keys(doc, pages: list[int]) -> set[str]:
    """Dòng chữ lặp lại ở lề trên/dưới (header, footer) trên nhiều trang.

    Lấy mẫu cả các trang ngoài khoảng đang dịch, để dịch 1–2 trang lẻ vẫn lọc được header.
    """
    total = doc.page_count
    sample = set(pages[:: max(1, len(pages) // 30)])
    anchor = pages[0]
    sample.update(p for p in range(anchor - 6, anchor + 7) if 1 <= p <= total)
    sample.update(range(1, total + 1, max(1, total // 12)))
    counter: Counter = Counter()
    for page_no in sample:
        page = doc[page_no - 1]
        height = page.rect.height
        keys = set()
        for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
            text = " ".join(text.split())
            if text and len(text) < 120 and (y0 < height * MARGIN or y1 > height * (1 - MARGIN)):
                keys.add(_normalize_margin_text(text))
        counter.update(keys)
    threshold = max(3, int(len(sample) * 0.3)) if len(sample) >= 3 else 99
    return {key for key, n in counter.items() if key and n >= threshold}


def _body_font_size(raw_pages) -> float:
    sizes: Counter = Counter()
    for _, raws, _, _ in raw_pages:
        for raw in raws:
            sizes[raw.size] += len(raw.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heading_levels(raw_pages, body: float) -> dict[float, int]:
    candidates: Counter = Counter()
    for _, raws, _, _ in raw_pages:
        for raw in raws:
            if raw.size >= body * 1.15 and len(raw.text) < 200 and raw.lines <= 4:
                candidates[raw.size] += 1
    sizes = sorted(candidates, reverse=True)[:3]
    return {size: level for level, size in enumerate(sizes, 1)}


def _classify(raw: _Raw, body: float, heading_levels: dict[float, int]) -> Block:
    text = raw.text
    if raw.size in heading_levels and len(text) < 200:
        return Block("heading", text.replace("\n", " "), raw.page, level=heading_levels[raw.size])
    if CAPTION_RE.match(text):
        return Block("caption", text.replace("\n", " "), raw.page)
    if raw.bold > 0.8 and len(text) < 100 and raw.lines <= 2 and not TERMINAL_RE.search(text.rstrip(":")):
        return Block("heading", text, raw.page, level=min(4, len(heading_levels) + 1))
    if text[:1] in BULLETS and text[1:2] == " ":
        items = [re.sub(rf"^[{re.escape(BULLETS)}]\s*", "", line).strip() for line in text.split("\n")]
        return Block("list", "\n".join(i for i in items if i), raw.page)
    if raw.size < body * 0.85 and len(text) < 400:
        return Block("note", text.replace("\n", " "), raw.page)
    return Block("para", text.replace("\n", " "), raw.page)


def _merge_continuations(blocks: list[Block]) -> list[Block]:
    """Nối đoạn văn bị cắt ngang cột hoặc trang (câu chưa kết thúc + đoạn sau viết thường)."""
    merged: list[Block] = []
    for block in blocks:
        # Bỏ qua hình/nhãn hình chen giữa: câu vẫn có thể chạy tiếp qua một hình.
        prev = next((b for b in reversed(merged) if b.kind not in ("figure", "label")), None)
        if (
            prev is not None
            and prev.kind == "para"
            and block.kind == "para"
            and not TERMINAL_RE.search(prev.text)
            and block.text[:1].islower()
        ):
            if prev.text.endswith("-") and prev.text[-2:-1].isalpha():
                prev.text = prev.text[:-1] + block.text
            else:
                prev.text = f"{prev.text} {block.text}"
            prev.parts.extend(block.parts)
            continue
        merged.append(block)
    return merged
