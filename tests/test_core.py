from __future__ import annotations

import json
import re

import pytest

from dichyk import extract, pipeline, validate
from dichyk.cache import TranslationCache
from dichyk.engines import Engine, clean_output
from dichyk.glossary import Glossary
from tests.sample_pdf import build


class FakeEngine(Engine):
    """Giả lập model: thay mọi từ tiếng Anh bằng 'từ', giữ số, thêm thuật ngữ được yêu cầu."""

    name = "fake"

    def __init__(self):
        super().__init__("http://fake", "fake-model")
        self.calls = 0

    def _chat(self, system, user, temperature):
        self.calls += 1
        source = re.search(r"<SOURCE>\n(.*)\n</SOURCE>", user, re.DOTALL).group(1)
        terms = re.findall(r"^- .+? -> (.+)$", user, re.MULTILINE)
        lines = [re.sub(r"[A-Za-z]+", "từ", line) for line in source.split("\n")]
        if terms:
            lines[0] += " " + ", ".join(terms)
        return "\n".join(lines)


@pytest.fixture()
def sample(tmp_path):
    return build(str(tmp_path / "sample.pdf"))


@pytest.fixture()
def glossary(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "t.tsv").write_text(
        "en\tvi\tvariants\tabbr\tdomain\tambiguous\tstatus\tnote\n"
        "mandible\txương hàm dưới\t\t\tanatomy\t0\tapproved\t\n"
        "temporomandibular joint\tkhớp thái dương hàm\t\tTMJ\tanatomy\t0\tapproved\t\n"
        "joint\tkhớp\t\t\tanatomy\t0\tdraft\t\n"
        "vertebra\tđốt sống\t\t\tanatomy\t0\tdraft\t\n"
        "occlusion\tkhớp cắn\t\t\tdental\t1\tdraft\tMạch máu: tắc\n",
        encoding="utf-8",
    )
    return Glossary(core_dir=core, user_file=tmp_path / "user.tsv")


# ------------------------------------------------------------------ extract
def test_page_range_parsing():
    assert extract.parse_page_range("", 5) == [1, 2, 3, 4, 5]
    assert extract.parse_page_range("1-3, 5", 10) == [1, 2, 3, 5]
    assert extract.parse_page_range("4-2", 10) == [2, 3, 4]
    assert extract.parse_page_range("8-20", 10) == [8, 9, 10]
    with pytest.raises(ValueError):
        extract.parse_page_range("abc", 10)
    with pytest.raises(ValueError):
        extract.parse_page_range("50-60", 10)


def test_extract_structure(sample):
    info = extract.pdf_info(sample)
    assert info.pages == 3
    assert info.toc[0]["start"] == 1 and info.toc[0]["end"] == 2
    blocks = extract.extract_blocks(sample, [1, 2, 3])
    kinds = [b.kind for b in blocks]
    texts = " ".join(b.text for b in blocks)
    assert blocks[0].kind == "heading" and blocks[0].level == 1
    assert "ginglymoid" in texts  # từ bị ngắt gạch nối đã được nối lại
    assert "Management of Temporomandibular" not in texts  # header lặp lại bị bỏ
    assert "list" in kinds and "caption" in kinds and "table" in kinds
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows[0] == ["Tooth", "Force (N)", "Area"]
    # đoạn vắt qua cột/trang được nối thành một đoạn, kết thúc bằng dấu chấm
    for block in blocks:
        if block.kind == "para":
            assert block.text.rstrip().endswith("."), block.text[-60:]


def test_extract_single_page(sample):
    blocks = extract.extract_blocks(sample, [3])
    assert blocks[0].kind == "heading" and blocks[0].text == "Occlusal Forces"
    assert all(b.page == 3 for b in blocks)


# ------------------------------------------------------------------ glossary
def test_glossary_longest_match_plural_and_abbr(glossary):
    found = glossary.find("The temporomandibular joint (TMJ) connects both mandibles; each joint moves.")
    names = [(m.term.en, m.surface) for m in found]
    assert ("temporomandibular joint", "temporomandibular joint") in names
    assert ("temporomandibular joint", "TMJ") in names
    assert ("mandible", "mandibles") in names
    assert ("joint", "joint") in names
    assert not glossary.find("the tmj is lowercase")  # viết tắt phân biệt hoa/thường
    assert glossary.find("two vertebrae")[0].term.en == "vertebra"


def test_glossary_user_override(glossary):
    glossary.upsert({"en": "Mandible", "vi": "hàm dưới", "status": "approved"})
    assert glossary.terms["mandible"].vi == "hàm dưới"
    assert glossary.terms["mandible"].source == "user"
    assert glossary.user_file.is_file()


