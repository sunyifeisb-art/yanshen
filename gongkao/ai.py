import json
import os
import shutil
import subprocess
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class AiConfigError(Exception):
    pass


class AiRequestError(Exception):
    pass


def masked_key(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def resolve_api_key(settings):
    inline_key = (settings["api_key"] or "").strip()
    if inline_key:
        return inline_key
    env_name = (settings["api_key_env"] or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def build_chat_url(base_url):
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    parsed = urlparse(base)
    if parsed.netloc.endswith("api.deepseek.com"):
        return base + "/chat/completions"
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


WORKBUDDY_MODE = "workbuddy"


def workbuddy_chat_completion(settings, prompt, request_options=None):
    """WorkBuddy 账号模式：复用 DSH 插件已登录的账号池，走 OpenAI 兼容上游。"""

    from . import workbuddy

    try:
        return workbuddy.chat_completion(settings, prompt, request_options)
    except workbuddy.WorkbuddyError as error:
        # 统一向上层抛 AiRequestError，批改链路的重试/降级逻辑据此处理
        raise AiRequestError(str(error)) from error


def _setting_value(settings, key, default=""):
    """settings 可能是 dict 也可能是 sqlite3.Row；Row 没有 .get，统一安全读取。"""

    if settings is None:
        return default
    try:
        value = settings[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def chat_completion(settings, prompt, request_options=None):
    mode = _setting_value(settings, "mode")
    if mode == "chatgpt":
        return codex_chat_completion(settings, prompt, request_options)
    if mode == WORKBUDDY_MODE:
        return workbuddy_chat_completion(settings, prompt, request_options)
    api_key = resolve_api_key(settings)
    if not api_key:
        raise AiConfigError("未找到 API key。请在设置页填写 API key，或设置对应环境变量。")

    base_url = (settings["api_base_url"] or "").strip().rstrip("/")
    if not base_url:
        raise AiConfigError("API Base URL 不能为空。")
    url = build_chat_url(base_url)
    model = (settings["model"] or "").strip()
    if not model:
        raise AiConfigError("模型名不能为空。")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业、严格、可操作的申论批改老师。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(settings["temperature"] or 0.2),
    }
    request_options = request_options or {}
    thinking_type = request_options.get("thinking")
    api_host = (urlparse(base_url).hostname or "").lower()
    if (
        thinking_type in {"enabled", "disabled"}
        and api_host == "api.deepseek.com"
        and model.startswith("deepseek-v4")
    ):
        payload["thinking"] = {"type": thinking_type}
    response_format = request_options.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") == "json_object":
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] += (
            " 当前任务要求 JSON 输出：最终内容必须是单个合法 JSON 对象，"
            "不要输出 Markdown、XML 标签或任何额外文字。"
        )
    max_tokens = request_options.get("max_tokens")
    if isinstance(max_tokens, int) and 1 <= max_tokens <= 384000:
        payload["max_tokens"] = max_tokens
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AiRequestError(f"API 请求失败：HTTP {exc.code}。{detail[:500]}") from exc
    except URLError as exc:
        raise AiRequestError(f"API 连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AiRequestError("API 请求超时，请稍后重试或换用 Codex 手动模式。") from exc

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AiRequestError("API 返回格式无法解析，请检查服务商是否兼容 OpenAI chat completions。") from exc
    return content, raw


# ---------------------------------------------------------------------------
# ChatGPT / Codex 账号模式：复用本机 Codex CLI（ChatGPT 桌面应用自带）的
# ChatGPT OAuth 登录，无需 API Key。认证归 ~/.codex/auth.json 所有，codex
# 二进制负责令牌刷新；这里只做一次 exec 调用并取回最后一条消息。
# ---------------------------------------------------------------------------

CODEX_HOME_DIR = os.path.join(os.path.expanduser("~"), ".codex")
CODEX_BINARY_CANDIDATES = [
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
]
CODEX_FALLBACK_MODELS = [
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
]
CODEX_EXEC_TIMEOUT_SECONDS = 300
CODEX_RESEARCH_TIMEOUT_SECONDS = 900
CODEX_DEFAULT_MODEL = "gpt-6-luna"


def find_codex_binary():
    for candidate in CODEX_BINARY_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("codex") or ""


def codex_auth_status():
    """Return whether the local ChatGPT OAuth login is usable."""
    status = {"logged_in": False, "auth_mode": "", "last_refresh": "", "error": ""}
    try:
        with open(os.path.join(CODEX_HOME_DIR, "auth.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mode = str(data.get("auth_mode") or "")
        tokens = data.get("tokens") or {}
        status.update(
            {
                "auth_mode": mode,
                "logged_in": bool(mode == "chatgpt" and tokens),
                "last_refresh": str(data.get("last_refresh") or "")[:19],
            }
        )
    except FileNotFoundError:
        status["error"] = "未找到 ~/.codex/auth.json，请先在设置页登录 ChatGPT。"
    except (OSError, ValueError, TypeError) as exc:
        status["error"] = f"读取登录状态失败：{exc}"
    return status


CODEX_AGENTIC_MODEL_PREFIXES = ("codex-",)


def _codex_model_is_agentic(slug):
    """Classify a model as the agentic coding tier (sol/terra/luna/auto-review) vs
    the general GPT tier (gpt-5.x/gpt-6.x). The ChatGPT account can call both via the Codex
    CLI; the split only helps the user pick 普通 GPT 聊天 还是 Codex 编码模型."""
    return (
        slug.startswith(CODEX_AGENTIC_MODEL_PREFIXES)
        or slug in {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
    )


def codex_model_groups():
    """Return {"gpt": [...], "codex": [...]} model slugs, reading the local
    Codex model catalog first, then a static fallback list."""
    slugs = None
    try:
        with open(os.path.join(CODEX_HOME_DIR, "models_cache.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        slugs = [str(model.get("slug")) for model in data.get("models", []) if model.get("slug")]
    except (OSError, ValueError, TypeError):
        pass
    if not slugs:
        slugs = list(CODEX_FALLBACK_MODELS)
    gpt = [slug for slug in slugs if not _codex_model_is_agentic(slug)]
    codex = [slug for slug in slugs if _codex_model_is_agentic(slug)]
    return {"gpt": gpt, "codex": codex}


def codex_available_models():
    groups = codex_model_groups()
    return groups["gpt"] + groups["codex"]


def codex_login():
    """Open the Codex browser login flow (non-blocking)."""
    binary = find_codex_binary()
    if not binary:
        return False, "未找到 codex 二进制，请先安装 ChatGPT 桌面应用或 Codex CLI。"
    try:
        subprocess.Popen(
            [binary, "login"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, "已在浏览器打开 Codex 登录页，完成后请回到设置页刷新状态。"
    except OSError as exc:
        return False, f"启动登录失败：{exc}"


def _codex_reasoning_effort(request_options):
    thinking = (request_options or {}).get("thinking")
    if thinking == "enabled":
        return "high"
    if thinking == "disabled":
        return "low"
    return "medium"


def build_codex_exec_command(binary, *, model="", reasoning="medium", enable_search=False):
    """Build the shared non-interactive Codex command.

    `--search` is a global Codex CLI flag, so it must appear before the
    `exec` subcommand.  Keeping this construction in one helper prevents the
    research path from drifting away from the existing ChatGPT path.
    """
    cmd = [binary]
    if enable_search:
        cmd.append("--search")
    cmd += [
        "exec",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
    ]
    if model:
        cmd += ["-m", model]
    if reasoning:
        cmd += ["-c", f"model_reasoning_effort={reasoning}"]
    return cmd


def _codex_completion(
    settings,
    prompt,
    request_options=None,
    *,
    enable_search=False,
    progress_callback=None,
    timeout_seconds=None,
):
    binary = find_codex_binary()
    if not binary:
        raise AiConfigError(
            "未找到 codex 二进制。请安装 ChatGPT 桌面应用或 Codex CLI，"
            "然后在设置页切换到 ChatGPT 模式。"
        )
    auth = codex_auth_status()
    if not auth.get("logged_in"):
        raise AiRequestError(
            "ChatGPT 账号未登录。请在设置页点击「登录」，或先打开 ChatGPT/Codex 应用完成登录。"
        )
    model = str(settings.get("model") or "").strip() or ""
    reasoning = _codex_reasoning_effort(request_options)
    timeout_seconds = int(timeout_seconds or CODEX_EXEC_TIMEOUT_SECONDS)
    cmd = build_codex_exec_command(
        binary,
        model=model,
        reasoning=reasoning,
        enable_search=enable_search,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="yanshen-codex-") as workdir:
            out_file = os.path.join(workdir, "out.txt")
            if progress_callback is not None:
                cmd.append("--json")
            cmd += ["-o", out_file, "-"]
            if progress_callback is None:
                proc = subprocess.run(
                    cmd,
                    input=prompt.encode("utf-8"),
                    capture_output=True,
                    timeout=timeout_seconds,
                    cwd=workdir,
                )
                return_code = proc.returncode
                detail = proc.stderr.decode("utf-8", errors="replace").strip()
            else:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=workdir,
                )
                output_lines = []

                def read_events():
                    if proc.stdout is None:
                        return
                    for line in proc.stdout:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        output_lines.append(stripped)
                        try:
                            event = json.loads(stripped)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            continue
                        if not isinstance(event, dict):
                            continue
                        try:
                            progress_callback(event)
                        except Exception:
                            # UI progress reporting must never break the actual AI request.
                            continue

                reader = threading.Thread(target=read_events, name="yanshen-codex-progress", daemon=True)
                reader.start()
                if proc.stdin is not None:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                try:
                    return_code = proc.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    reader.join(timeout=2)
                    raise
                reader.join(timeout=2)
                detail = "\n".join(output_lines[-80:])

            if return_code != 0:
                if "usage limit" in detail.lower() or "upgrade to pro" in detail.lower():
                    raise AiRequestError(
                        "ChatGPT/Codex 用量已达到上限，请到 https://chatgpt.com/codex/settings/usage "
                        "查看额度窗口，或换用 API 模式。"
                    )
                raise AiRequestError(f"Codex 调用失败（退出码 {return_code}）：{detail[-600:]}")
            if not os.path.exists(out_file):
                raise AiRequestError("Codex 未返回内容，请重试。")
            with open(out_file, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
            if not content:
                raise AiRequestError("Codex 返回了空内容，请重试。")
            return content, content
    except subprocess.TimeoutExpired as exc:
        if enable_search:
            raise AiRequestError(
                "Codex 联网研究在规定时间内未完成。可能与联网检索、官方来源响应、题量或服务端负载有关，请稍后重试。"
            ) from exc
        raise AiRequestError("Codex 请求超时，请稍后重试。") from exc


def codex_chat_completion(settings, prompt, request_options=None):
    return _codex_completion(settings, prompt, request_options, enable_search=False)


def codex_research_completion(settings, prompt, request_options=None, progress_callback=None):
    """Run Codex with native web search enabled for freshness-sensitive work."""
    return _codex_completion(
        settings,
        prompt,
        request_options,
        enable_search=True,
        progress_callback=progress_callback,
        timeout_seconds=CODEX_RESEARCH_TIMEOUT_SECONDS,
    )
