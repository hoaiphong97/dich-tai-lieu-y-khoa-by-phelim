"""Kiểm lỗi bản dịch bằng quy tắc (không cần model): đánh dấu đoạn cần người xem lại."""

from __future__ import annotations

import re
import unicodedata

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
ENGLISH_STOPWORDS = {
    "the", "of", "and", "with", "is", "are", "was", "were", "which", "that", "this",
    "these", "to", "in", "for", "by", "from", "be", "has", "have", "can", "may",
}
FIG_RE = re.compile(r"\b(?:Fig\.|Figure|Table)\s+(\d+(?:[.\-–]\d+)*[A-Za-z]?)")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def _numbers(text: str) -> set[str]:
    # 2.5 và 2,5 coi như nhau; bỏ dấu phân cách hàng nghìn khi so sánh
    return {n.replace(",", ".") for n in NUMBER_RE.findall(text)}


def check(src: str, dst: str, kind: str, required_terms: list[dict] | None = None) -> list[str]:
    flags: list[str] = []
    if not dst.strip():
        return ["Bản dịch rỗng"]
    if kind in ("cell",) and len(src) < 4:
        return flags

    missing = sorted(_numbers(src) - _numbers(dst), key=len, reverse=True)
    if missing:
        flags.append("Thiếu số liệu: " + ", ".join(missing[:5]))

    words = re.findall(r"[A-Za-z]+", re.sub(r"\([^)]*\)", " ", dst))
    english = sum(1 for w in words if w.lower() in ENGLISH_STOPWORDS)
    total_words = max(1, len(dst.split()))
    if dst.strip() == src.strip() and re.search(r"[a-z]{3,}", src):
        flags.append("Chưa được dịch (giống hệt bản gốc)")
    elif english >= 3 and english / total_words > 0.12:
        flags.append("Còn sót nhiều câu tiếng Anh")

    if kind in ("para", "list", "note") and len(src) > 60:
        ratio = len(dst) / len(src)
        if ratio < 0.55:
            flags.append(f"Bản dịch ngắn bất thường ({ratio:.0%} độ dài bản gốc) — có thể bỏ sót ý")
        elif ratio > 2.6:
            flags.append(f"Bản dịch dài bất thường ({ratio:.0%}) — có thể thêm nội dung")

    for ref in FIG_RE.findall(src):
        if ref not in dst:
            flags.append(f"Thiếu tham chiếu hình/bảng {ref}")

    dst_norm = _norm(dst)
    for term in required_terms or []:
        if term.get("status") == "approved" and not term.get("ambiguous"):
            if _norm(term["vi"]) not in dst_norm:
                flags.append(f"Không dùng thuật ngữ chuẩn: {term['en']} → {term['vi']}")
    return flags


def fix_references(text: str) -> str:
    """Sửa các tham chiếu hình/bảng còn sót tiếng Anh."""
    text = re.sub(r"\b(?:Fig\.|Figure)\s+(?=\d)", "Hình ", text)
    text = re.sub(r"\bTable\s+(?=\d)", "Bảng ", text)
    return unicodedata.normalize("NFC", text)