# ------------------------------------------------------------------ validate
def test_validate_flags():
    assert validate.check("Dose is 2.5 mg daily for 10 days.", "Liều 2,5 mg mỗi ngày trong 10 ngày.", "para") == []
    flags = validate.check("Measured 18 to 23 mm in adults over time.", "Đo được ở người lớn theo thời gian.", "para")
    assert any("Thiếu số liệu" in f for f in flags)
    src = "The mandible is the largest bone of the face and it is very strong."
    assert any("Chưa được dịch" in f for f in validate.check(src, src, "para"))
    terms = [{"en": "mandible", "vi": "xương hàm dưới", "status": "approved", "ambiguous": False}]
    assert any("thuật ngữ" in f for f in validate.check("The mandible.", "Hàm dưới.", "para", terms))
    assert validate.fix_references("Xem Fig. 1.2 và Table 3") == "Xem Hình 1.2 và Bảng 3"


def test_clean_output():
    assert clean_output("<think>hmm</think>\nBản dịch: Xin chào") == "Xin chào"
    assert clean_output('"Xin chào"') == "Xin chào"
    assert clean_output("<SOURCE>\nXin chào\n</SOURCE>") == "Xin chào"


# ------------------------------------------------------------------ pipeline
def test_segments_first_mention_and_section_reset(glossary):
    blocks = [
        extract.Block("heading", "Anatomy", 1, level=1),
        extract.Block("para", "The mandible is a bone.", 1),
        extract.Block("para", "The mandible moves.", 1),
        extract.Block("heading", "Function", 2, level=2),
        extract.Block("para", "The mandible again. Occlusion matters.", 2),
    ]
    segs = pipeline.build_segments(blocks, glossary, bilingual=True)
    firsts = [[t["first"] for t in s.terms if t["en"] == "mandible"] for s in segs]
    assert firsts[1] == [True] and firsts[2] == [False] and firsts[4] == [True]
    occl = next(t for t in segs[4].terms if t["en"] == "occlusion")
    assert occl["ambiguous"] and not occl["first"]
    assert segs[2].previous == "The mandible is a bone."
    assert segs[4].section == "Anatomy > Function"


def test_full_pipeline_with_cache(sample, glossary, tmp_path):
    blocks = extract.extract_blocks(sample, [1, 2, 3])
    segments = pipeline.build_segments(blocks, glossary, bilingual=True)
    engine = FakeEngine()
    cache = TranslationCache(tmp_path / "cache.db")
    seen = []
    pipeline.translate_segments(segments, engine, cache, workers=2, progress=lambda d, t, s: seen.append(d))
    assert seen[-1] == len(segments)
    assert all(s.dst for s in segments)
    md = pipeline.render_markdown(blocks, segments, title="Mẫu", source_name="sample.pdf", scope="Toàn bộ")
    assert md.startswith("# Mẫu")
    assert "## " in md and "| --- |" in md and "- " in md
    assert "<!-- Trang 3 -->" in md
    calls = engine.calls
    # chạy lại: lấy hết từ cache, không gọi model
    segments2 = pipeline.build_segments(blocks, glossary, bilingual=True)
    pipeline.translate_segments(segments2, engine, cache, workers=1)
    assert engine.calls == calls
    assert all(s.cached for s in segments2)


def test_list_keeps_line_count(glossary):
    blocks = [extract.Block("list", "The maxilla forms the upper jaw.\nThe mandible forms the lower jaw.", 1)]
    segs = pipeline.build_segments(blocks, glossary, bilingual=False)
    pipeline.translate_segments(segs, FakeEngine(), None)
    assert len(segs[0].dst.split("\n")) == 2


def test_text_to_blocks():
    blocks = pipeline.text_to_blocks("# Title\n\nFirst para line one\nline two.\n\n- a item\n- b item")
    assert [b.kind for b in blocks] == ["heading", "para", "list"]
    assert blocks[1].text == "First para line one line two."


def test_agent_roundtrip(glossary, tmp_path):
    blocks = pipeline.text_to_blocks("The mandible is a bone.\n\nIt has 2 condyles.")
    segs = pipeline.build_segments(blocks, glossary, bilingual=True)
    job_dir = tmp_path / "job"
    pipeline.save_state(job_dir, blocks, segs)
    pipeline.export_agent_task(job_dir, segs, source="x", scope="y")
    lines = (job_dir / "segments.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["terms"][0]["first"] is True
    (job_dir / "translations.jsonl").write_text(
        json.dumps({"id": 0, "dst": "Xương hàm dưới (mandible) là một xương."}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    blocks2, segs2 = pipeline.load_state(job_dir)
    missing = pipeline.load_agent_translations(job_dir, segs2)
    assert missing == 1
    assert segs2[0].flags == []
    md = pipeline.render_markdown(blocks2, segs2, title="t", header=False)
    assert "Xương hàm dưới (mandible)" in md and "It has 2 condyles." in md
