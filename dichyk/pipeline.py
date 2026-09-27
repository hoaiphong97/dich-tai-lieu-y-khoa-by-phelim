"""Từ các khối đã tách → đoạn cần dịch (kèm ngữ cảnh, thuật ngữ) → bản dịch → Markdown."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import prompts, validate
from .cache import TranslationCache
from .engines import Engine, EngineError
from .extract import Block
from .glossary import Glossary

SEVERE = ("Bản dịch rỗng", "Chưa được dịch", "Còn sót nhiều câu tiếng Anh")
TRANSLATABLE_CELL = re.compile(r"[A-Za-z]{3,}")


class Cancelled(Exception):
    pass


@dataclass
class Segment:
    id: int
    block: int
    kind: str  # heading | para | list | caption | note | cell
    text: str
    page: int
    section: str = ""
    previous: str = ""
    terms: list[dict] = field(default_factory=list)
    dst: str = ""
    flags: list[str] = field(default_factory=list)
    cached: bool = False


# --------------------------------------------------------------------------- #
# Dựng đoạn cần dịch
# --------------------------------------------------------------------------- #

def text_to_blocks(text: str) -> list[Block]:
    """Văn bản dán vào (chế độ dịch nhanh) → khối. Dòng trống ngăn đoạn."""
    blocks: list[Block] = []
    for chunk in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [line.strip() for line in chunk.split("\n") if line.strip()]
        if not lines:
            continue
        if all(re.match(r"^[-•*·]\s+", line) for line in lines):
            blocks.append(Block("list", "\n".join(re.sub(r"^[-•*·]\s+", "", l) for l in lines), 1))
        elif len(lines) == 1 and lines[0].startswith("#"):
            level = len(lines[0]) - len(lines[0].lstrip("#"))
            blocks.append(Block("heading", lines[0].lstrip("#").strip(), 1, level=min(level, 4)))
        else:
            joined = lines[0]
            for line in lines[1:]:
                if joined.endswith("-") and joined[-2:-1].isalpha() and line[:1].islower():
                    joined = joined[:-1] + line
                else:
                    joined += " " + line
            blocks.append(Block("para", joined, 1))
    return blocks


def build_segments(blocks: list[Block], glossary: Glossary | None, bilingual: bool) -> list[Segment]:
    segments: list[Segment] = []
    headings: list[tuple[int, str]] = []
    seen_terms: set[str] = set()
    previous = ""

    def terms_for(text: str, mark_seen: bool) -> list[dict]:
        if glossary is None:
            return []
        found: dict[str, dict] = {}
        for match in glossary.find(text):
            term = match.term
            if term.key in found:
                continue
            found[term.key] = {
                "en": term.en,
                "vi": term.vi,
                "ambiguous": term.ambiguous,
                "note": term.note,
                "status": term.status,
                "first": bilingual and not term.ambiguous and term.key not in seen_terms,
            }
        if mark_seen:
            seen_terms.update(k for k, v in found.items() if not v["ambiguous"])
        return list(found.values())

    for index, block in enumerate(blocks):
        if block.kind == "heading":
            level = block.level or 1
            headings = [h for h in headings if h[0] < level] + [(level, block.text)]
            if level <= 2:
                seen_terms.clear()  # mục mới: thuật ngữ lại được ghi kèm tiếng Anh ở lần đầu
        section = " > ".join(h[1] for h in headings)

        if block.kind == "table":
            done: set[str] = set()
            for row in block.rows:
                for cell in row:
                    if cell and cell not in done and TRANSLATABLE_CELL.search(cell):
                        done.add(cell)
                        segments.append(
                            Segment(len(segments), index, "cell", cell, block.page, section,
                                    terms=terms_for(cell, mark_seen=False))
                        )
            continue

        if block.kind == "note" and block.text.startswith("[Trang "):
            continue  # ghi chú của app (trang scan), không dịch

        segments.append(
            Segment(
                len(segments), index, block.kind, block.text, block.page,
                section=section if block.kind != "heading" else " > ".join(h[1] for h in headings[:-1]),
                previous=previous if block.kind in ("para", "list", "note") else "",
                terms=terms_for(block.text, mark_seen=True),
            )
        )
        if block.kind in ("para", "list"):
            previous = block.text[-600:]
    return segments


# --------------------------------------------------------------------------- #
# Dịch
# --------------------------------------------------------------------------- #

def _prompt(segment: Segment) -> tuple[str, str]:
    user = prompts.build_user(segment.text, segment.kind, segment.section, segment.previous, segment.terms)
    return prompts.SYSTEM, user


def _translate_list(engine: Engine, segment: Segment, first: str) -> str:
    """Danh sách phải giữ đúng số dòng; nếu model gộp/tách dòng thì dịch từng mục."""
    source_lines = segment.text.split("\n")
    lines = [l for l in first.split("\n") if l.strip()]
    if len(lines) == len(source_lines):
        return "\n".join(re.sub(r"^[-•*·]\s*", "", l).strip() for l in lines)
    out = []
    for line in source_lines:
        item = Segment(segment.id, segment.block, "para", line, segment.page, segment.section, terms=segment.terms)
        system, user = _prompt(item)
        out.append(engine.translate(system, user).replace("\n", " "))
    return "\n".join(out)


def translate_one(engine: Engine, cache: TranslationCache | None, segment: Segment) -> Segment:
    system, user = _prompt(segment)
    key = TranslationCache.key(prompts.PROMPT_VERSION, engine.identity, system, user)
    cached = cache.get(key) if cache else None
    if cached is not None:
        segment.dst = cached
        segment.cached = True
    else:
        dst = _finish(engine, segment, engine.translate(system, user))
        flags = validate.check(segment.text, dst, segment.kind, segment.terms)
        if any(f.startswith(SEVERE) for f in flags):
            retry = _finish(engine, segment, engine.translate(system, user, temperature=0.6))
            retry_flags = validate.check(segment.text, retry, segment.kind, segment.terms)
            if len(retry_flags) < len(flags):
                dst = retry
        segment.dst = dst
        if cache:
            cache.set(key, dst)
    segment.flags = validate.check(segment.text, segment.dst, segment.kind, segment.terms)
    return segment


def _finish(engine: Engine, segment: Segment, dst: str) -> str:
    if segment.kind == "list":
        dst = _translate_list(engine, segment, dst)
    elif segment.kind in ("heading", "caption", "cell"):
        dst = " ".join(dst.split())
    return validate.fix_references(dst)


def translate_segments(
    segments: list[Segment],
    engine: Engine,
    cache: TranslationCache | None,
    *,
    workers: int = 1,
    progress=None,
    cancelled=None,
) -> None:
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        pending = {}
        queue = iter(segments)

        def submit_next() -> bool:
            if cancelled and cancelled():
                return False
            try:
                segment = next(queue)
            except StopIteration:
                return False
            pending[pool.submit(translate_one, engine, cache, segment)] = segment
            return True

        for _ in range(max(1, workers) * 2):
            if not submit_next():
                break
        while pending:
            future = next(as_completed(pending))
            segment = pending.pop(future)
            try:
                future.result()
            except EngineError:
                for other in pending:
                    other.cancel()
                raise
            done += 1
            if progress:
                progress(done, len(segments), segment)
            submit_next()
    if cancelled and cancelled():
        raise Cancelled()


# --------------------------------------------------------------------------- #
# Xuất Markdown
# --------------------------------------------------------------------------- #

def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(
    blocks: list[Block],
    segments: list[Segment],
    *,
    title: str,
    source_name: str = "",
    scope: str = "",
    engine_desc: str = "",
    keep_source: bool = False,
    header: bool = True,
) -> str:
    by_block: dict[int, Segment] = {}
    cells: dict[int, dict[str, str]] = {}
    for s in segments:
        if s.kind == "cell":
            cells.setdefault(s.block, {})[s.text] = s.dst or s.text
        else:
            by_block[s.block] = s

    out: list[str] = []
    if header:
        out.append(f"# {title}")
        meta = " · ".join(
            x for x in (
                f"Dịch từ **{source_name}**" if source_name else "",
                scope,
                datetime.now().strftime("%d/%m/%Y"),
                f"Máy dịch: {engine_desc}" if engine_desc else "",
            ) if x
        )
        out.append(f"> {meta}  \n> Bản dịch máy — cần đối chiếu bản gốc trước khi dùng cho mục đích chuyên môn.")

    current_page = None
    for index, block in enumerate(blocks):
        if block.page != current_page and source_name:
            current_page = block.page
            out.append(f"<!-- Trang {block.page} -->")
        if block.kind == "table":
            table_cells = cells.get(index, {})
            rows = [[_escape_cell(table_cells.get(c, c)) for c in row] for row in block.rows]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
            lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
            out.append("\n".join(lines))
            continue
        segment = by_block.get(index)
        if segment is None:
            if block.kind == "note":
                out.append(f"> *{block.text}*")
            continue
        text = segment.dst or segment.text
        if block.kind == "heading":
            out.append("#" * (min(block.level or 1, 3) + 1) + " " + text)
        elif block.kind == "list":
            out.append("\n".join(f"- {line}" for line in text.split("\n")))
        elif block.kind == "caption":
            out.append(f"*{text}*")
        elif block.kind == "note":
            out.append(f"> {text}")
        else:
            out.append(text)
        if keep_source and block.kind != "heading":
            source = segment.text.replace("\n", " · ")
            out.append(f"> *Bản gốc:* {source}")
        if segment.flags:
            out.append(f"<!-- ⚠ Cần kiểm tra: {'; '.join(segment.flags)} -->")
    return "\n\n".join(out).strip() + "\n"


def review_items(segments: list[Segment]) -> list[dict]:
    return [
        {"id": s.id, "page": s.page, "kind": s.kind, "src": s.text, "dst": s.dst, "flags": s.flags}
        for s in segments
        if s.flags
    ]


# --------------------------------------------------------------------------- #
# Chế độ agent: xuất file cho agent (Claude Code…) dịch, rồi ghép lại
# --------------------------------------------------------------------------- #

AGENT_TASK = """# Nhiệm vụ dịch cho agent

