"""Prompt cho model dịch. Tăng PROMPT_VERSION khi đổi nội dung để cache không trả bản cũ."""

from __future__ import annotations

PROMPT_VERSION = "1"

SYSTEM = """You are an expert medical translator. Translate English medical and scientific text into Vietnamese for a Vietnamese medical textbook.

Rules:
1. Translate faithfully and completely. Never omit, summarize, add explanations, or add content.
2. Use standard Vietnamese medical terminology as used in Vietnamese medical universities, written in natural academic Vietnamese.
3. Keep numbers, units, doses, measurements, laboratory values, gene and protein names, drug names (INN), abbreviations (MRI, CT, ECG, TMJ...), citations and reference numbers exactly as in the source.
4. Figure and table references: "Fig. 1.2" or "Figure 1.2" -> "Hình 1.2", "Table 3" -> "Bảng 3", "Box 2" -> "Khung 2".
5. When a TERMS list is given, use exactly those Vietnamese terms, in exactly the given form.
6. The CONTEXT block is only for understanding. Never translate or repeat it.
7. Output ONLY the Vietnamese translation of the SOURCE block as plain text: no quotes, no labels, no notes, no markdown."""

KIND_HINT = {
    "heading": "This is a section heading. Translate it concisely as a heading.",
    "caption": "This is a figure or table caption.",
    "list": "This is a bulleted list. Keep exactly one output line per input line, in the same order.",
    "cell": "This is a single table cell. Translate it briefly; keep numbers and symbols unchanged.",
    "note": "This is a side note or footnote.",
    "para": "",
}


def build_user(text: str, kind: str, section: str, previous: str, terms: list[dict]) -> str:
    parts = []
    context = []
    if section:
        context.append(f"Section: {section}")
    if previous:
        context.append(f"Previous paragraph: {previous}")
    if context:
        parts.append("<CONTEXT>\n" + "\n".join(context) + "\n</CONTEXT>")
    if terms:
        lines = []
        for t in terms:
            if t.get("ambiguous"):
                note = f" (only if this meaning fits: {t['note']})" if t.get("note") else " (if this meaning fits)"
                lines.append(f"- {t['en']} -> {t['vi']}{note}")
            elif t.get("first"):
                lines.append(f"- {t['en']} -> {t['vi']} ({t['en']})")
            else:
                lines.append(f"- {t['en']} -> {t['vi']}")
        parts.append("<TERMS>\n" + "\n".join(lines) + "\n</TERMS>")
    hint = KIND_HINT.get(kind, "")
    if hint:
        parts.append(hint)
    parts.append("<SOURCE>\n" + text + "\n</SOURCE>")
    return "\n\n".join(parts)
