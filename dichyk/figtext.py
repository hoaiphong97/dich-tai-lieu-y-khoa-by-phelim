"""Chữ trong hình: gom lại thành danh sách Anh → Việt để gắn ngay dưới mỗi hình.

Hai nguồn:
- Nhãn chữ PDF lưu dạng ký tự (khối "label", app đã tách và dịch như mọi đoạn khác).
- Chữ nằm trong ảnh (điểm ảnh): ở chế độ agent, app xuất ảnh từng hình vào thư mục job,
  agent xem ảnh và ghi `figure_texts*.jsonl`; app nạp lại khi ghép.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

from .extract import MUPDF_LOCK, Block

IMAGE_DIR = "hinh-agent"
WORD = re.compile(r"[^\W\d_]{2,}")
UNITS = {
    "mm", "cm", "dm", "km", "nm", "µm", "um", "ml", "dl", "mg", "kg", "µg", "ug", "ng", "ms", "hz", "khz",
    "mhz", "mmhg", "kpa", "mol", "mmol", "iu",
}

Items = list[tuple[str, str]]  # [(tiếng Anh, tiếng Việt)]


def worth_listing(text: str) -> bool:
    """Có ít nhất một từ ≥ 2 chữ cái không phải đơn vị: bỏ nhãn A, B, 1, 2 và số đo như "12 mm".
    Giữ viết tắt ngắn có nghĩa như LV, RA."""
    return any(w.lower() not in UNITS for w in WORD.findall(text))


def figure_id(index: int) -> str:
    return f"fig-{index:04d}"


def figures(blocks: list[Block]) -> list[tuple[int, Block]]:
    """Các hình thật (không tính trang scan), kèm chỉ số khối."""
    return [(i, b) for i, b in enumerate(blocks) if b.kind == "figure" and b.parts and b.text != "scan"]


def _rect(block: Block) -> pymupdf.Rect:
    return pymupdf.Rect(block.parts[0][1:5])


def label_owners(blocks: list[Block]) -> dict[int, list[int]]:
    """{chỉ số hình: [chỉ số các khối nhãn nằm trong hình đó]}."""
    by_page: dict[int, list[tuple[int, pymupdf.Rect]]] = {}
    for i, b in figures(blocks):
        by_page.setdefault(b.page, []).append((i, _rect(b)))
    owners: dict[int, list[int]] = {}
    for i, b in enumerate(blocks):
        if b.kind != "label" or not b.parts or b.page not in by_page:
            continue
        rect = _rect(b)
        area = max(rect.get_area(), 1e-6)
        best = max(by_page[b.page], key=lambda f: (rect & f[1]).get_area())
        if (rect & best[1]).get_area() > 0.5 * area:
            owners.setdefault(best[0], []).append(i)
    return owners


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower()).strip()


def collect(blocks: list[Block], segments, agent_items: dict[int, Items] | None = None) -> dict[int, Items]:
    """{chỉ số hình: [(Anh, Việt)]} — nhãn dạng ký tự trước, rồi chữ agent đọc từ ảnh (bỏ trùng)."""
    dst_of = {s.block: s.dst for s in segments if s.kind == "label"}
    out: dict[int, Items] = {}
    for fig, labels in label_owners(blocks).items():
        for i in labels:
            en = " ".join(blocks[i].text.split())
            if worth_listing(en):
                out.setdefault(fig, []).append((en, dst_of.get(i, "")))
    for fig, items in (agent_items or {}).items():
        seen = {_key(en) for en, _ in out.get(fig, [])}
        for en, vi in items:
            if worth_listing(en) and _key(en) not in seen:
                seen.add(_key(en))
                out.setdefault(fig, []).append((en, vi))
    return out


# --------------------------------------------------------------------------- #
# Hiển thị
# --------------------------------------------------------------------------- #

def _line(en: str, vi: str) -> str:
    return f"{en} → {vi}" if vi and _key(vi) != _key(en) else en


def markdown(items: Items) -> str:
    rows = "\n".join(f"> - {_line(en, vi).replace(chr(10), ' ')}" for en, vi in items)
    return f"> **Chữ trong hình**\n>\n{rows}"


def note_text(items: Items) -> str:
    return "Chữ trong hình (Anh → Việt):\n" + "\n".join(f"• {_line(en, vi)}" for en, vi in items)


def pdf_notes(blocks: list[Block], texts: dict[int, Items], pages) -> dict[int, list[tuple[pymupdf.Rect, str]]]:
    """{số trang: [(khung hình, nội dung ghi chú)]} cho các trang trong `pages`."""
    wanted = set(pages)
    notes: dict[int, list[tuple[pymupdf.Rect, str]]] = {}
    for fig, items in texts.items():
        block = blocks[fig]
        if items and block.page in wanted:
            notes.setdefault(block.page, []).append((_rect(block), note_text(items)))
    return notes


def add_notes(page, notes: list[tuple[pymupdf.Rect, str]]) -> None:
    """Biểu tượng ghi chú ở góc trên-phải mỗi hình; bấm vào hiện danh sách chữ. Không đụng bố cục trang."""
    for rect, text in notes:
        x = min(max(rect.x1 - 20, page.rect.x0), page.rect.x1 - 20)
        y = min(max(rect.y0 + 2, page.rect.y0), page.rect.y1 - 20)
        annot = page.add_text_annot(pymupdf.Point(x, y), text, icon="Comment")
        annot.set_info(content=text, title="Chữ trong hình")
        annot.set_colors(stroke=(0.06, 0.46, 0.43))
        annot.update()


# --------------------------------------------------------------------------- #
# Chế độ agent
# --------------------------------------------------------------------------- #

def export_images(source: str | Path, blocks: list[Block], job_dir: Path, *, dpi: int = 150,
                  progress=None, cancelled=None) -> list[dict]:
    """Xuất ảnh từng hình (bản gốc) cho agent đọc. Trả về danh sách mô tả hình."""
    folder = job_dir / IMAGE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    owners = label_owners(blocks)
    items = figures(blocks)
    out = []
    with MUPDF_LOCK, pymupdf.open(source) as doc:
        for n, (index, block) in enumerate(items, 1):
            if cancelled and cancelled():
                break
            page = doc[block.page - 1]
            name = f"{figure_id(index)}.png"
            clip = (_rect(block) + (-4, -4, 4, 4)) & page.rect
            page.get_pixmap(dpi=dpi, clip=clip, annots=False).save(str(folder / name))
            known = [" ".join(blocks[i].text.split()) for i in owners.get(index, [])]
            out.append({"figure": figure_id(index), "page": block.page, "image": f"{IMAGE_DIR}/{name}", "known": known})
            if progress:
                progress(n, len(items))
    return out


def load_agent(job_dir: Path) -> tuple[dict[int, Items], set[int]]:
    """Đọc figure_texts*.jsonl. Trả về ({chỉ số hình: [(Anh, Việt)]}, {các hình agent đã xử lý})."""
    texts: dict[int, Items] = {}
    done: set[int] = set()
    for path in sorted(job_dir.glob("figure_texts*.jsonl")):
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    index = int(str(record["figure"]).rsplit("-", 1)[-1])
                    items = [
                        (str(it.get("en", "")).strip(), str(it.get("vi", "")).strip())
                        for it in record.get("items") or [] if isinstance(it, dict)
                    ]
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                done.add(index)
                texts[index] = [(en, vi) for en, vi in items if en]
    return texts, done
