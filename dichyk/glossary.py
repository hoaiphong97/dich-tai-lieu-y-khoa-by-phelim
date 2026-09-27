"""Kho thuật ngữ Anh → Việt và bộ khớp thuật ngữ trong câu.

Nguồn:
- kit/termbase/*.tsv  : bộ thuật ngữ đi kèm app (chỉ đọc khi đã cài)
- <dữ liệu>/termbase/user.tsv : thuật ngữ người dùng thêm/sửa, ghi đè bộ đi kèm

Cột TSV: en, vi, variants (ngăn bằng |), abbr, domain, ambiguous (0/1),
status (approved/draft), note.
"""

from __future__ import annotations

import csv
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths

COLUMNS = ["en", "vi", "variants", "abbr", "domain", "ambiguous", "status", "note"]


@dataclass
class Term:
    en: str
    vi: str
    variants: str = ""
    abbr: str = ""
    domain: str = ""
    ambiguous: bool = False
    status: str = "draft"
    note: str = ""
    source: str = "core"  # core | user

    @property
    def key(self) -> str:
        return self.en.lower()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Match:
    term: Term
    start: int
    end: int
    surface: str


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _plural_forms(word: str) -> set[str]:
    """Các dạng số nhiều thường gặp trong thuật ngữ y khoa (Latin/Hy Lạp)."""
    forms = {word + "s"}
    if re.search(r"(s|x|z|ch|sh)$", word):
        forms.add(word + "es")
    if re.search(r"[^aeiou]y$", word):
        forms.add(word[:-1] + "ies")
    for singular, plural in (("us", "i"), ("um", "a"), ("on", "a"), ("is", "es"), ("a", "ae"), ("ex", "ices"), ("ix", "ices")):
        if word.endswith(singular) and len(word) > len(singular) + 2:
            forms.add(word[: -len(singular)] + plural)
    return forms


def surface_forms(term: Term) -> set[str]:
    base = {term.en}
    base.update(v.strip() for v in term.variants.split("|") if v.strip())
    forms = set()
    for phrase in base:
        forms.add(phrase)
        words = phrase.split()
        if words and words[-1].isalpha() and words[-1].islower():
            for plural in _plural_forms(words[-1]):
                forms.add(" ".join(words[:-1] + [plural]))
    if term.abbr and len(term.abbr) >= 2:
        forms.add(term.abbr)
    return forms


class Glossary:
    def __init__(self, core_dir: Path | None = None, user_file: Path | None = None):
        self.core_dir = core_dir or paths.kit_dir() / "termbase"
        self.user_file = user_file or paths.sub("termbase") / "user.tsv"
        self._lock = threading.Lock()
        self.reload()

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        terms: dict[str, Term] = {}
        if self.core_dir.is_dir():
            for file in sorted(self.core_dir.glob("*.tsv")):
                for term in _read_tsv(file, source="core"):
                    terms[term.key] = term
        if self.user_file.is_file():
            for term in _read_tsv(self.user_file, source="user"):
                terms[term.key] = term
        self.terms = terms
        self._build_matcher()

    def _build_matcher(self) -> None:
        lookup: dict[str, Term] = {}
        for term in self.terms.values():
            for form in surface_forms(term):
                # Viết tắt khớp phân biệt hoa/thường, còn lại không phân biệt.
                key = form if form == term.abbr else form.lower()
                lookup.setdefault(key, term)
        self._lookup = lookup
        phrases = sorted(lookup, key=len, reverse=True)
        if phrases:
            body = "|".join(re.escape(p) for p in phrases)
            self._regex = re.compile(rf"(?<![\w-])(?:{body})(?![\w-])", re.IGNORECASE)
        else:
            self._regex = None

    # ------------------------------------------------------------------ #
    def find(self, text: str) -> list[Match]:
        """Các thuật ngữ xuất hiện trong câu, ưu tiên cụm dài nhất, không chồng lấn."""
        if not self._regex:
            return []
        out = []
        for m in self._regex.finditer(text):
            surface = m.group(0)
            # Viết tắt chỉ có trong bảng tra ở dạng đúng hoa/thường, nên "ct" không khớp "CT".
            term = self._lookup.get(surface) or self._lookup.get(surface.lower())
            if term is not None:
                out.append(Match(term, m.start(), m.end(), surface))
        return out

    def search(self, query: str = "", limit: int = 500) -> list[dict]:
        q = query.strip().lower()
        items = sorted(self.terms.values(), key=lambda t: t.en.lower())
        if q:
            items = [t for t in items if q in t.en.lower() or q in t.vi.lower() or q in t.abbr.lower()]
        return [t.to_dict() for t in items[:limit]]

    def upsert(self, data: dict) -> Term:
        en = (data.get("en") or "").strip()
        vi = nfc((data.get("vi") or "").strip())
        if not en or not vi:
            raise ValueError("Cần nhập cả thuật ngữ tiếng Anh và tiếng Việt")
        term = Term(
            en=en,
            vi=vi,
            variants=(data.get("variants") or "").strip(),
            abbr=(data.get("abbr") or "").strip(),
            domain=(data.get("domain") or "").strip(),
            ambiguous=str(data.get("ambiguous", "")).lower() in {"1", "true", "yes", "on"},
            status="approved" if data.get("status") == "approved" else "draft",
            note=(data.get("note") or "").strip(),
            source="user",
        )
        with self._lock:
            user_terms = {t.key: t for t in _read_tsv(self.user_file, "user")} if self.user_file.is_file() else {}
            user_terms[term.key] = term
            _write_tsv(self.user_file, user_terms.values())
            self.reload()
        return term


def _read_tsv(path: Path, source: str) -> list[Term]:
    terms = []
    with open(path, encoding="utf-8", newline="") as stream:
        rows = (line for line in stream if line.strip() and not line.startswith("#"))
        for row in csv.DictReader(rows, delimiter="\t"):
            en = (row.get("en") or "").strip()
            vi = nfc((row.get("vi") or "").strip())
            if not en or not vi:
                continue
            terms.append(
                Term(
                    en=en,
                    vi=vi,
                    variants=(row.get("variants") or "").strip(),
                    abbr=(row.get("abbr") or "").strip(),
                    domain=(row.get("domain") or "").strip(),
                    ambiguous=(row.get("ambiguous") or "0").strip() in {"1", "true", "yes"},
                    status=(row.get("status") or "draft").strip() or "draft",
                    note=(row.get("note") or "").strip(),
                    source=source,
                )
            )
    return terms


def _write_tsv(path: Path, terms) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(COLUMNS)
        for t in sorted(terms, key=lambda t: t.en.lower()):
            writer.writerow([t.en, t.vi, t.variants, t.abbr, t.domain, int(t.ambiguous), t.status, t.note])
    tmp.replace(path)
