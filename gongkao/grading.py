import json
import re
from difflib import SequenceMatcher

from .timeutils import format_beijing_time

ANSWER_GRID_COLUMNS = 25


def revised_answer_overwrites_original(original_text, revised_text):
    """Detect a rewrite that cannot honestly be shown as local edits to the answer."""
    original = str(original_text or "").strip()
    revised = str(revised_text or "").strip()
    if len(original) < 40 or not revised:
        return False
    deleted = sum(
        i2 - i1
        for tag, i1, i2, _, _ in SequenceMatcher(
            None, original, revised, autojunk=False
        ).get_opcodes()
        if tag in {"delete", "replace"}
    )
    return deleted / len(original) > 0.45


def build_revised_answer_local_edit_prompt(original_text, revised_text, suggestions, word_limit=""):
    """Ask for a genuinely local revision when the first draft rewrote the answer."""
    suggestions_text = "\n".join(f"- {item}" for item in (suggestions or []))
    budget = word_limit_budget(word_limit)
    hard_max = budget["hard_max_exclusive"]
    target = budget["suggested_max"]
    length_guidance = (
        f"按25格答题纸计算，原答案占用 {count_cjk_chars(original_text)} 格（含换行留空）；"
        f"修改稿目标不超过 {target} 字，最多可写 {hard_max - 1} 字。"
        "补充内容前先压缩原文中的重复或次要表述，逐句核算，手动换行留下的格子也计入字数限制。"
        if hard_max and target
        else f"字数要求：{word_limit or '遵守原题要求'}。"
    )
    return "\n".join(
        [
            "上一版修改稿改写了大部分考生原文，不能作为原答案上的逐处批改。请只返修修改版答案。",
            "以考生原答案为底稿，保留原有句子、编号和顺序；只在对应原句内修改错误、压缩冗余，并就近补充关键遗漏。",
            "至少保留原答案七成文字的原有顺序，不要另写一份范文。不得编造材料事实。",
            length_guidance,
            "只输出 <revised_answer> 标签包裹的干净答案正文，不要输出解释、删除线或字数声明。",
            "",
            "考生原答案：",
            str(original_text or "").strip(),
            "",
            "批改建议：",
            suggestions_text or "仅修正明确的问题。",
            "",
            "上一版改写稿（只用于识别需要补充的内容，不要照其结构重写）：",
            str(revised_text or "").strip(),
        ]
    )


def limited_reference_guidance(reference_count):
    count = max(0, int(reference_count or 0))
    if count > 3:
        return ""
    sample_description = "未提供机构答案" if count == 0 else f"仅有 {count} 份机构答案"
    guidance = (
        f"机构参考答案样本提示：本题{sample_description}，样本不足，不能把少量答案的交集当作唯一标准。"
        "可以结合现有机构答案、题干任务和材料原文自行分析可能的采分点、结构与表达；"
        "自行补充的判断必须能由材料直接支持，不得凭常识扩展或虚构新的扣分点。"
    )
    if count == 1:
        guidance += (
            "reference_fusion 字段只说明这份答案如何作为辅助参考，不要使用“融合”“共性核心点”"
            "或“差异补充点”等多答案比较措辞。"
        )
    return guidance


def _grid_cells_for_line(text):
    characters = list(text or "")
    cells = 0
    index = 0
    while index < len(characters):
        character = characters[index]
        if character in {"—", "…"}:
            if index + 1 < len(characters) and characters[index + 1] == character:
                cells += 2
                index += 2
            else:
                cells += 2
                index += 1
            continue
        if character.isascii() and character.isalnum():
            end = index + 1
            while end < len(characters) and characters[end].isascii() and characters[end].isalnum():
                end += 1
            cells += (end - index + 1) // 2
            index = end
            continue
        # 汉字、全角标点、半角符号和实际输入的空格均占一格。
        cells += 1
        index += 1
    return cells


