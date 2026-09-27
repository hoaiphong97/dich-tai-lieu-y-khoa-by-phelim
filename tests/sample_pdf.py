"""Sinh PDF mẫu giống sách giáo khoa y khoa để kiểm thử phần tách chữ.

Có: header/footer lặp lại, số trang, bố cục 2 cột, tiêu đề 2 cấp, từ bị ngắt
gạch nối cuối dòng, đoạn văn vắt qua cột và qua trang, chú thích hình,
danh sách gạch đầu dòng và một bảng có kẻ ô.
"""

from __future__ import annotations

import pymupdf

W, H = 595, 842
LEFT = (50, 290)
RIGHT = (305, 545)
TOP, BOTTOM = 80, 770
BODY = 10
LEAD = 13

PARA_1 = (
    "The temporomandibular joint is one of the most complex joints in the body. "
    "It provides for hinging movement in one plane and therefore can be considered a "
    "ginglymoid joint. However, at the same time it also provides for gliding movements, "
    "which classifies it as an arthrodial joint."
)
PARA_2 = (
    "The mandible is suspended below the maxilla by muscles, ligaments, and other soft "
    "tissues, which provide the mobility necessary to function with the maxilla. The "
    "alveolar process and the teeth are supported by the periodontal ligament, which "
    "absorbs occlusal forces of approximately 18 to 23 mm of displacement during function "
    "and distributes them to the alveolar bone over the entire root surface of each tooth "
    "in the dental arch while chewing continues for several minutes."
)


class Writer:
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.page = None
        self.col = LEFT
        self.y = TOP
        self.new_page()

    def new_page(self):
        self.page = self.doc.new_page(width=W, height=H)
        n = self.doc.page_count
        self.page.insert_text((50, 40), "Management of Temporomandibular Disorders", fontsize=8)
        self.page.insert_text((W / 2 - 5, 815), str(n), fontsize=8)
        self.col, self.y = LEFT, TOP

    def next_column(self):
        if self.col == LEFT:
            self.col, self.y = RIGHT, TOP
        else:
            self.new_page()

    def line(self, text: str, size=BODY, font="helv"):
        if self.y + LEAD > BOTTOM:
            self.next_column()
        self.page.insert_text((self.col[0], self.y), text, fontsize=size, fontname=font)
        self.y += size * 1.3

    def paragraph(self, text: str, hyphenate_at: str | None = None):
        width = self.col[1] - self.col[0]
        current = ""
        for word in text.split():
            trial = f"{current} {word}".strip()
            if pymupdf.get_text_length(trial, fontsize=BODY) > width:
                if hyphenate_at and word.startswith(hyphenate_at) and len(word) > len(hyphenate_at):
                    self.line(f"{current} {hyphenate_at}-")
                    current = word[len(hyphenate_at):]
                    hyphenate_at = None
                    continue
                self.line(current)
                current = word
            else:
                current = trial
        if current:
            self.line(current)
        self.y += 8

    def heading(self, text: str, size: float):
        self.y += 6
        self.line(text, size=size, font="hebo")
        self.y += 4


def build(path: str) -> str:
    doc = pymupdf.open()
    w = Writer(doc)
    w.heading("Functional Anatomy", 18)
    w.heading("The Masticatory System", 13)
    w.paragraph(PARA_1, hyphenate_at="gingly")
    w.line("• The maxilla forms the upper jaw.")
    w.line("• The mandible forms the lower jaw.")
    w.y += 8
    w.paragraph(" ".join([PARA_2] * 3))
    w.line("Fig. 1.1 Lateral view of the skull.", size=8)
    w.y += 10
    w.heading("Dentition and Supportive Structures", 13)
    for _ in range(6):
        w.paragraph(PARA_2)
    # Trang có bảng
    w.new_page()
    w.heading("Occlusal Forces", 13)
    w.paragraph("Table 1.1 summarizes the forces measured in adults.")
    rows = [["Tooth", "Force (N)", "Area"], ["Incisor", "150", "Anterior"], ["Molar", "600", "Posterior"]]
    x0, y0, cw, rh = 60, w.y + 10, 150, 20
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            w.page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            w.page.insert_text((rect.x0 + 4, rect.y1 - 6), cell, fontsize=BODY)
    w.y = y0 + len(rows) * rh + 20
    w.col = (50, 545)
    w.paragraph("These values vary considerably between individuals and depend on muscle activity.")
    doc.set_toc([[1, "Chapter 1 Functional Anatomy", 1], [1, "Occlusal Forces", doc.page_count]])
    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    import sys

    print(build(sys.argv[1] if len(sys.argv) > 1 else "sample.pdf"))
