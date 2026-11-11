"""Fast local PDF export for Shenlun questions and full papers.

The exporter is intentionally deterministic and network-free.  ReportLab is
loaded lazily so the rest of the app can still import on development machines
where the optional PDF dependency has not been installed yet.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

SONGTI_TTC = Path("/System/Library/Fonts/Supplemental/Songti.ttc")
SONGTI_REGULAR_INDEX = 6
SONGTI_BOLD_INDEX = 1
BODY_FONT_SIZE = 12  # 小四
GRID_COLUMNS = 25
GRID_CELL_SIZE_MM = 7.0  # 接近真实申论答题卡，保证可手写
GRID_CELL_SIZE = GRID_CELL_SIZE_MM * 72 / 25.4
MAX_GRID_CELLS = 5000
LINE_WRAP_SAFETY = 6


def safe_pdf_filename(value: str, fallback: str = "研申试题") -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", str(value or "")).strip().strip(".")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback
    return f"{text[:90]}.pdf"


def answer_grid_cell_count(question: dict) -> int:
    """Return the answer-grid size implied by the question's word requirement.

    The explicit word-limit field is authoritative.  If an imported question
    has no numeric word_limit, requirements are used as a fallback.  A small
    type-based fallback keeps incomplete legacy questions exportable.
    """
    word_limit = str(question.get("word_limit") or "")
    requirements = str(question.get("requirements") or "")
    primary = [int(value) for value in re.findall(r"(?<!\d)(\d{2,4})(?=\s*字)", word_limit)]
    if not primary:
        primary = [int(value) for value in re.findall(r"\d{2,4}", word_limit)]
    if not primary:
        primary = [int(value) for value in re.findall(r"(?<!\d)(\d{2,4})(?=\s*字)", requirements)]
    if primary:
        return max(25, min(MAX_GRID_CELLS, max(primary)))

    question_type = str(question.get("question_type") or "")
    if question_type == "综合写作":
        return 1200
    if question_type == "公文写作":
        return 500
    return 300


def _reportlab():
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - exercised in packaged runtime
        raise RuntimeError("PDF 导出组件未安装，请安装 reportlab 后重试。") from exc
    return {
        "colors": colors,
        "TA_CENTER": TA_CENTER,
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "mm": mm,
        "pdfmetrics": pdfmetrics,
        "UnicodeCIDFont": UnicodeCIDFont,
        "TTFont": TTFont,
        "Flowable": Flowable,
        "PageBreak": PageBreak,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


def _register_songti(rl):
    pdfmetrics = rl["pdfmetrics"]
    regular_name = "YanShenSongtiSC"
    bold_name = "YanShenSongtiSCBold"
    try:
        pdfmetrics.getFont(regular_name)
        pdfmetrics.getFont(bold_name)
        return regular_name, bold_name
    except KeyError:
        pass

    if SONGTI_TTC.exists():
        try:
            pdfmetrics.registerFont(
                rl["TTFont"](
                    regular_name,
                    str(SONGTI_TTC),
                    subfontIndex=SONGTI_REGULAR_INDEX,
                )
            )
            pdfmetrics.registerFont(
                rl["TTFont"](
                    bold_name,
                    str(SONGTI_TTC),
                    subfontIndex=SONGTI_BOLD_INDEX,
                )
            )
            return regular_name, bold_name
        except Exception:
            # Some stripped-down ReportLab builds cannot subset TTC files.
            pass

    fallback = "STSong-Light"
    try:
        pdfmetrics.getFont(fallback)
    except KeyError:
        pdfmetrics.registerFont(rl["UnicodeCIDFont"](fallback))
    return fallback, fallback


def _styles(rl, regular_font, bold_font):
    ParagraphStyle = rl["ParagraphStyle"]
    center = rl["TA_CENTER"]
    return {
        "paper_title": ParagraphStyle(
            "paper_title",
            fontName=bold_font,
            fontSize=16,
            leading=24,
            alignment=center,
            spaceAfter=14,
            wordWrap="CJK",
        ),
        "section": ParagraphStyle(
            "section",
            fontName=bold_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            spaceBefore=8,
            spaceAfter=6,
            keepWithNext=1,
            wordWrap="CJK",
        ),
        "material_label": ParagraphStyle(
            "material_label",
            fontName=bold_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=1,
            wordWrap="CJK",
        ),
        "body_first_line": ParagraphStyle(
            "body_first_line",
            fontName=regular_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            leftIndent=BODY_FONT_SIZE * 2,
            spaceAfter=0,
            wordWrap="LTR",
        ),
        "body_line": ParagraphStyle(
            "body_line",
            fontName=regular_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            spaceAfter=0,
            wordWrap="LTR",
        ),
        "question_line": ParagraphStyle(
            "question_line",
            fontName=regular_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            spaceAfter=0,
            wordWrap="LTR",
        ),
        "grid_label": ParagraphStyle(
            "grid_label",
            fontName=regular_font,
            fontSize=BODY_FONT_SIZE,
            leading=20,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=1,
            wordWrap="CJK",
        ),
    }


def _plain_paragraphs(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]


_NO_LINE_START = set("，。！？；：、）】》〉」』”’％‰℃…")
_NO_LINE_END = set("（【《〈「『“‘")


def _kinsoku_markup(text: str) -> str:
    """Keep Chinese punctuation away from illegal line starts/ends."""
    chars = list(str(text or ""))
    if not chars:
        return ""
    bonds = [False] * max(0, len(chars) - 1)
    for index in range(len(chars) - 1):
        if chars[index] in _NO_LINE_END or chars[index + 1] in _NO_LINE_START:
            bonds[index] = True
    parts = []
    start = 0
    for index, bonded in enumerate(bonds):
        if bonded:
            continue
        chunk = "".join(chars[start : index + 1])
        parts.append((chunk, index + 1 - start > 1))
        start = index + 1
    chunk = "".join(chars[start:])
    parts.append((chunk, len(chars) - start > 1))
    return "".join(
        f"<nobr>{escape(chunk)}</nobr>" if no_break else escape(chunk)
        for chunk, no_break in parts
        if chunk
    )


def _paragraph(text: str, style, rl, preserve_lines: bool = False):
    raw = str(text or "")
    if preserve_lines:
        content = "<br/>".join(_kinsoku_markup(line) for line in raw.split("\n"))
    else:
        content = _kinsoku_markup(raw)
    return rl["Paragraph"](content or "　", style)


def _wrap_cjk_segment(text: str, font_name: str, font_size: float, width: float, first_indent: float, rl):
    """Wrap one logical line with basic Chinese kinsoku rules."""
    value = str(text or "")
    if not value:
        return [""]
    string_width = rl["pdfmetrics"].stringWidth
    lines = []
    start = 0
    first_line = True
    while start < len(value):
        limit = max(24.0, width - (first_indent if first_line else 0) - LINE_WRAP_SAFETY)
        used = 0.0
        cursor = start
        while cursor < len(value):
            char_width = string_width(value[cursor], font_name, font_size)
            if cursor > start and used + char_width > limit:
                break
            used += char_width
            cursor += 1
        if cursor >= len(value):
            lines.append(value[start:])
            break

        break_at = cursor
        # A line must not begin with closing punctuation.  Move the preceding
        # character together with the punctuation to the next line instead of
        # letting punctuation hang at the left edge.
        while break_at > start and break_at < len(value) and value[break_at] in _NO_LINE_START:
            break_at -= 1
        # Opening punctuation should travel with the following character.
        while break_at > start and value[break_at - 1] in _NO_LINE_END:
            break_at -= 1

        # Avoid splitting ordinary ASCII words/numbers when there is a usable
        # earlier break point.  Chinese text remains character-wrapped.
        if (
            break_at > start
            and break_at < len(value)
            and value[break_at - 1].isascii()
            and value[break_at - 1].isalnum()
            and value[break_at].isascii()
            and value[break_at].isalnum()
        ):
            token_start = break_at - 1
            while (
                token_start > start
                and value[token_start - 1].isascii()
                and value[token_start - 1].isalnum()
            ):
                token_start -= 1
            if token_start > start:
                break_at = token_start

        if break_at <= start:
            break_at = cursor
        lines.append(value[start:break_at])
        start = break_at
        first_line = False
    return lines


def _text_line_flowable(text: str, style, rl):
    """Draw one already-wrapped line without letting Paragraph reflow it."""
    Flowable = rl["Flowable"]

    class TextLine(Flowable):
        def __init__(self, value, line_style):
            super().__init__()
            self.value = value
            self.line_style = line_style
            self.height = float(line_style.leading)

        def wrap(self, avail_width, avail_height):
            self.width = avail_width
            return avail_width, self.height

        def draw(self):
            canvas = self.canv
            canvas.saveState()
            canvas.setFont(self.line_style.fontName, self.line_style.fontSize)
            canvas.setFillColor(rl["colors"].black)
            baseline = max(1.0, (self.height - self.line_style.fontSize) / 2.0 + 1.0)
            canvas.drawString(float(getattr(self.line_style, "leftIndent", 0) or 0), baseline, self.value)
            canvas.restoreState()

    return TextLine(str(text or ""), style)


def _wrapped_text_flowables(
    text: str,
    first_style,
    rest_style,
    rl,
    available_width: float,
    *,
    first_indent: float = 0,
    space_after: float = 6,
):
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    segments = raw.split("\n")
    physical_lines = []
    first_physical_line = True
    for segment in segments:
        if segment == "":
            physical_lines.append(("", first_physical_line))
            first_physical_line = False
            continue
        wrapped = _wrap_cjk_segment(
            segment,
            rest_style.fontName,
            rest_style.fontSize,
            available_width,
            first_indent if first_physical_line else 0,
            rl,
        )
        for line in wrapped:
            physical_lines.append((line, first_physical_line))
            first_physical_line = False

    flowables = []
    for line, is_first in physical_lines:
        if not line:
            flowables.append(rl["Spacer"](1, rest_style.leading))
            continue
        style = first_style if is_first else rest_style
        flowables.append(_text_line_flowable(line, style, rl))
    if space_after:
        flowables.append(rl["Spacer"](1, space_after))
    return flowables


def _material_story(materials, styles, rl, available_width):
    story = [_paragraph("给定资料", styles["section"], rl)]
    for material in materials:
        number = material.get("material_number")
        title = str(material.get("title") or "").strip()
        label = title or (f"资料{number}" if number not in {None, ""} else "资料")
        story.append(_paragraph(label, styles["material_label"], rl))
        paragraphs = _plain_paragraphs(material.get("content") or "")
        if not paragraphs:
            paragraphs = [""]
        for paragraph in paragraphs:
            story.extend(
                _wrapped_text_flowables(
                    paragraph,
                    styles["body_first_line"],
                    styles["body_line"],
                    rl,
                    available_width,
                    first_indent=BODY_FONT_SIZE * 2,
                    space_after=6,
                )
            )
    return story


def _answer_grid_story(cell_count: int, styles, rl):
    Table = rl["Table"]
    TableStyle = rl["TableStyle"]
    colors = rl["colors"]
    cells = max(25, int(cell_count or 0))
    full_rows, remainder = divmod(cells, GRID_COLUMNS)
    flowables = [
        _paragraph(
            f"答题区（{cells}格，每行{GRID_COLUMNS}格）",
            styles["grid_label"],
            rl,
        )
    ]
    grid_style = TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#777777")),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
    )
    if full_rows:
        table = Table(
            [[""] * GRID_COLUMNS for _ in range(full_rows)],
            colWidths=[GRID_CELL_SIZE] * GRID_COLUMNS,
            rowHeights=[GRID_CELL_SIZE] * full_rows,
            hAlign="CENTER",
            splitByRow=1,
        )
        table.setStyle(grid_style)
        flowables.append(table)
    if remainder:
        partial = Table(
            [[""] * remainder],
            colWidths=[GRID_CELL_SIZE] * remainder,
            rowHeights=[GRID_CELL_SIZE],
            hAlign="LEFT",
        )
        partial.setStyle(grid_style)
        outer = Table(
            [[partial, ""]],
            colWidths=[GRID_CELL_SIZE * remainder, GRID_CELL_SIZE * (GRID_COLUMNS - remainder)],
            rowHeights=[GRID_CELL_SIZE],
            hAlign="CENTER",
        )
        outer.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        flowables.append(outer)
    return flowables


def _question_story(question: dict, index: int, styles, rl, available_width):
    number = question.get("question_number") or index
    title = f"第{number}题"
    story = [_paragraph(title, styles["section"], rl)]
    prompt = str(question.get("prompt") or question.get("title") or "").strip()
    if prompt:
        story.extend(
            _wrapped_text_flowables(
                prompt,
                styles["question_line"],
                styles["question_line"],
                rl,
                available_width,
                first_indent=0,
                space_after=6,
            )
        )
    requirements = str(question.get("requirements") or "").strip()
    if requirements:
        story.append(_paragraph("作答要求", styles["material_label"], rl))
        story.extend(
            _wrapped_text_flowables(
                requirements,
                styles["question_line"],
                styles["question_line"],
                rl,
                available_width,
                first_indent=0,
                space_after=6,
            )
        )
    story.extend(_answer_grid_story(answer_grid_cell_count(question), styles, rl))
    return story


def _build_pdf(title: str, materials: list[dict], questions: list[dict]) -> bytes:
    rl = _reportlab()
    regular_font, bold_font = _register_songti(rl)
    styles = _styles(rl, regular_font, bold_font)
    buffer = BytesIO()
    mm = rl["mm"]
    page_width = rl["A4"][0]
    document = rl["SimpleDocTemplate"](
        buffer,
        pagesize=rl["A4"],
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="研申",
        subject="申论试题导出",
    )

    story = [_paragraph(title, styles["paper_title"], rl)]
    story.extend(_material_story(materials, styles, rl, document.width))
    if materials and questions:
        story.append(rl["Spacer"](1, 8))
    for index, question in enumerate(questions, start=1):
        if index > 1:
            story.append(rl["Spacer"](1, 12))
        story.extend(_question_story(question, index, styles, rl, document.width))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setTitle(title)
        canvas.setAuthor("研申")
        canvas.setFont(regular_font, 9)
        canvas.setFillColor(rl["colors"].HexColor("#666666"))
        canvas.drawCentredString(page_width / 2, 8 * mm, f"- {doc.page} -")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def build_question_pdf(question: dict, materials: list[dict]) -> bytes:
    title = str(question.get("paper_name") or question.get("title") or "申论单题")
    question_number = question.get("question_number")
    if question_number:
        title = f"{title} - 第{question_number}题"
    return _build_pdf(title, materials, [question])


def build_paper_pdf(paper: dict, materials: list[dict], questions: list[dict]) -> bytes:
    title = str(paper.get("paper_name") or "申论试卷")
    return _build_pdf(title, materials, questions)