Thư mục này chứa một job dịch tài liệu y khoa Anh → Việt, xuất từ app "Dịch Tài Liệu Y Khoa".

## Việc cần làm
1. Đọc `segments.jsonl`. Mỗi dòng là một đoạn cần dịch:
   `{{"id", "kind", "page", "section", "previous", "terms", "src"}}`
2. Dịch `src` sang tiếng Việt theo quy tắc bên dưới. `section` và `previous` chỉ để hiểu ngữ cảnh, không dịch.
3. Ghi kết quả vào `translations.jsonl`, mỗi dòng: `{{"id": <id>, "dst": "<bản dịch>"}}`.
   Có thể ghi dần theo lô (ví dụ 30–50 đoạn một lần) và ghi tiếp vào cuối file.
4. Dịch xong, báo người dùng bấm **"Ghép bản dịch"** trong app. App sẽ kiểm lỗi và xuất file Markdown.

## Quy tắc dịch
{rules}

## Thuật ngữ
- Trường `terms` của mỗi đoạn liệt kê thuật ngữ trong kho. Dùng đúng dạng `vi`.
- Nếu `first` = true: viết dạng song ngữ ở lần này, ví dụ `xương hàm dưới (mandible)`.
- Nếu `ambiguous` = true: chỉ dùng khi đúng nghĩa (xem `note`).
- `kind` = "list": giữ đúng số dòng, mỗi mục một dòng. `kind` = "cell": ô bảng, dịch ngắn gọn.

