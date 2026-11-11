import json
import logging
import threading

from ..agent_modules import FEATURE_HASH_MODEL
from ..db import connect
from ..grading import (
    build_revised_answer_local_edit_prompt,
    build_revised_answer_retry_prompt,
    compact_revised_answer_linebreaks,
    normalize_revised_answer_word_count,
    parse_revised_answer_repair,
    replace_revised_answer_body,
    revised_answer_overwrites_original,
    revised_answer_word_count_status,
)
from ..normative_expressions import ingest_report_normative_expressions
from .calibration import load_calibration_policy
from .common import (
    PIPELINE_VERSION,
    QUESTION_TYPE_PROFILES,
    RUBRIC_VERSION,
    _clean,
    _row_dict,
    grading_input_hash,
    question_display_max_score,
    question_score_is_estimated,
    question_word_limit_text,
    reference_set_hash,
    rubric_source_hash,
)
from .contracts import GradingJobOptions
from .essay_review import (
    build_essay_review_prompt,
    render_essay_review,
    validate_essay_review,
)
from .evidence import (
    ESSAY_SCORING_GUIDANCE,
    _save_rubric_to_db,
    build_grading_prompt,
    retrieve_grading_evidence,
)
from .persistence import load_job_context as _load_job_context
from .persistence import update_job as _update_job
from .report import render_grading_report
from .rubric import (
    build_rubric_prompt,
    compact_reference_consensus,
    extract_tagged_json,
    manual_grading_basis,
    validate_rubric,
)
from .state import ACTIVE_JOB_STATUSES, GradingRunState, classify_grading_error
from .validation import validate_grading_result

__all__ = [
    "ACTIVE_JOB_STATUSES",
    "QUESTION_TYPE_PROFILES",
    "apply_report_feedback",
    "build_grading_prompt",
    "build_rubric_prompt",
    "compact_reference_consensus",
    "create_grading_job",
    "grading_job_payload",
    "invalidate_question_rubrics",
    "manual_grading_basis",
    "question_display_max_score",
    "question_score_is_estimated",
    "render_grading_report",
    "retrieve_grading_evidence",
    "rubric_cache_status",
    "run_grading_job",
    "start_grading_job",
    "validate_grading_result",
    "validate_rubric",
]


def _repair_json_prompt(original, response, tag):
    return f"""下面的响应未能解析为规定 JSON。只修复格式，不改变事实、判断或正文。
请只输出 <{tag}> 与 </{tag}> 包裹的合法 JSON，不要输出 Markdown。

原任务：
{original}

待修复响应：
{response}
"""


def _repair_rubric_prompt(original, response, error):
    return f"""下面的评分基准虽然可以解析，但未通过业务校验：{error}
请重新核对题干、材料原文和机构参考答案，修正计分点本身，不能只修改 JSON 格式。
- 每个计分点的 material_evidence.quote 必须是本题材料中的连续原文，不能引用机构答案或编造材料。
- core 需要达到原任务规定的机构支持数量。没有机构共识、但材料直接证实且属于完成题目任务不可缺少的要点，可用 material_core，并设 required_for_full_score=true、填写正数 suggested_weight。
- supporting 和 disputed 不能充当必答计分点；不要仅为了通过校验把缺乏材料依据的点改为 material_core。
- 至少保留一个经材料校验的必答计分点。若原响应中的点均不成立，请根据材料和题目任务重新提炼。
只输出 <rubric_json> 与 </rubric_json> 包裹的完整合法 JSON，不要输出 Markdown。

原任务：
{original}

未通过校验的响应：
{response}
"""


def _repair_smart_response_prompt(original, response, error):
    return f"""下面的申论批改响应未通过系统校验。请只修复指出的问题，不改变材料事实。
只输出一个 <smart_grading_json> 与 </smart_grading_json> 包裹的完整合法 JSON，不要输出 Markdown。

dimension_scores 里每个维度的 score 必须是该维度 weight 的绝对分数（0 到 weight 之间），
不要按题目满分折算成十分制或别的比例；维度权重和满分都在原任务的评分基准里。

校验错误：
{error}

原任务：
{original}

待修复响应：
{response}
"""