def answer_grid_metrics(text, columns=ANSWER_GRID_COLUMNS):
    columns = max(1, int(columns or ANSWER_GRID_COLUMNS))
    logical_lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while len(logical_lines) > 2 and logical_lines[-1] == logical_lines[-2] == "":
        logical_lines.pop()
    occupied_cells = 0
    written_cells = 0
    occupied_lines = 0
    current_line_cells = 0
    last_index = len(logical_lines) - 1
    for index, line in enumerate(logical_lines):
        content_cells = _grid_cells_for_line(line)
        written_cells += content_cells
        current_line_cells = ((content_cells - 1) % columns) + 1 if content_cells else 0
        if index < last_index:
            # 换行段落未写满的行按整行 columns 格计（申论答题纸规矩：换行后本行剩余格不再使用）。
            line_count = max(1, (content_cells + columns - 1) // columns)
            occupied_cells += line_count * columns
            occupied_lines += line_count
        else:
            occupied_cells += content_cells
            occupied_lines += (content_cells + columns - 1) // columns
    return {
        "occupied_cells": occupied_cells,
        "written_cells": written_cells,
        "unused_cells": occupied_cells - written_cells,
        "lines": occupied_lines,
        "columns": columns,
        "current_line_cells": current_line_cells,
    }


def count_cjk_chars(text):
    # 保留旧函数名供现有调用方使用；字数按答题纸占格计算。
    return answer_grid_metrics(text)["occupied_cells"]


def compact_revised_answer_linebreaks(text, word_limit=""):
    """Remove the fewest low-value body line breaks needed to fit the answer grid."""
    original = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    budget = word_limit_budget(word_limit)
    hard_max = budget["hard_max_exclusive"]
    if not original or not hard_max or count_cjk_chars(original) < hard_max:
        return original

    lines = [line.strip() for line in original.split("\n") if line.strip()]
    if len(lines) < 2 or count_cjk_chars("".join(lines)) >= hard_max:
        return original

    # Keep the title/salutation and final closing/signature layout intact. Body
    # sub-points can safely share a physical answer-sheet line when necessary.
    # 短答案（“经济效益/社会效益/生态效益”这类三段式）没有首尾布局可保，若继续
    # 按长答案保护 2+3 行，可合并区间会变成空集，导致超过硬上限也压不下来。
    if len(lines) >= 6:
        head_keep, tail_keep = 2, 3
    else:
        head_keep, tail_keep = 0, 1
    while count_cjk_chars("\n".join(lines)) >= hard_max:
        candidates = []
        for index in range(head_keep, len(lines) - tail_keep):
            left, right = lines[index], lines[index + 1]
            separator = (
                " "
                if left
                and right
                and left[-1].isascii()
                and left[-1].isalnum()
                and right[0].isascii()
                and right[0].isalnum()
                else ""
            )
            merged = [*lines[:index], left + separator + right, *lines[index + 2 :]]
            reduction = count_cjk_chars("\n".join(lines)) - count_cjk_chars(
                "\n".join(merged)
            )
            if reduction > 0:
                candidates.append((reduction, index, merged))
        if not candidates:
            return original
        _, _, lines = max(candidates, key=lambda item: (item[0], -item[1]))
    return "\n".join(lines)


def word_limit_budget(value):
    raw = str(value or "").strip()
    compact = re.sub(r"\s+", "", raw)
    numbers = [int(number) for number in re.findall(r"\d+", compact)]
    budget = {
        "raw": raw,
        "mode": "none",
        "minimum": 0,
        "suggested_min": 0,
        "suggested_max": 0,
        "hard_max_exclusive": 0,
    }
    if not numbers:
        return budget

    if "左右" in compact:
        target = numbers[-1]
        if target <= 500:
            # 申论小题的答题卡空间固定，“350字左右”等标注仍以该数字为上限；
            # 大作文的“1000字左右”才是没有强制上限的篇幅建议。
            target_min = min(target - 1, int(target * 0.90 + 0.999999))
            target_max = min(target - 1, max(target_min, int(target * 0.96)))
            budget.update(
                mode="hard_max",
                suggested_min=max(0, target_min),
                suggested_max=max(0, target_max),
                hard_max_exclusive=target + 1,
            )
        else:
            budget.update(
                mode="approximate",
                suggested_min=max(1, round(target * 0.95)),
                suggested_max=max(1, round(target * 1.05)),
            )
        return budget

    if re.search(r"(?:不少于|不低于|至少|以上)", compact):
        minimum = numbers[-1]
        budget.update(
            mode="minimum",
            minimum=minimum,
            suggested_min=minimum,
            suggested_max=max(minimum, round(minimum * 1.10)),
        )
        return budget

    is_range = len(numbers) >= 2 and bool(re.search(r"[-—–~～至到]", compact))
    if is_range:
        lower, upper = sorted(numbers[-2:])
        target_min = min(upper - 1, max(lower, int(upper * 0.90 + 0.999999)))
        target_max = min(upper - 1, max(target_min, int(upper * 0.96)))
        budget.update(
            mode="range",
            minimum=lower,
            suggested_min=max(0, target_min),
            suggested_max=max(0, target_max),
            hard_max_exclusive=upper + 1,
        )
        return budget

    hard_max = numbers[-1]
    target_min = min(hard_max - 1, int(hard_max * 0.90 + 0.999999))
    target_max = min(hard_max - 1, max(target_min, int(hard_max * 0.96)))
    budget.update(
        mode="hard_max",
        suggested_min=max(0, target_min),
        suggested_max=max(0, target_max),
        hard_max_exclusive=hard_max + 1,
    )
    return budget


def budget_status_label(actual_chars, budget):
    hard_max = budget["hard_max_exclusive"]
    suggested_min = budget["suggested_min"]
    suggested_max = budget["suggested_max"]
    if hard_max and actual_chars >= hard_max:
        return "超出硬限制"
    if budget["mode"] == "minimum":
        return "符合最低要求" if actual_chars >= budget["minimum"] else "低于最低要求"
    if budget["mode"] == "range" and actual_chars < budget["minimum"]:
        return "符合硬限制，低于最低要求"
    if not suggested_min:
        return "未标注字数要求"
    if actual_chars < suggested_min:
        return "符合字数要求，篇幅偏短" if hard_max else "低于建议区间"
    if actual_chars <= suggested_max:
        return "符合字数要求，处于建议区间" if hard_max else "处于建议区间"
    return "符合字数要求，接近上限" if hard_max else "高于建议区间"


def revised_answer_word_count_line(metrics, word_limit=""):
    actual_chars = metrics["occupied_cells"]
    budget = word_limit_budget(word_limit)
    parts = [
        f"占格：{actual_chars}格（实写{metrics['written_cells']}格，换行留空{metrics['unused_cells']}格）"
    ]
    if budget["suggested_min"]:
        parts.append(f"建议区间：{budget['suggested_min']}—{budget['suggested_max']}字")
    if budget["hard_max_exclusive"]:
        parts.append(f"硬限制：不超过{budget['hard_max_exclusive'] - 1}字")
    elif budget["minimum"]:
        parts.append(f"最低要求：不少于{budget['minimum']}字")
    else:
        parts.append("硬限制：未标注")
    parts.append(f"状态：{budget_status_label(actual_chars, budget)}")
    return "；".join(parts)


def split_revised_answer_section(report_text):
    match = re.search(r"(?m)^##\s*修改版答案\s*$", report_text or "")
    if not match:
        return None
    body_start = match.end()
    next_match = re.search(r"(?m)^##\s+", report_text[body_start:])
    body_end = body_start + next_match.start() if next_match else len(report_text)
    return match.start(), body_start, body_end


def revised_answer_body(section_text):
    # Exported grading packages ask external agents to include a standalone
    # tracked-changes preview before the clean answer.  Only the clean answer
    # participates in word counting, retries, storage, and the app's own diff.
    clean_heading = re.search(
        r"(?m)^###\s*最终清洁版(?:\s*[（(][^\n）)]*[）)])?\s*$",
        section_text or "",
    )
    if clean_heading:
        section_text = section_text[clean_heading.end():]
        next_heading = re.search(r"(?m)^#{1,3}\s+", section_text)
        if next_heading:
            section_text = section_text[:next_heading.start()]
    lines = section_text.strip("\n").splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^\s*(?:[-*]\s*)?(?:(?:估算|实际)?字数|占格)\s*[:：]", lines[0]):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and lines[0].strip().startswith("> 系统提示："):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def normalize_revised_answer_word_count(report_text, word_limit=""):
    section = split_revised_answer_section(report_text)
    if not section:
        return report_text
    section_start, body_start, body_end = section
    heading = report_text[section_start:body_start]
    section_text = report_text[body_start:body_end]
    answer_body = revised_answer_body(section_text)
    if not answer_body:
        return report_text
    metrics = answer_grid_metrics(answer_body)
    actual_chars = metrics["occupied_cells"]
    budget = word_limit_budget(word_limit)
    hard_max = budget["hard_max_exclusive"]
    over_limit = bool(hard_max and actual_chars >= hard_max)
    normalized_section = (
        heading
        + "\n"
        + revised_answer_word_count_line(metrics, word_limit)
        + "\n\n"
        + ("> 系统提示：修改版答案未满足严格硬限制，不能直接作为最终答案使用；请继续压缩。\n\n" if over_limit else "")
        + answer_body
    )
    suffix = report_text[body_end:]
    if suffix:
        normalized_section += "\n\n"
    return report_text[:section_start] + normalized_section + suffix


def revised_answer_word_count_status(report_text, word_limit=""):
    section = split_revised_answer_section(report_text)
    budget = word_limit_budget(word_limit)
    hard_max = budget["hard_max_exclusive"]
    if not section:
        return {
            "has_revised_answer": False,
            "actual_chars": 0,
            "max_chars": max(0, hard_max - 1),
            "budget": budget,
            "budget_status": "missing",
            "over_limit": False,
            "over_by": 0,
        }
    _, body_start, body_end = section
    answer_body = revised_answer_body(report_text[body_start:body_end])
    metrics = answer_grid_metrics(answer_body)
    actual_chars = metrics["occupied_cells"]
    over_by = actual_chars - hard_max + 1 if hard_max and actual_chars >= hard_max else 0
    return {
        "has_revised_answer": bool(answer_body),
        "actual_chars": actual_chars,
        "written_cells": metrics["written_cells"],
        "unused_cells": metrics["unused_cells"],
        "lines": metrics["lines"],
        "max_lines": max(0, (hard_max - 1 + ANSWER_GRID_COLUMNS - 1) // ANSWER_GRID_COLUMNS) if hard_max else 0,
        "max_chars": max(0, hard_max - 1),
        "budget": budget,
        "budget_status": budget_status_label(actual_chars, budget),
        "over_limit": over_by > 0,
        "over_by": over_by,
    }


def build_revised_answer_retry_prompt(original_prompt, report_text, word_limit=""):
    status = revised_answer_word_count_status(report_text, word_limit)
    max_chars = status["max_chars"]
    budget = status["budget"]
    return "\n".join(
        [
            "你刚才生成的批改报告中，## 修改版答案 未满足严格字数限制。",
            f"系统按25格答题纸规则复核：占格 {status['actual_chars']} 格（实写 {status['written_cells']} 格，换行留空 {status['unused_cells']} 格）；硬限制为不超过 {max_chars} 格；至少需要减少 {status['over_by']} 格。",
            f"本次返修目标：{budget['suggested_min']}—{budget['suggested_max']} 字（这是硬要求，必须压到这个区间内，不是尽量）。",
            "",
            "请基于原批改任务和原报告，只重写修改版答案正文。",
            "要求：",
            "1. 压缩顺序固定为：重复同义和空泛表达 → 差异补充点与非必要例证 → 合并相近采分点 → 压缩修饰语和过渡语。",
            "2. 必须保留共性核心采分点，以及必要的主体、对象、动作、方式和关键效果。",
            "3. 保留题目要求的文种、称谓、分点和段落格式，不得增加无材料依据的内容；继续以用户原答案为底稿，能保留的原句、编号和顺序尽量保留。但本次字数压缩优先于逐字保留：如果保留原句就无法达标，必须果断删减次要信息，不得因为舍不得原句而只做微小删减。",
            "4. 字数按系统答题纸占格估算：汉字和全角标点一格；连续半角英文或数字每两个字符一格；单个破折号或省略号两格；输入的空格一格；手动换行留下的格子和中间空白行都计入字数限制。",
            "5. 只输出下面的标签及修改后的干净答案正文，不得输出删除线、批注标记、评分、解释、字数声明或其他报告章节：",
            "<revised_answer>",
            "压缩后的答案正文",
            "</revised_answer>",
            "",
            "原批改任务如下：",
            original_prompt,
            "",
            "上一版超限报告如下：",
            report_text,
        ]
    )


def parse_revised_answer_repair(response_text):
    text = str(response_text or "").strip()
    tagged = re.search(r"<revised_answer>\s*(.*?)\s*</revised_answer>", text, flags=re.I | re.S)
    if tagged:
        return tagged.group(1).strip()
    # 追问压缩时模型常常无视“只输出 <revised_answer>”，直接回整份智能批改 JSON。
    # 不先取字段就会把整段 JSON 当成答案，字数统计随之爆到几千格。
    from_json = _revised_answer_from_smart_json(text)
    if from_json is not None:
        return from_json
    section = split_revised_answer_section(text)
    if section:
        _, body_start, body_end = section
        return revised_answer_body(text[body_start:body_end])
    if re.search(r"(?m)^##\s+(?:总体评分|得分点清单|踩点对比|遗漏点分析|材料领读|优化建议)\s*$", text):
        return ""
    text = re.sub(r"^```(?:markdown|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return revised_answer_body(text)


def _revised_answer_from_smart_json(text):
    """从整份智能批改 JSON 里取 modified answer；取不到返回 None。"""
    block = re.search(r"<smart_grading_json>\s*(.*?)\s*</smart_grading_json>", text or "", flags=re.I | re.S)
    candidate = block.group(1) if block else (text if text.lstrip().startswith("{") else "")
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    found = None

    def walk(node):
        nonlocal found
        if found is not None or not isinstance(node, dict):
            return
        value = node.get("revised_answer")
        if isinstance(value, str) and value.strip():
            found = value.strip()
            return
        for child in node.values():
            walk(child)

    walk(payload)
    return found


def replace_revised_answer_body(report_text, answer_body, word_limit=""):
    section = split_revised_answer_section(report_text)
    answer_body = str(answer_body or "").strip()
    if not section or not answer_body:
        return report_text
    _, body_start, body_end = section
    replaced = report_text[:body_start] + "\n\n" + answer_body + "\n\n" + report_text[body_end:]
    return normalize_revised_answer_word_count(replaced, word_limit)


def word_limit_max(value):
    boundary = word_limit_budget(value)["hard_max_exclusive"]
    return max(0, boundary - 1)


CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


COMPREHENSIVE_WRITING_CUES = (
    "写一篇文章",
    "写一篇关于",
    "写一篇议论文",
    "写一篇议论",
    "议论文",
    "议论性文章",
    "讨论性文章",
    "策论文",
    "对策性文章",
    "作文",
    "自拟题目",
    "自选角度",
)


def parse_number(value):
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    if value in CN_NUMBERS:
        return CN_NUMBERS[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + CN_NUMBERS.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return CN_NUMBERS.get(value[0], 0) * 10
    if "十" in value and len(value) == 3:
        return CN_NUMBERS.get(value[0], 0) * 10 + CN_NUMBERS.get(value[2], 0)
    return None


def question_value(question, key, default=""):
    if not question or not hasattr(question, "keys") or key not in question.keys():
        return default
    return question[key] or default


def question_text(question):
    return "\n".join(
        str(question_value(question, key))
        for key in ("prompt", "requirements", "title", "question_type")
    )


def is_comprehensive_writing(question):
    text = re.sub(r"\s+", "", question_text(question))
    return (
        question_value(question, "question_type") == "综合写作"
        or any(cue in text for cue in COMPREHENSIVE_WRITING_CUES)
    )


def should_use_whole_paper_materials(question):
    # A comprehensive essay draws on the whole paper even when the prompt
    # quotes only one material or does not explicitly say to consult materials.
    return is_comprehensive_writing(question)


def referenced_material_numbers(question):
    text = "\n".join(
        str(question_value(question, key))
        for key in ("prompt", "requirements", "title")
    )
    pattern = re.compile(
        r"(?:给定)?(?:资料|材料)\s*([0-9一二两三四五六七八九十]+(?:\s*(?:[-—至到~～、,，和及]\s*)[0-9一二两三四五六七八九十]+)*)"
    )
    number_pattern = re.compile(r"[0-9]+|[一二两三四五六七八九十]+")
    range_pattern = re.compile(
        r"([0-9一二两三四五六七八九十]+)\s*(?:[-—至到~～])\s*([0-9一二两三四五六七八九十]+)"
    )
    numbers = []
    for match in pattern.finditer(text):
        cluster = match.group(1)
        consumed = []
        for range_match in range_pattern.finditer(cluster):
            start = parse_number(range_match.group(1))
            end = parse_number(range_match.group(2))
            if start is not None and end is not None:
                low, high = sorted((start, end))
                numbers.extend(range(low, high + 1))
                consumed.append(range_match.span())
        remainder = list(cluster)
        for start, end in consumed:
            for index in range(start, end):
                remainder[index] = " "
        for number_match in number_pattern.finditer("".join(remainder)):
            number = parse_number(number_match.group(0))
            if number is not None:
                numbers.append(number)
    deduped = []
    for number in numbers:
        if number not in deduped:
            deduped.append(number)
    return deduped


def select_relevant_materials(question, materials):
    if not materials:
        return []
    if should_use_whole_paper_materials(question):
        return list(materials)
    wanted = set(referenced_material_numbers(question))
    if not wanted:
        return list(materials)
    selected = [material for material in materials if material["material_number"] in wanted]
    return selected or list(materials)


GRADING_REPORT_IMPORT_MARKER = "<!-- YANSHEN_GRADING_REPORT_V1 -->"
GRADING_REPORT_QUESTION_PATTERN = re.compile(r"<!--\s*QUESTION_CODE:\s*([^\n>]+?)\s*-->", re.I)
GRADING_REPORT_REQUIRED_HEADINGS = (
    "评分基准",
    "总体评分",
    "得分点清单",
    "遗漏点分析",
    "材料领读",
    "优化建议",
    "修改版答案",
)


def parse_imported_grading_report(report_text, expected_question_code="", question_type=""):
    """Validate and clean a Markdown report produced from an exported grading package."""
    text = str(report_text or "").lstrip("\ufeff").strip()
    errors = []
    if GRADING_REPORT_IMPORT_MARKER not in text[:800]:
        errors.append("缺少研申批改报告格式标记")
    question_match = GRADING_REPORT_QUESTION_PATTERN.search(text[:1200])
    question_code = question_match.group(1).strip() if question_match else ""
    if not question_code:
        errors.append("缺少 QUESTION_CODE 题目编号标记")
    elif expected_question_code and question_code != str(expected_question_code).strip():
        errors.append(f"报告题目编号 {question_code} 与当前题目 {expected_question_code} 不一致")
    headings = {
        match.group(1).strip()
        for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)
    }
    if question_type == "综合写作":
        from .grading_pipeline.essay_review import ESSAY_REPORT_HEADINGS
        # Existing outline reports remain importable; new exports use the full essay format.
        legacy = {"题意与提纲判断", "需要调整的架构", "材料与参考答案对照", "建议提纲"}
        required_headings = () if legacy.issubset(headings) else ESSAY_REPORT_HEADINGS
    else:
        required_headings = GRADING_REPORT_REQUIRED_HEADINGS
    missing = [heading for heading in required_headings if heading not in headings]
    if missing:
        errors.append("缺少必要章节：" + "、".join(missing))
    if errors:
        return "", errors
    cleaned = text.replace(GRADING_REPORT_IMPORT_MARKER, "", 1)
    cleaned = GRADING_REPORT_QUESTION_PATTERN.sub("", cleaned, count=1)
    cleaned = re.sub(r"\A\s*#\s*研申批改报告\s*", "", cleaned, count=1)
    return cleaned.strip(), []


REPORT_INSTRUCTIONS = """你是一名严谨的申论批改老师。请基于题目、作答要求、整卷材料、用户答案，以及本批改包实际提供的参考答案进行批改。

批改原则：
1. 修改版答案必须遵守题目信息中的结构化字数预算。“建议作答区间”只用于指导首轮生成，不是合格下限，也不得为了进入区间而补写套话；“硬限制”才决定能否保存。“不超过 N 字”允许等于 N，手动换行留下的格子计入字数限制。模型填写的字数只作占位，系统会重新计算并覆盖。
2. 生成修改版答案前，先在内部提炼共性核心采分点并分配表达预算。每个核心点优先保留主体、对象、动作、方式和关键效果；多份参考答案不能简单相加，差异补充点必须服从总预算，不得挤占核心点。
3. 采分始终以材料和题目要求为准，所提供的答案仅作为参考，不得机械照搬；不同机构说法冲突时，以材料原文和题干任务为准。
4. 判定“漏点”前必须先在用户原答案中查找同义表达、近义表达、主谓宾不同但意思相同的表达。只要用户已经表达了同一材料信息或同一措施方向，应判为“命中/部分命中”，不得说完全漏写。
4a. 判漏点必须留下可核对的原话证据：每一条“未命中”都要先写出你在用户答案里检索过的近义说法，并明确结论“用户原文未出现任何对应表述”；只要你确实找到了对应表述，就只能判“命中”或“部分命中”。查不到对应表述时写“已核对原答案，未见对应表述”，不得凭印象直接判漏。用户在建议段写了某一措施，而原因段没写对应原因时，要按“原因维度缺失”单独说明，不能笼统说整条要点未涉及。
4b. 参考答案必须逐份落地：`## 得分点清单` 的“参考答案融合说明”要逐个点名本批改包提供了哪几家机构的答案（按材料里给出的机构名），并说明每一家各自强调了什么、几家共有的是什么、哪些只在一家出现。不得只写“三份答案共性较高”这类空话；若某家答案的某个点没被采纳，要写明原因（材料依据不足、超出字数预算等）。
5. 内部仍要逐点区分“命中、部分命中、未命中”，以保证评分准确；但报告不再展示已命中的采分点，只展示“部分命中”和“未命中”的遗漏点分析。判定遗漏前必须先核对用户原答案中的同义、近义表达，不得把已经写出的原句判为未命中。
6. 评分要像真实申论批改：重视要点覆盖、材料依据、结构逻辑、表达规范和字数格式。
6a. 必须独立审查答案结构：依据设问、材料逻辑和参考答案共性推导可行排列，按原答案顺序列出每段/每点的功能，比较层级、先后、因果、重点篇幅和论证衔接。不能默认肯定作答思路，也不能机械要求照搬某一份参考答案。指出问题时引用原答案短句和材料或设问依据，说明具体调序、合并、拆分或重组动作；结构分须与审查结论一致。
7. 修改建议要可操作，指出应补、应删、应合并、应规范表达的位置。
8. 禁止用重复表达、空泛背景、无材料依据的意义、例证和套话凑字数。生成时应保留安全余量，避免答案贴近硬上限后因标点、实际输入的空格超限。
9. 字数统一按考试答题纸的占用格数估算：汉字、全角标点每个一格；连续英文、半角数字每两个字符一格；标准“——”“……”整体两格，单独“—”“…”也按两格；实际输入的空格一格；手动换行留下的格子和中间空白行也计入字数限制；不自动添加段首缩进。
10. 报告里凡是点评用户原答案具体字句的地方，必须使用行内批注标记，软件会据此在左侧答案快照上生成标号批注。格式为 `[类型|需要标出的用户原答案原文|点评说明|插入锚点|建议改成]`：
   - 类型只能是：好、润色、可改、删、补、关键，分别对应亮点、轻微润色、建议修改、建议删减、建议补充、关键问题；
   - 第二段（原文）必须是用户答案里**逐字连续出现且全文唯一**的一段文字，不得改写、不得拼接两处、不得加省略号；做不到唯一就换更短或更长的一段，实在找不到就不加标记，直接正常叙述；
   - 第四段只对“补”类型有意义，填插入位置前的那句用户原文（同样要求逐字唯一）；其他类型留空，写成 `[可改|原句|说明||建议]`；
   - 第五段写建议表述，没有就留空；
   - 每条批注单独占位，不要写进代码块、表格或标题里；一篇报告控制在 6—12 条，优先标出真正影响得分的句子。
   示例：`[可改|天然气不普及，入户申请长期无下文|申请无下文属审批效率问题，应与“基础设施”分开单列。||建立联合审批机制，明确办理时限]`

请固定输出以下 Markdown 结构：

## 总体评分
- 总分：X/题目分值或建议分值
- 等级：优秀/良好/一般/较弱
- 核心判断：一句话说明最大得分点和最大失分点

## 得分点清单
- 题型分类：
- 参考答案融合说明：先说明共性核心点如何提炼，再说明哪些只是差异补充点
- 标准采分点：
  1. 采分点名称：共性/差异 + 材料依据 + 规范表达

## 遗漏点分析
| 遗漏的采分点 | 材料依据 | 遗漏原因 | 整体建议 |
| --- | --- | --- | --- |
要求：只列“部分命中”和“未命中”的采分点，不再分析或展示已经完整命中的点。材料依据应引用对应材料原文短句。遗漏原因要结合用户实际写法说明具体遗漏机制，例如过早上位概括导致限定和结果被压缩、把两个独立要点强行合并、只写做法未写题目要求的成效等；若无法可靠判断机制，就客观说明“已写到什么、还缺什么”，不得臆测。整体建议必须直接告诉考生如何补足该点，优先使用材料原词和最短完整表达，避免泛泛写“加强概括能力”。若参考答案中存在但材料依据弱或只有个别机构写到，标为差异补充点，不作为主要扣分依据。

## 结构与顺序审查
- 题目与材料支持的结构：按合理顺序列出各段/各分点应承担的任务，并说明参考答案共性依据。
- 我的答案实际结构：按原文顺序列出每段/每点的作用，不要只写“总分总”。
- 对照结论：合理 / 需局部调整 / 需重组；说明与参考答案共性结构的关键差异，以及不同排列是否同样合理。
- 结构得分依据：指出具体优点和缺陷，说明结构分为何在此档位；不得仅因有标题、分段或分点就给高分。
- 具体调整：对每处真正影响作答任务的结构问题，引用我的答案原文短句及题干/材料/参考答案共性依据，给出可执行的调序、合并、拆分或重组动作；若结构合理，基于具体证据说明无需调整。

## 材料领读
按材料段落梳理：材料信息 -> 可转化要点 -> 答案表达。只写和本题有关的材料。

## 优化建议
1. 个性化建议：
2. 结构化建议：
3. 表达规范建议：

## 修改版答案
本节必须按以下顺序输出，两个三级标题均不得省略或改名。修改痕迹必须直接在 Markdown 中渲染，不得只给一段普通的重写答案，也不得依赖研申软件才能看出改动。

### 修改痕迹可视化（必须）
先输出一行统计：`替换 N 处 · 删除 N 处 · 补充 N 处`。统计口径：相邻的删除与新增算一处替换；只有删除算删除；只有新增算补充。

然后以“我的答案”全文为底稿，按原顺序输出一份完整的逐字修订视图：
- 未改文字保持普通文字；删除内容使用 `<del style="color:#a65743;background:#fff1ed;text-decoration-thickness:2px;">删除文字</del>`；新增或替换后的内容使用 `<ins style="color:#216757;background:#edf7f3;text-decoration-thickness:2px;text-underline-offset:3px;">新增文字</ins>`。
- 替换必须紧邻输出 `<del>原文字</del><ins>新文字</ins>`；纯补充只用 `<ins>`；纯删除只用 `<del>`。
- 外层必须使用 `<div style="white-space:pre-wrap;line-height:1.9;padding:16px;border:1px solid #d9e2dd;border-radius:10px;">……</div>` 包裹，保留原答案的分段和编号。HTML 特殊字符须转义，正文换行可使用 `<br>`。
- 只标真正发生变化的最小连续片段，不能把整段原文全部标成删除、再把整段新答案全部标成新增；不能省略未修改文字；不能使用代码块、diff 代码围栏、表格或仅用“原文/修改后”两段对照代替。
- 该视图必须能在支持 Markdown 内嵌 HTML 的查看器中直接显示；即使颜色样式被查看器过滤，也必须依靠语义化 `<del>` / `<ins>` 保留删除线和新增下划线。

### 最终清洁版（必须）
先写“实际字数：X 字；建议区间：A—B 字；硬限制：不超过 N 字；状态：待系统复核”，再仅输出去掉所有 `<del>` 内容、保留所有 `<ins>` 内容后的纯文本最终答案。这里禁止出现 `<del>`、`<ins>`、删除线、批注或解释。

最终清洁版应以用户原答案为底稿做最小必要修改，能保留的原句、编号和顺序尽量保留，不得整体换写成与原答案无对应关系的范文。建议区间是首轮生成目标，不是最低要求；不得因正文偏短而添加弱依据内容。如有硬限制，答案不得超过上限，等于上限合格。如果题目有文种、称谓、分点或段落要求，必须保留。研申导入时只会对“最终清洁版”重新计算字数，并基于原答案生成软件内修订视图；若首轮硬超限，系统只会要求局部压缩最终清洁版，不会重写其他报告章节。
"""


# 导出批改包专用提示词：复刻智能批改的两阶段流程（先独立建评分基准，再按基准综合评分）。
# REPORT_INSTRUCTIONS 提供批改原则与完整报告输出结构，叠加在两阶段框架之后。
SMART_GRADING_EXPORT_PROMPT = """你正在执行申论智能批改。请严格分两阶段完成，先做「第一阶段」，再做「第二阶段」，两阶段都要输出；第一阶段的评分基准一旦确定，第二阶段不得因考生答案而增删采分点或改动权重。

最终交付必须是一份可被研申直接导入的 Markdown 批改报告。报告最前面逐字输出以下两行，不要放进代码块：
<!-- YANSHEN_GRADING_REPORT_V1 -->
<!-- QUESTION_CODE: 这里逐字填写 `02-题目与材料.md` 中的题目编号 -->
随后直接输出 `# 研申批改报告`。不得删除、改写或移动这两个 HTML 注释标记；软件会用它们校验报告是否属于当前题目。

## 第一阶段：独立建立评分基准（先做）
只依据同目录下 `02-题目与材料.md` 的题目、作答要求与材料原文，以及 `03-参考答案.md` 的参考答案，独立完成：
1. 先做参考答案共识：从多份参考答案中提炼共同确认的共性核心点，差异补充点单列，不得机械相加；
2. 提炼 4—12 个有材料依据、能在题目字数预算内完成的采分点；
3. 为每个采分点标注：材料依据（引用材料原文短句）、重要程度（critical / major / supporting）、建议权重、命中所需关键词；
4. 在报告开头输出评分基准表，作为 `## 评分基准` 章节：
   | 采分点 | 材料依据 | 重要程度 | 建议权重 | 命中关键词 |

评分基准要求：必须在题目字数预算内完成；只依据材料与参考答案，绝不依据考生答案。

## 第二阶段：按评分基准综合评分（再做）
对照第一阶段建立的评分基准，批改 `04-我的答案.md` 中的考生答案：
- 内部逐点核对：先在原答案中查找同义/近义表达，能对应的判「命中/部分命中」，找不到才判「未命中」；不得把考生已写出的原句误判为漏点；最终报告的“遗漏点分析”只展示部分命中和未命中点；
- 单独输出“结构与顺序审查”：先推导材料和参考答案共性支持的可行结构，再按原答案顺序拆解段落/分点，说明差异是否构成真实问题、怎样调整，并让结构得分与审查结论一致；
- 维度综合评分：要点覆盖、材料依据、结构逻辑、表达规范、字数格式；
- `05-检索证据.md` 的相似题方法与教材知识只用于点评建议，不作为主要扣分依据；
- 修改版答案严格遵守题目字数硬限制（占格规则见下；可用 `06-字数计算.py` 精确校验，超限先压缩再输出）；
- 修改版答案必须同时包含 `### 修改痕迹可视化（必须）` 与 `### 最终清洁版（必须）`：前者用 Markdown 内嵌 HTML 的 `<del>` / `<ins>` 逐字展示删除、新增和替换，后者提供不含标记的纯文本答案；
- 最后按下方「完整批改报告输出格式」输出完整报告。

## 完整批改报告输出格式
""" + REPORT_INSTRUCTIONS


def render_package_evidence(evidence):
    """Format smart-grading retrieval cards (similar questions / knowledge /
    personal history) into markdown lines. current_scoring cards duplicate the
    question/references/materials already in the package and are skipped."""
    if not evidence:
        return []
    labels = {"question": "相似题方法", "knowledge": "教材知识"}
    buckets = {}
    for card in evidence or []:
        if not isinstance(card, dict) or card.get("role") == "current_scoring":
            continue
        source = str(card.get("source_type") or "")
        label = labels.get(source, "个人历史")
        buckets.setdefault(label, []).append(card)
    if not buckets:
        return []
    parts = [
        "",
        "## 智能批改检索证据",
        "（相似题、教材知识与个人历史检索结果，供综合评分与点评建议参考；除非有明确材料依据，不作为主要扣分依据。）",
    ]
    for label, cards in buckets.items():
        parts.extend(["", f"### {label}（{len(cards)} 条）"])
        for card in cards:
            title = str(card.get("title") or "未命名")
            snippet = str(card.get("snippet") or "").strip()
            parts.append(f"- {title}")
            if snippet:
                parts.append(f"  {snippet}")
    return parts


def build_grading_package(
    question,
    references,
    attempt=None,
    materials=None,
    custom_reference_answer="",
    grading_basis=None,
    evidence=None,
):
    if question_value(question, "question_type") == "综合写作":
        from .grading_pipeline.essay_review import build_essay_review_prompt

        material_rows = [dict(item) for item in select_relevant_materials(question, materials or [])]
        prompt = build_essay_review_prompt(dict(question), material_rows,
                                           question_value(attempt, "answer_text"),
                                           can_search=True, markdown_output=True)
        instructions, _, source_text = prompt.partition("【年份、地区、考试、卷别】\n")
        question_text, _, answer_text = source_text.partition("【我的作答】\n")
        return "\n".join([
            "# 大作文深度审阅包", "", "## 批改提示词（请先读）",
            "请先阅读题目、整卷材料与我的作答，再自行联网搜索并核对多篇公开范文。"
            "机构参考答案可能有误，本包不导出、不作为判断依据。",
            "若模型没有联网能力，必须写明未联网，不得编造检索来源。输出 Markdown，最前面依次写：",
            GRADING_REPORT_IMPORT_MARKER,
            f"<!-- QUESTION_CODE: {question['question_code']} -->",
            "# 研申批改报告",
            "并保留以下二级标题：题意与文章判断、范文检索与核验、总论点与分论点、逐段论证与行文修改、材料运用与建议架构、主题表达积累、下一步修改。",
            "范文检索章节列出实际访问的标题、URL、与本题的关联和局限；不要照抄。"
            "逐段修改引用我的原句。表达积累须注明出处或标明原创。不要评分或整篇代写。",
            instructions.strip(),
            "", "---", "", "## 题目与整卷材料", "", "【年份、地区、考试、卷别】", question_text.rstrip(),
            "", "## 我的作答", "", answer_text.strip(),
        ])
    attempt_text = attempt["answer_text"] if attempt else ""
    attempt_created = format_beijing_time(attempt["created_at"]) if attempt else "未保存作答"
    budget = word_limit_budget(question["word_limit"] or "")
    suggested_range = (
        f"{budget['suggested_min']}—{budget['suggested_max']}字"
        if budget["suggested_min"]
        else "未标注"
    )
    hard_limit = (
        f"不超过{budget['hard_max_exclusive'] - 1}字"
        if budget["hard_max_exclusive"]
        else "无"
    )
    minimum_requirement = (
        f"不少于{budget['minimum']}字（仅提示，不触发自动补写）"
        if budget["minimum"]
        else "无"
    )
    if materials:
        materials = select_relevant_materials(question, materials)
        material_text = "\n\n".join(
            f"{material['title'] or ('材料' + str(material['material_number']))}\n{material['content']}"
            for material in materials
        )
    else:
        material_text = question["materials"] or "未录入材料原文。"

    parts = [
        "# 申论作答批改包",
        "",
        "## 批改提示词（请先读）",
        "### 回传给研申的 Markdown 格式（用于‘导入批改数据’）",
        "最终批改报告不要放进代码块，第一行必须逐字写：`<!-- YANSHEN_GRADING_REPORT_V1 -->`。",
        f"第二行必须逐字写：`<!-- QUESTION_CODE: {question['question_code']} -->`。",
        "第三行写 `# 研申批改报告`；随后必须保留 `## 评分基准`、`## 总体评分`、`## 得分点清单`、`## 遗漏点分析`、`## 结构与顺序审查`、`## 材料领读`、`## 优化建议`、`## 修改版答案` 八个二级标题。研申会按必要标记校验并导入，标题不得改名。",
        "`## 修改版答案` 内必须保留 `### 修改痕迹可视化（必须）` 和 `### 最终清洁版（必须）`：前者直接在 Markdown 中渲染逐字删除/新增痕迹，后者为纯文本最终答案。",
        "",
        REPORT_INSTRUCTIONS,
        "",
        "---",
        "",
        "## 题目信息",
        f"- 题目编号：{question['question_code']}",
        f"- 考试：{question['year']} {question['region']} {question['exam_type']}",
        f"- 原卷：{question['paper_name'] or '未标注'}",
        f"- 题型：{question['question_type']}",
        f"- 标题：{question['title']}",
        f"- 字数限制：{question['word_limit'] or '未标注'}",
        f"- 建议作答区间：{suggested_range}",
        f"- 硬限制：{hard_limit}",
        f"- 最低要求：{minimum_requirement}",
        f"- 训练优先级：{question['zhejiang_relevance']}/5",
        f"- 原文完整：{'是' if question['is_full_original'] else '否/待校对'}",
        "",
        "## 题目",
        question["prompt"],
        "",
        question["requirements"],
        "",
        "## 材料",
        material_text,
        "",
        "## 本次批改参考答案",
        "",
        "参考答案使用规则：多份参考答案只能用于提炼共性核心点与材料依据，不能把不同机构的所有要点机械相加；差异补充点应谨慎作为加分提醒，不能导致修改版答案超出字数上限。",
    ]
    sample_guidance = limited_reference_guidance(len(references))
    if sample_guidance:
        parts.append(sample_guidance)

    if references:
        for index, ref in enumerate(references, start=1):
            parts.extend(
                [
                    "",
                    f"### {index}. {ref['organization']}",
                    ref["answer_text"],
                    "",
                    "采分点：",
                    ref["scoring_points"] or "未提炼",
                ]
            )
    if custom_reference_answer.strip():
        parts.extend(
            [
                "",
                "### 用户补充参考答案",
                custom_reference_answer.strip(),
            ]
        )
    if not references and not custom_reference_answer.strip():
        parts.append("本次未提供参考答案，请仅依据题目、作答要求和材料进行批改。")

    if grading_basis:
        if grading_basis.get("kind") == "cached_rubric":
            parts.extend(["", "## AI 智能评分基准"])
            rubric = grading_basis.get("rubric") or {}
            parts.append("以下为本题在智能批改中生成、已缓存并通过材料引文校验的评分基准；仍须核对用户答案中的同义表达。")
            for index, point in enumerate(rubric.get("points") or [], start=1):
                basis_evidence = "；".join(item.get("quote") or "" for item in point.get("material_evidence") or [])
                parts.append(
                    f"{index}. [{point.get('tier')}] {point.get('label')}：{point.get('canonical_expression')}"
                    f"；材料依据：{basis_evidence or '不足'}；支持机构数：{point.get('support_org_count', 0)}"
                )
        else:
            parts.extend(
                [
                    "",
                    "## 评分依据状态",
                    "本题尚未生成 AI 智能评分基准。本批改包已主动省略未经材料核验的本地分句聚类候选，禁止把相似句或材料片段直接当作采分点。",
                    "请先依据题目任务、作答要求和材料原文，结合所选参考答案，独立提炼 4—12 个有材料依据、能在规定字数内完成的采分点；再逐点核对我的答案。每个扣分点都必须说明对应材料依据，并识别同义或近义表达。",
                ]
            )

    if evidence:
        parts.extend(render_package_evidence(evidence))

    parts.extend(
        [
            "",
            "## 我的答案",
            f"- 作答时间：{attempt_created}",
            f"- 实际字数：{count_cjk_chars(attempt_text)}",
            "原文命中识别规则：批改踩点前必须先在下方原答案中查找同义或近义表达；若用户已经写出同一意思，请判为命中或部分命中，不要误判为漏点。",
            "",
            attempt_text or "（请在这里粘贴我的答案）",
        ]
    )
    return "\n".join(parts)


# 系统提示词：用户先把这段发给网页版 GPT，再上传批改包。
GRADING_SYSTEM_PROMPT = """# 系统提示词：申论批改老师（处理上传的批改包）

你是一名严谨的申论批改老师，专精中国公务员考试《申论》作答批改。用户会给你上传一个「申论批改包」（压缩包解压后的多个文件），你要按下面的流程完成一次专业、可执行的批改。

## 批改包文件（按顺序读取）
- `00-系统提示词.md`：你现在正在读的，规定你的角色与流程
- `01-批改提示词.md`：本次批改的详细规则与必须遵守的输出格式（务必先读）
- `02-题目与材料.md`：题目、作答要求、整卷材料
- `03-参考答案.md`：参考答案（若该题曾跑过智能批改，可能含 AI 评分基准）
- `04-我的答案.md`：考生（用户）的答案
- `05-检索证据.md`：相似题方法、教材知识、个人历史（只用于点评建议）
- `06-字数计算.py`：答题纸实写字数与占用格数计算脚本，可运行以精确核对字数

## 处理流程（严格按此顺序，两阶段，不得跳过或合并）

### 第一阶段 · 独立建立评分基准（先做）
1. 先读 `01` 明确本次规则，再依次读 `02`、`03`、`05`，全部读完；
2. 从多份参考答案提炼共识核心点（差异补充点单列、不机械相加）；
3. 只依据题目、作答要求和材料原文，独立提炼 4—12 个有材料依据、能在字数预算内完成的采分点，标注材料依据 / 重要程度 / 建议权重 / 命中关键词；
4. 在报告开头输出 `## 评分基准` 表格。**本阶段不得查看考生答案；采分点一旦确定，后续不得因答案而增删或改动权重。**

### 第二阶段 · 按评分基准综合评分（再做）
5. 读 `04` 考生答案，对照评分基准逐点核对：先在原答案中查找同义/近义表达，能对应的判「命中/部分命中」，找不到才判「未命中」；不得把考生已写出的原句误判为漏点；
6. 先按题干、材料和参考答案共性审查考生答案的段落/分点结构、先后顺序与论证衔接，指出需要调整的原文位置及依据，再按维度综合评分（要点覆盖、材料依据、结构逻辑、表达规范、字数格式）；
7. `05` 的相似题/知识/历史只用于点评建议，不作为主要扣分依据；
8. 生成修改版答案，严格遵守题目字数硬限制；需要精确核对时运行 `python3 06-字数计算.py --limit <硬限制> --file <答案文件>`，超限先压缩再输出。
9. 修改版答案必须以考生原答案为底稿做最小必要修改，并按 `01` 的模板同时输出：可独立渲染的逐字修改痕迹（`<del>` 删除、`<ins>` 新增/替换）和不含任何标记的最终清洁版。不得只输出普通重写稿。

## 输出要求
- 最终回答必须以 `<!-- YANSHEN_GRADING_REPORT_V1 -->` 开头，第二行必须是 `<!-- QUESTION_CODE: 当前题目编号 -->`，第三行写 `# 研申批改报告`；不要用 Markdown 代码块包裹整份报告。
- 严格按 `01` 的 Markdown 结构输出完整报告，一个都不能少：评分基准、总体评分、得分点清单、遗漏点分析、结构与顺序审查、材料领读、优化建议、修改版答案。
- 内部仍要逐点判断命中状态，但报告中的“遗漏点分析”只列部分命中和未命中点，并逐项写明材料依据、遗漏原因和整体建议；完整命中点不再展示。
- 修改版答案占用格数不得超过字数硬限制（等于上限合格）；手动换行留下的格子也计入限制。不得为凑字数补写套话、空泛背景或无材料依据的意义。
- `## 修改版答案` 中的 `### 修改痕迹可视化（必须）` 必须用普通文字 + `<del>` + `<ins>` 拼成完整修订稿，并输出替换/删除/补充计数；`### 最终清洁版（必须）` 只放纯文本答案。两部分缺一即视为未完成，请在发送前自检并补齐。
- 可视化修订必须直接包含在最终 Markdown 中，使报告在支持 Markdown 内嵌 HTML 的 Agent 或编辑器里即可显示删除线、下划线和颜色；不得要求用户导入研申后才能查看。

## 底线
- 只依据包内材料批改，不臆造采分点；参考答案仅作参考，与材料冲突时以材料原文和题干任务为准。
- 报告要能让考生直接照着改：指出应补、应删、应合并、应规范表达的具体位置。
- 若包内文件缺失、内容不完整或与说明不符，先向用户说明缺了什么，不要凭想象补齐。
"""

# 独立字数脚本：与研申 App 同口径的答题纸占格计算，网页版 GPT 可直接运行。
GRADING_WORD_COUNT_SCRIPT = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""申论答题纸字数计算脚本（与研申 App 同口径）。

规则：
- 汉字、全角标点、半角符号、实际输入的空格：每字符 1 格
- 连续英文 / 半角数字：每 2 个字符 1 格（不足 2 个也按 1 格）
- "——" "……" 整体 2 格；单独的 "—" "…" 也按 2 格
- 手动换行：本行未写满也按整行 25 格结算，剩余格计入字数限制
- 中间的纯空白行占 25 格

用法：
    python3 字数计算.py "正文文本"
    python3 字数计算.py --file 我的答案.txt
    python3 字数计算.py --limit 500 --file 我的答案.txt
输出：实写字数、占用格数、占用行数、逐段明细、是否超过硬限制。
"""
import argparse
import sys

COLUMNS = 25


def grid_cells_for_line(text):
    characters = list(text or "")
    cells = 0
    index = 0
    while index < len(characters):
        character = characters[index]
        if character in {"\\u2014", "\\u2026"}:
            if index + 1 < len(characters) and characters[index + 1] == character:
                cells += 2
                index += 2
            else:
                cells += 2
                index += 1
            continue
        if character.isascii() and character.isalnum():
            end = index + 1
            while end < len(characters) and characters[end].isascii() and characters[end].isalnum():
                end += 1
            cells += (end - index + 1) // 2
            index = end
            continue
        cells += 1
        index += 1
    return cells


def answer_grid_metrics(text, columns=COLUMNS):
    columns = max(1, int(columns))
    logical_lines = str(text or "").replace("\\r\\n", "\\n").replace("\\r", "\\n").split("\\n")
    while len(logical_lines) > 2 and logical_lines[-1] == logical_lines[-2] == "":
        logical_lines.pop()
    occupied_cells = 0
    written_cells = 0
    occupied_lines = 0
    current_line_cells = 0
    last_index = len(logical_lines) - 1
    detail = []
    for index, line in enumerate(logical_lines):
        content_cells = grid_cells_for_line(line)
        written_cells += content_cells
        current_line_cells = ((content_cells - 1) % columns) + 1 if content_cells else 0
        if index < last_index:
            line_count = max(1, (content_cells + columns - 1) // columns)
            occupied_cells += line_count * columns
            occupied_lines += line_count
            detail.append((len(detail) + 1, content_cells, line_count * columns))
        else:
            occupied_cells += content_cells
            occupied_lines += (content_cells + columns - 1) // columns
            detail.append((len(detail) + 1, content_cells, content_cells))
    return {
        "occupied_cells": occupied_cells,
        "written_cells": written_cells,
        "unused_cells": occupied_cells - written_cells,
        "lines": occupied_lines,
        "columns": columns,
        "current_line_cells": current_line_cells,
        "detail": detail,
    }


def main():
    parser = argparse.ArgumentParser(description="申论答题纸字数计算（实写字数与占用格数）")
    parser.add_argument("text", nargs="*", help="直接传入正文（多个参数会拼接）")
    parser.add_argument("--file", help="从文件读取正文")
    parser.add_argument("--limit", type=int, help="硬限制（占用格数不得超过该值）")
    parser.add_argument("--columns", type=int, default=COLUMNS, help="每行格数，默认 25")
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    elif args.text:
        text = "".join(args.text)
    else:
        text = sys.stdin.read()

    metrics = answer_grid_metrics(text, args.columns)
    print("实际字数：{} 字".format(metrics["written_cells"]))
    print("排版占格：{} 格（手动换行留空 {} 格）".format(metrics["occupied_cells"], metrics["unused_cells"]))
    print("占用行数：{} 行（每行 {} 格）".format(metrics["lines"], metrics["columns"]))
    print("当前行已用：{} 格".format(metrics["current_line_cells"]))
    print("逐段明细（段序, 本段内容格, 本段结算占格）：")
    for seq, content, settled in metrics["detail"]:
        print("  段 {}：内容 {} 格 -> 结算 {} 格".format(seq, content, settled))
    if args.limit is not None:
        ok = metrics["occupied_cells"] <= args.limit
        print("硬限制：不超过 {} 字 -> {}".format(args.limit, "合格（占格 <= 上限）" if ok else "超限（占格 > 上限，必须压缩）"))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
'''


# 使用说明：给用户的。
GRADING_PACKAGE_README = """# 申论批改包使用说明

## 这是给谁的
这个压缩包用于让「网页版 ChatGPT / 任何 GPT 模型」批改你的申论作答，**不消耗 Codex 额度**。

## 怎么用
1. 打开压缩包，先看 `00-系统提示词.md`，把它的内容整段发给网页版 GPT（作为开场指令，告诉它你的角色设定和批改流程）。
2. 把 `00`—`06` 的内容依次发给网页版 GPT（或直接把文件上传/拖给它）：
   - `00-系统提示词.md`：角色与批改流程
   - `01-批改提示词.md`：本次批改的详细规则和输出格式
   - `02-题目与材料.md`：题目、作答要求、整卷材料
   - `03-参考答案.md`：参考答案（含 AI 评分基准，若有）
   - `04-我的答案.md`：你的答案
   - `05-检索证据.md`：智能批改检索到的相似题/教材知识/个人历史（供点评参考）
   - `06-字数计算.py`：答题纸字数脚本，GPT 可用 `python3` 运行以精确核对占格
3. 让 GPT 严格按 `01` 的输出格式生成批改报告，需要核对字数时让它运行 `06-字数计算.py`。最终报告必须保留开头的 `YANSHEN_GRADING_REPORT_V1` 和 `QUESTION_CODE` 两个标记。
4. 把 GPT 最终回答保存为 `.md` 文件，回到研申该题批改页，点击「导入批改数据」选择这个 Markdown。导入成功后会直接出现在左侧“批改报告”区域；如果软件本身已有批改报告，会出现多个报告切换按钮。

## 注意
- 一定要把 `00`—`06` 都发给 GPT，只发提示词它看不到题目和答案。
- 修改版答案的字数限制以 `02` 中「字数限制」为准，占格规则见 `01`，也可运行 `06-字数计算.py` 校验。
- 最终 Markdown 的“修改版答案”必须同时有“修改痕迹可视化”和“最终清洁版”。前者使用内嵌 HTML 的 `<del>` / `<ins>`，可直接在支持 Markdown 内嵌 HTML 的 Agent 或编辑器中显示，不需要先导入研申。
- 如果想只发一个文件：把 `01` 之后的所有内容（题目、材料、参考答案、你的答案）拼进 `01-批改提示词.md` 的「---」下方再发送。
"""


def build_grading_package_zip(package_text):
    """Turn the single grading-package markdown into a multi-file zip whose
    first file is the system prompt and whose 01 file leads with the grading
    prompt, so a web ChatGPT can grade without touching Codex quota."""
    import io
    import zipfile

    if package_text.startswith("# 大作文深度审阅包"):
        buffer = io.BytesIO()
        instructions, _, sources = package_text.partition("\n---\n")
        topic, _, answer = sources.partition("\n## 我的作答\n")
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("大作文深度审阅.md", package_text)
            zf.writestr("01-批改提示词.md", instructions + "\n\n请同时阅读 02 和 03 文件，再开始检索与审阅。\n")
            zf.writestr("02-题目与材料.md", topic.strip() + "\n")
            zf.writestr("03-我的作答.md", "## 我的作答\n" + answer.strip() + "\n")
            zf.writestr("使用说明.md", "把“大作文深度审阅.md”全文交给具备网页检索能力的 AI；保存其 Markdown 报告后在本题页面导入。若使用不具备联网能力的模型，报告必须注明未联网。\n")
        return buffer.getvalue()

    _head, _separator, data = package_text.partition("\n---\n")
    prompt_file = (
        SMART_GRADING_EXPORT_PROMPT
        + "\n\n---\n\n"
        + "> 同目录下的 `02-题目与材料.md`、`03-参考答案.md`、`04-我的答案.md`、`05-检索证据.md`、`06-字数计算.py` 包含全部批改材料，请全部读完再按两阶段流程执行。\n"
    )
    topic_marker, refs_marker, evidence_marker, answer_marker = (
        "## 题目信息",
        "## 本次批改参考答案",
        "## 智能批改检索证据",
        "## 我的答案",
    )
    topic_start, refs_start, ev_start, answer_start = (
        data.find(topic_marker),
        data.find(refs_marker),
        data.find(evidence_marker),
        data.find(answer_marker),
    )
    topic = data[topic_start:refs_start].strip() if topic_start >= 0 and refs_start > topic_start else ""
    refs_end = ev_start if ev_start > refs_start else answer_start
    refs = data[refs_start:refs_end].strip() if refs_start >= 0 and refs_end > refs_start else ""
    evidence = data[ev_start:answer_start].strip() if ev_start >= 0 and answer_start > ev_start else ""
    if evidence.startswith(evidence_marker):
        evidence = evidence[len(evidence_marker):].strip()
    answer = data[answer_start:].strip() if answer_start >= 0 else ""
    files = [
        ("00-系统提示词.md", GRADING_SYSTEM_PROMPT),
        ("01-批改提示词.md", prompt_file),
        ("02-题目与材料.md", f"# 题目与材料\n\n{topic}" if topic else ""),
        ("03-参考答案.md", f"# 参考答案\n\n{refs}" if refs else ""),
        ("04-我的答案.md", f"# 我的答案\n\n{answer}" if answer else ""),
        ("05-检索证据.md", f"# 智能批改检索证据\n\n{evidence}" if evidence else ""),
        ("06-字数计算.py", GRADING_WORD_COUNT_SCRIPT),
        ("使用说明.md", GRADING_PACKAGE_README),
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files:
            if content:
                zf.writestr(name, content)
    return buffer.getvalue()


def build_ai_prompt(
    question,
    references,
    attempt,
    materials=None,
    custom_reference_answer="",
):
    return build_grading_package(
        question,
        references,
        attempt,
        materials,
        custom_reference_answer,
    )