## Thông tin job
- Tài liệu: {source}
- Phạm vi: {scope}
- Số đoạn: {count}
"""


def export_agent_task(job_dir: Path, segments: list[Segment], *, source: str, scope: str) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "segments.jsonl", "w", encoding="utf-8") as stream:
        for s in segments:
            record = {
                "id": s.id, "kind": s.kind, "page": s.page, "section": s.section,
                "previous": s.previous[-300:], "terms": s.terms, "src": s.text,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    rules = prompts.SYSTEM.split("Rules:", 1)[-1].strip()
    task = AGENT_TASK.format(rules=rules, source=source, scope=scope, count=len(segments))
    path = job_dir / "AGENT_TASK.md"
    path.write_text(task, encoding="utf-8")
    return path


def load_agent_translations(job_dir: Path, segments: list[Segment]) -> int:
    """Nạp translations.jsonl vào segments. Trả về số đoạn còn thiếu."""
    table: dict[int, str] = {}
    path = job_dir / "translations.jsonl"
    if path.is_file():
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    table[int(record["id"])] = str(record["dst"])
                except (ValueError, KeyError, TypeError):
                    continue
    missing = 0
    for s in segments:
        dst = table.get(s.id, "")
        s.dst = validate.fix_references(dst) if dst else ""
        if s.dst:
            s.flags = validate.check(s.text, s.dst, s.kind, s.terms)
        else:
            s.flags = ["Agent chưa dịch đoạn này"]
            missing += 1
    return missing


def save_state(job_dir: Path, blocks: list[Block], segments: list[Segment]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "state.json", "w", encoding="utf-8") as stream:
        json.dump(
            {"blocks": [asdict(b) for b in blocks], "segments": [asdict(s) for s in segments]},
            stream, ensure_ascii=False,
        )


def load_state(job_dir: Path) -> tuple[list[Block], list[Segment]]:
    with open(job_dir / "state.json", encoding="utf-8") as stream:
        data = json.load(stream)
    return [Block(**b) for b in data["blocks"]], [Segment(**s) for s in data["segments"]]