def _smart_response_parts(response, expects_rubric):
    payload = extract_tagged_json(response, "smart_grading_json")
    if not isinstance(payload, dict):
        raise ValueError("智能批改响应不是 JSON 对象")
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("智能批改响应缺少 evaluation")
    rubric = payload.get("rubric")
    if expects_rubric and not isinstance(rubric, dict):
        raise ValueError("首次智能批改响应缺少 rubric")
    return rubric, evaluation


def _build_review_prompt(question, rubric, answer_text, result):
    essay_guidance = ESSAY_SCORING_GUIDANCE if question.get("question_type") == "综合写作" else ""
    return f"""你正在复核一份申论智能评分中的结构化冲突。只纠正采分点状态、证据短引文和维度分，不重写点评或修改版答案。

题目信息：
{json.dumps({key: question.get(key) for key in ('question_type', 'prompt', 'requirements', 'word_limit')}, ensure_ascii=False)}

评分基准：
{json.dumps(rubric, ensure_ascii=False)}

用户答案：
{answer_text}

待复核结果与触发原因：
{json.dumps({'point_matches': result.get('point_matches'), 'dimension_scores': result.get('dimension_scores'), 'review': result.get('review')}, ensure_ascii=False)}

{essay_guidance}

只输出 <smart_grading_json> 包裹的合法 JSON：
<smart_grading_json>
{{"evaluation": {{
  "point_matches": [{{"point_key": "", "status": "hit|partial|miss", "coverage_ratio": 0.0, "answer_quote": "一段短连续原文，或用……连接按顺序出现的短片段", "reason": "复核依据", "confidence": 0.0, "missing_elements": []}}],
  "dimension_scores": [{{"dimension": "", "score": 0.0, "reason": "复核后的维度依据"}}]
}}}}
</smart_grading_json>

规则：每个评分基准 point_key 和每个维度必须且只能出现一次；不得输出总分；不得因空格、标点、引号或省略号形式差异把已有语义改判为未命中；综合写作不要求机械覆盖每一则材料案例。
"""


def _merge_review_evaluation(original, reviewed):
    merged = dict(original or {})
    for key in ("point_matches", "dimension_scores", "holistic_adjustment_reason"):
        if reviewed.get(key) is not None:
            merged[key] = reviewed[key]
    return merged


def _call_grading_model(
    chat_completion_func,
    settings,
    prompt,
    deep_thinking=False,
    structured=False,
):
    request_options = {
        "thinking": "enabled" if deep_thinking else "disabled",
    }
    if structured:
        request_options.update(
            {
                "response_format": {"type": "json_object"},
                # Deep-think reasoning consumes tokens before the final JSON,
                # so the structured response needs headroom to avoid truncation
                # (which used to force a repair round).
                "max_tokens": 16384,
            }
        )
    return chat_completion_func(
        settings,
        prompt,
        request_options,
    )


def _run_essay_review_job(db_path, job_id, chat_completion_func, run_state, context):
    _, attempt, question, materials, references, settings, _, options = context
    answer_text = attempt.get("answer_text") or ""
    from ..ai import AiConfigError, AiRequestError, chat_completion, codex_research_completion

    can_search = settings.get("mode") == "chatgpt" and chat_completion_func is chat_completion
    prompt = build_essay_review_prompt(
        question, materials, answer_text, can_search=can_search,
    )
    _update_job(db_path, job_id, "grading", 50,
                "正在检索范文并审阅作文…" if can_search else "正在审阅作文与材料…")
    run_state.reserve_model_call("essay_review", "联网研究与作文审阅" if can_search else "作文审阅")
    if can_search:
        try:
            response, raw = codex_research_completion(
                settings, prompt, {"thinking": "enabled" if options.get("deep_thinking") else "disabled"},
            )
        except (AiConfigError, AiRequestError) as error:
            can_search = False
            prompt = build_essay_review_prompt(question, materials, answer_text, can_search=False)
            prompt += f"\n【检索尝试结果】联网检索失败：{str(error)[:160]}。research_status 必须写明检索失败。\n"
            _update_job(db_path, job_id, "grading", 60, "联网检索未完成，正在依据题目与材料审阅…")
            run_state.reserve_model_call("essay_review_offline", "联网失败后离线审阅")
            response, raw = _call_grading_model(
                chat_completion_func, settings, prompt,
                bool(options.get("deep_thinking")), structured=True,
            )
    else:
        response, raw = _call_grading_model(
            chat_completion_func, settings, prompt,
            bool(options.get("deep_thinking")), structured=True,
        )
    raw_parts = [raw]
    _update_job(db_path, job_id, "validating", 85, "正在核对作文诊断、来源与原文引用…")
    try:
        review = validate_essay_review(json.loads(response), answer_text, can_search=can_search)
    except (ValueError, TypeError) as error:
        repair_prompt = (
            f"以下作文审阅 JSON 未通过校验：{error}。请重新核对我的原文与已访问的网页来源，"
            "仅修正格式、错误引文或来源，不得编造新网页。保持原任务要求。只输出完整 JSON 对象。\n\n"
            f"原任务：\n{prompt}\n\n待修复响应：\n{response}"
        )
        run_state.reserve_model_call("essay_review_repair", str(error))
        response, repair_raw = _call_grading_model(
            chat_completion_func, settings, repair_prompt, False, structured=True,
        )
        raw_parts.append(repair_raw)
        review = validate_essay_review(json.loads(response), answer_text, can_search=can_search)
    result = {"review_type": "essay_review", "review": review, "answer_snapshot": answer_text,
              "score_status": "not_scored"}
    report_text = render_essay_review(review)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """INSERT INTO grading_reports
               (attempt_id, provider, model, report_text, prompt_text, raw_response, status)
               VALUES (?, ?, ?, ?, ?, ?, 'ok')""",
            (attempt["id"], settings.get("provider_name") or "", settings.get("model") or "",
             report_text, prompt, "\n\n--- essay review call ---\n".join(raw_parts)),
        )
        report_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO grading_report_contexts
               (report_id, rubric_id, pipeline_version, retrieval_json, result_json,
                validation_json, rubric_snapshot_json, api_call_count, latency_ms)
               VALUES (?, NULL, ?, '[]', ?, ?, '{}', ?, ?)""",
            (report_id, PIPELINE_VERSION, json.dumps(result, ensure_ascii=False),
             json.dumps({"api_calls": run_state.api_call_audit}, ensure_ascii=False),
             run_state.api_calls, run_state.latency_ms()),
        )
    _update_job(db_path, job_id, "completed", 100, "大作文审阅完成。",
                report_id=report_id, retryable=0)
    return report_id


def run_grading_job(db_path, job_id, chat_completion_func):
    """Run the ordered grading state machine.

    Keeping status writes, the two-call retry budget, and final persistence in
    one visible sequence makes interrupted jobs auditable.
    """
    run_state = GradingRunState()
    raw_parts = run_state.raw_parts
    prompts = run_state.prompts
    logging.info("Smart grading job %s started", job_id)
    try:
        _update_job(db_path, job_id, "preparing", 8, "正在准备本题数据…")
        with connect(db_path) as conn:
            job, attempt, question, materials, references, settings, question_feedback, options = _load_job_context(
                conn, job_id
            )
        # A grading job is evaluated against the answer as it existed when the
        # user started the job, even if autosave changes the attempt meanwhile.
        attempt["answer_text"] = options.get("answer_snapshot", attempt.get("answer_text") or "")
        if question.get("question_type") == "综合写作":
            return _run_essay_review_job(
                db_path, job_id, chat_completion_func, run_state,
                (job, attempt, question, materials, references, settings, question_feedback, options),
            )
        ref_hash = reference_set_hash(references)
        source_hash = rubric_source_hash(question, materials, references)
        cached_row = None
        rubric = None
        reused = False
        with connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM grading_rubrics
                 WHERE question_id = ? AND reference_set_hash = ? AND source_hash = ?
                   AND rubric_version = ? AND status = 'ready'
              ORDER BY updated_at DESC LIMIT 1
                """,
                (question["id"], ref_hash, source_hash, RUBRIC_VERSION),
            ).fetchone()
            if row:
                cached_row = _row_dict(row)
                rubric = json.loads(row["rubric_json"])
                reused = True

        deep_thinking = bool(options.get("deep_thinking"))

        if reused:
            _update_job(db_path, job_id, "reusing_rubric", 42, "已复用评分基准，正在准备综合批改…")
            consensus = {}
        else:
            _update_job(db_path, job_id, "building_rubric", 20, "正在独立建立评分基准…")
            try:
                with connect(db_path) as conn:
                    consensus = compact_reference_consensus(conn, references, materials)
            except Exception as consensus_error:
                logging.warning(
                    "Smart grading job %s consensus preprocessing degraded: %s",
                    job_id,
                    consensus_error,
                )
                consensus = {
                    "embedding_model": FEATURE_HASH_MODEL,
                    "preprocessing_mode": "fallback",
                    "organization_count": len(references),
                    "clusters": [],
                    "degraded": True,
                    "error": str(consensus_error)[:300],
                }
            rubric_prompt = build_rubric_prompt(question, materials, references, consensus)
            prompts.append(rubric_prompt)
            run_state.reserve_model_call("rubric_generation", "首次建立可缓存评分基准")
            # 评分基准是结构化抽取且会缓存复用，这里强制关闭深度思考：思考模式下
            # max_tokens 会把推理+正文一起计，推理常吃掉 1.2万~1.5万 token 导致
            # 正文 JSON 被截断（finish_reason=length），进而触发修复调用挤掉压缩预算。
            # 非思考模式（实测修复调用即如此）能完整、稳定输出整套评分基准。
            rubric_response, rubric_raw = _call_grading_model(
                chat_completion_func,
                settings,
                rubric_prompt,
                False,
                structured=True,
            )
            raw_parts.append(rubric_raw)
            try:
                parsed_rubric = extract_tagged_json(rubric_response, "rubric_json")
                rubric = validate_rubric(
                    parsed_rubric,
                    question,
                    materials,
                    references,
                    question_feedback,
                )
            except Exception as validation_error:
                if not run_state.can_call():
                    raise
                repair_prompt = (
                    _repair_rubric_prompt(rubric_prompt, rubric_response, str(validation_error))
                    if isinstance(validation_error, ValueError)
                    and not isinstance(validation_error, json.JSONDecodeError)
                    else _repair_json_prompt(rubric_prompt, rubric_response, "rubric_json")
                )
                prompts.append(repair_prompt)
                run_state.reserve_model_call("rubric_repair", str(validation_error))
                rubric_response, rubric_raw = _call_grading_model(
                    chat_completion_func,
                    settings,
                    repair_prompt,
                    False,
                    structured=True,
                )
                raw_parts.append(rubric_raw)
                parsed_rubric = extract_tagged_json(rubric_response, "rubric_json")
                rubric = validate_rubric(
                    parsed_rubric,
                    question,
                    materials,
                    references,
                    question_feedback,
                )
            cached_row, rubric = _save_rubric_to_db(
                db_path,
                question,
                references,
                materials,
                settings,
                question_feedback,
                parsed_rubric,
                consensus,
            )
            _update_job(db_path, job_id, "building_rubric", 38, "评分基准已建立，正在整理检索证据…")

        _update_job(db_path, job_id, "retrieving", 46, "正在检索本题相关训练证据…")
        try:
            with connect(db_path) as conn:
                evidence, history_meta = retrieve_grading_evidence(conn, question, attempt, rubric, options)
        except Exception as retrieval_error:
            evidence = []
            history_meta = {
                "history_attempt_count": 0,
                "history_stable": False,
                "retrieval_degraded": True,
                "retrieval_error": str(retrieval_error)[:300],
            }

        _update_job(
            db_path,
            job_id,
            "grading",
            58,
            "已连接 AI，正在完成采分点分析与综合评分…",
        )
        prompt = build_grading_prompt(
            question,
            materials,
            attempt,
            rubric,
            evidence,
            options.get("custom_reference_answer") or "",
            history_meta,
            question_feedback,
            references,
        )
        prompts.append(prompt)
        try:
            run_state.reserve_model_call("grading", "正式结构化批改")
            response, raw = _call_grading_model(
                chat_completion_func,
                settings,
                prompt,
                deep_thinking,
                structured=True,
            )
            raw_parts.append(raw)
        except Exception:
            if not run_state.can_call():
                raise
            run_state.reserve_model_call("request_retry", "正式批改请求失败后重试")
            response, raw = _call_grading_model(
                chat_completion_func,
                settings,
                prompt,
                deep_thinking,
                structured=True,
            )
            raw_parts.append(raw)

        calibration_policy = load_calibration_policy()

        def parse_and_validate(candidate_response, reviewed=False, original_evaluation=None):
            _, evaluation = _smart_response_parts(candidate_response, expects_rubric=False)
            if original_evaluation is not None:
                evaluation = _merge_review_evaluation(original_evaluation, evaluation)
            candidate_result = validate_grading_result(
                evaluation,
                rubric,
                attempt.get("answer_text") or "",
                evidence,
                calibration_policy=calibration_policy,
                reviewed=reviewed,
                require_structure_review=True,
            )
            return evaluation, candidate_result

        _update_job(db_path, job_id, "validating", 82, "正在校验权重、引用与综合维度分…")
        try:
            evaluation, result = parse_and_validate(response)
        except Exception as validation_error:
            if not run_state.can_call():
                raise
            repair_prompt = _repair_smart_response_prompt(prompt, response, str(validation_error))
            run_state.reserve_model_call("schema_repair", str(validation_error))
            response, repair_raw = _call_grading_model(
                chat_completion_func,
                settings,
                repair_prompt,
                False,
                structured=True,
            )
            raw_parts.append(repair_raw)
            evaluation, result = parse_and_validate(response)

        if result.get("review", {}).get("triggered") and run_state.can_call():
            _update_job(db_path, job_id, "validating", 87, "发现关键证据冲突，正在独立复核…")
            review_prompt = _build_review_prompt(
                question,
                rubric,
                attempt.get("answer_text") or "",
                result,
            )
            prompts.append(review_prompt)
            review_reasons = result.get("review", {}).get("reasons") or []
            run_state.reserve_model_call(
                "independent_review",
                "；".join(str(item) for item in review_reasons) or "评分证据触发独立复核",
            )
            review_response, review_raw = _call_grading_model(
                chat_completion_func,
                settings,
                review_prompt,
                False,
                structured=True,
            )
            raw_parts.append(review_raw)
            evaluation, result = parse_and_validate(
                review_response,
                reviewed=True,
                original_evaluation=evaluation,
            )
        effective_word_limit = question_word_limit_text(question)
        result["word_limit"] = effective_word_limit
        result["revised_answer"] = compact_revised_answer_linebreaks(
            result.get("revised_answer") or "",
            effective_word_limit,
        )
        result["answer_snapshot"] = attempt.get("answer_text") or ""
        report_text = render_grading_report(result, rubric, evidence)
        report_text = normalize_revised_answer_word_count(report_text, effective_word_limit)
        original_answer = attempt.get("answer_text") or ""
        if revised_answer_overwrites_original(original_answer, result.get("revised_answer")) and run_state.can_call():
            _update_job(db_path, job_id, "repairing_answer", 90, "修改稿改写过多，正在贴着原答案局部修订…")
            local_prompt = build_revised_answer_local_edit_prompt(
                original_answer,
                result["revised_answer"],
                result.get("optimization_suggestions"),
                effective_word_limit,
            )
            run_state.reserve_model_call("revised_answer_local_edit", "修改稿改写原答案过多")
            try:
                local_response, local_raw = _call_grading_model(
                    chat_completion_func, settings, local_prompt, False
                )
                raw_parts.append(local_raw)
                localized_answer = compact_revised_answer_linebreaks(
                    parse_revised_answer_repair(local_response), effective_word_limit
                )
                if localized_answer and not revised_answer_overwrites_original(original_answer, localized_answer):
                    localized_report = replace_revised_answer_body(
                        report_text, localized_answer, effective_word_limit
                    )
                    localized_status = revised_answer_word_count_status(localized_report, effective_word_limit)
                    if localized_status["over_limit"] and run_state.can_call():
                        _update_job(db_path, job_id, "repairing_answer", 93, "原文修订已完成，正在压缩到字数限制内…")
                        retry_prompt = build_revised_answer_retry_prompt(
                            prompt, localized_report, effective_word_limit
                        )
                        run_state.reserve_model_call(
                            "revised_answer_local_compression",
                            f"原文修订超过字数硬上限 {localized_status['over_by']} 字",
                        )
                        retry_response, retry_raw = _call_grading_model(
                            chat_completion_func, settings, retry_prompt, False
                        )
                        raw_parts.append(retry_raw)
                        compressed_answer = compact_revised_answer_linebreaks(
                            parse_revised_answer_repair(retry_response), effective_word_limit
                        )
                        if compressed_answer and not revised_answer_overwrites_original(
                            original_answer, compressed_answer
                        ):
                            compressed_report = replace_revised_answer_body(
                                report_text, compressed_answer, effective_word_limit
                            )
                            if not revised_answer_word_count_status(
                                compressed_report, effective_word_limit
                            )["over_limit"]:
                                localized_answer = compressed_answer
                                localized_report = compressed_report
                    # Keep a faithful local revision even if compression fails.
                    # The report's word-count warning makes the remaining limit
                    # visible instead of hiding the edits behind a global rewrite.
                    result["revised_answer"] = localized_answer
                    report_text = localized_report
            except Exception:
                logging.exception("Could not localize the revised answer; keeping the original report draft")
        # 压缩重试在调用预算内循环：单次压缩常常只减掉一部分（例如超 89 格压到仍超 62 格），
        # 只试一次会把超限的修改稿原样存进报告。只要还有预算且上一轮确有进展就继续压。
        status = revised_answer_word_count_status(report_text, effective_word_limit)
        while status["over_limit"] and run_state.can_call():
            _update_job(db_path, job_id, "repairing_answer", 93, "修改版答案超出硬限制，正在局部压缩…")
            retry_prompt = build_revised_answer_retry_prompt(prompt, report_text, effective_word_limit)
            run_state.reserve_model_call(
                "revised_answer_compression",
                f"修改版答案超过字数硬上限 {status['over_by']} 字",
            )
            repair_response, repair_raw = _call_grading_model(
                chat_completion_func, settings, retry_prompt, False
            )
            raw_parts.append(repair_raw)
            repaired_answer = compact_revised_answer_linebreaks(
                parse_revised_answer_repair(repair_response), effective_word_limit
            )
            if not repaired_answer:
                break
            result["revised_answer"] = repaired_answer
            report_text = replace_revised_answer_body(report_text, repaired_answer, effective_word_limit)
            next_status = revised_answer_word_count_status(report_text, effective_word_limit)
            if next_status["over_by"] >= status["over_by"]:
                # 压缩没有实际进展（模型原样返回或更差），停止以免浪费预算。
                status = next_status
                break
            status = next_status
        status = revised_answer_word_count_status(report_text, effective_word_limit)
        latency_ms = run_state.latency_ms()
        validation = {
            "errors": result.get("validation_errors") or [],
            "score": result.get("score"),
            "score_status": result.get("score_status"),
            "review": result.get("review") or {},
            "score_calibration": result.get("score_calibration") or {},
            "history_meta": history_meta,
            "deep_thinking": deep_thinking,
            "word_count_status": status,
            "api_calls": run_state.api_call_audit,
        }
        with connect(db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO grading_reports (
                    attempt_id, provider, model, report_text, prompt_text, raw_response, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'ok')
                """,
                (
                    attempt["id"],
                    settings.get("provider_name") or "",
                    settings.get("model") or "",
                    report_text,
                    prompt,
                    "\n\n--- smart grading call ---\n".join(raw_parts),
                ),
            )
            report_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO grading_report_contexts (
                    report_id, rubric_id, pipeline_version, retrieval_json, result_json,
                    validation_json, rubric_snapshot_json, api_call_count, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    cached_row.get("id") if cached_row else None,
                    PIPELINE_VERSION,
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    json.dumps(rubric, ensure_ascii=False),
                    run_state.api_calls,
                    latency_ms,
                ),
            )
            ingest_report_normative_expressions(conn, report_id)
        completed_message = (
            f"智能批改完成，修改版答案超出字数限制 {status['over_by']} 字。"
            if status["over_limit"]
            else (
                "智能批改已生成待复核结果。"
                if result.get("score_status") == "provisional"
                else "智能批改完成。"
            )
        )
        _update_job(db_path, job_id, "completed", 100, completed_message, report_id=report_id, retryable=0)
        logging.info(
            "Smart grading job %s completed in %sms with %s API call(s)",
            job_id,
            latency_ms,
            run_state.api_calls,
        )
        return report_id
    except Exception as exc:
        logging.exception("Smart grading job %s failed", job_id)
        category = classify_grading_error(exc)
        _update_job(
            db_path,
            job_id,
            "failed",
            100,
            f"智能批改失败（{category.value}）。",
            error_text=f"{category.value}: {str(exc)[:950]}",
            retryable=1,
        )
        return None


def create_grading_job(
    conn,
    attempt,
    settings,
    reference_ids,
    custom_answer,
    options: GradingJobOptions | dict | None = None,
):
    options = dict(options or {})
    options.update(
        {
            "reference_ids": sorted({int(value) for value in reference_ids}),
            "custom_reference_answer": custom_answer or "",
            "analogies": bool(options.get("analogies", True)),
            "knowledge": bool(options.get("knowledge", True)),
            "history": bool(options.get("history", True)),
            "deep_thinking": bool(options.get("deep_thinking", False)),
            "answer_snapshot": _row_dict(attempt).get("answer_text") or "",
        }
    )
    active = conn.execute(
        f"SELECT * FROM grading_jobs WHERE attempt_id = ? AND status IN ({','.join('?' for _ in ACTIVE_JOB_STATUSES)}) ORDER BY id DESC LIMIT 1",
        (attempt["id"], *ACTIVE_JOB_STATUSES),
    ).fetchone()
    if active:
        return _row_dict(active), False
    input_hash = grading_input_hash(
        _row_dict(attempt),
        options["reference_ids"],
        custom_answer,
        {key: options[key] for key in ("analogies", "knowledge", "history", "deep_thinking")},
        settings["model"] if settings else "",
    )
    cursor = conn.execute(
        """
        INSERT INTO grading_jobs (attempt_id, input_hash, status, progress, message, options_json)
        VALUES (?, ?, 'queued', 0, '等待开始智能批改…', ?)
        """,
        (attempt["id"], input_hash, json.dumps(options, ensure_ascii=False)),
    )
    return _row_dict(conn.execute("SELECT * FROM grading_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()), True


def start_grading_job(db_path, job_id, chat_completion_func):
    thread = threading.Thread(
        target=run_grading_job,
        args=(db_path, int(job_id), chat_completion_func),
        name=f"grading-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return thread


def grading_job_payload(row):
    row = _row_dict(row)
    try:
        preview = json.loads(row.get("options_json") or "{}").get("_failed_preview") or {}
    except (TypeError, json.JSONDecodeError):
        preview = {}
    return {
        "job_id": row.get("id"),
        "attempt_id": row.get("attempt_id"),
        "status": row.get("status"),
        "progress": row.get("progress") or 0,
        "message": row.get("message") or "",
        "error": row.get("error_text") or "",
        "report_id": row.get("report_id"),
        "retryable": bool(row.get("retryable")),
        "preview_available": bool(preview.get("report_text")),
        "preview_anchor": f"grading-preview-{row.get('id')}",
        "complete": row.get("status") in {"completed", "failed", "interrupted"},
    }


def rubric_cache_status(conn, question_id, references, question, materials):
    ref_hash = reference_set_hash(references)
    source_hash = rubric_source_hash(question, materials, references)
    row = conn.execute(
        """
        SELECT id, updated_at FROM grading_rubrics
         WHERE question_id = ? AND reference_set_hash = ? AND source_hash = ?
           AND rubric_version = ? AND status = 'ready'
      ORDER BY updated_at DESC LIMIT 1
        """,
        (question_id, ref_hash, source_hash, RUBRIC_VERSION),
    ).fetchone()
    return {
        "cached": bool(row),
        "rubric_id": row["id"] if row else None,
        "updated_at": row["updated_at"] if row else None,
    }


def apply_report_feedback(conn, report_id, point_key, corrected_status, corrected_quote="", note="", scope="report"):
    report = conn.execute(
        """
        SELECT gr.*, a.question_id, a.answer_text
          FROM grading_reports gr JOIN attempts a ON a.id = gr.attempt_id
         WHERE gr.id = ?
        """,
        (report_id,),
    ).fetchone()
    context = conn.execute("SELECT * FROM grading_report_contexts WHERE report_id = ?", (report_id,)).fetchone()
    if not report or not context:
        raise ValueError("该报告不是可逐点纠错的智能批改报告")
    corrected_status = corrected_status if corrected_status in {"hit", "partial", "miss", "invalid"} else ""
    scope = "question" if scope == "question" else "report"
    if corrected_status in {"hit", "partial"} and (not corrected_quote or corrected_quote not in report["answer_text"]):
        raise ValueError("命中或部分命中时，修正引用必须是用户答案中的连续原文")
    conn.execute(
        """
        INSERT INTO grading_feedback (
            report_id, attempt_id, question_id, point_key, scope,
            corrected_status, corrected_quote, note, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(report_id, point_key, scope) DO UPDATE SET
            corrected_status = excluded.corrected_status,
            corrected_quote = excluded.corrected_quote,
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            report_id,
            report["attempt_id"],
            report["question_id"],
            point_key,
            scope,
            corrected_status,
            corrected_quote or "",
            note or "",
        ),
    )
    rubric_row = conn.execute("SELECT * FROM grading_rubrics WHERE id = ?", (context["rubric_id"],)).fetchone()
    rubric_json = rubric_row["rubric_json"] if rubric_row else context["rubric_snapshot_json"]
    rubric = json.loads(rubric_json or "{}")
    if not rubric.get("points"):
        raise ValueError("评分基准快照已不存在")
    result = json.loads(context["result_json"])
    evidence = json.loads(context["retrieval_json"] or "[]")
    for match in result.get("point_matches", []):
        if match.get("point_key") != point_key:
            continue
        match["status"] = corrected_status
        match["coverage_ratio"] = 1.0 if corrected_status == "hit" else (0.5 if corrected_status == "partial" else 0.0)
        match["answer_quote"] = corrected_quote if corrected_status in {"hit", "partial"} else ""
        match["feedback_applied"] = True
        match["feedback_note"] = _clean(note, 240)
        break
    result["score_status"] = "stale"
    result["feedback_applied"] = True
    report_text = render_grading_report(result, rubric, evidence)
    question = conn.execute("SELECT word_limit FROM questions WHERE id = ?", (report["question_id"],)).fetchone()
    report_text = normalize_revised_answer_word_count(report_text, question["word_limit"] if question else "")
    conn.execute("UPDATE grading_reports SET report_text = ? WHERE id = ?", (report_text, report_id))
    conn.execute(
        "UPDATE grading_report_contexts SET result_json = ?, validation_json = ? WHERE report_id = ?",
        (
            json.dumps(result, ensure_ascii=False),
            json.dumps(
                {
                    "feedback_applied": True,
                    "score_status": "stale",
                    "previous_score": result.get("score"),
                    "errors": result.get("validation_errors") or [],
                },
                ensure_ascii=False,
            ),
            report_id,
        ),
    )
    ingest_report_normative_expressions(conn, report_id, force=True)
    return result


def invalidate_question_rubrics(conn, question_id):
    cursor = conn.execute(
        "UPDATE grading_rubrics SET status = 'stale', updated_at = CURRENT_TIMESTAMP WHERE question_id = ? AND status = 'ready'",
        (int(question_id),),
    )
    return cursor.rowcount
