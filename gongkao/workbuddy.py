"""WorkBuddy 账号池接入。

复用本机 DSH（DeepSeek Harness）的 workbuddy 连接插件已经登录好的账号凭据，
直接调用上游 OpenAI 兼容接口，无需 API Key。凭据归 DSH 的 vault 所有：

    ~/.dsh/workbuddy-vault/cn/<accountId>.json

单个凭据文件的形态（只读敏感字段）：

    {
      "accountId": "...",
      "auth": {"accessToken": ..., "refreshToken": ..., "expiresAt": ...,
               "refreshExpiresAt": ..., "domain": "www.codebuddy.cn"},
      "account": {"uid": "...", "nickname": "...", "uin": "..."}
    }

上游约定（实测）：

* `POST {chat_base}/v2/chat/completions`，Bearer accessToken；
* 只接受 `stream: true`，非流式直接返回 400 / 11101；
* 必须带会话三件套请求头 X-Conversation-ID / -Request-ID / -Message-ID，
  以及 X-User-Id / X-No-Enterprise-Id / X-Domain / X-Product；
* accessToken 过期时用 `/v2/plugin/auth/token/refresh` + X-Refresh-Token 换新，
  新凭据写回 vault（与插件共用同一份文件）。

DSH 插件不在本进程内，所以刷新后必须落盘，否则下次启动又会拿到过期 token。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-v4.1-flash"
# 仅用于 ai_settings.api_base_url 的展示/落库占位；实际 base 由凭据区域决定（见 chat_base）。
UPSTREAM_BASE_URL = "https://copilot.tencent.com"

# 区域 -> (chat base, 默认 domain)。凭据里的 auth.domain 优先。
REGION_BASES = {
    "cn": ("https://copilot.tencent.com", "www.codebuddy.cn"),
    "global": ("https://copilot.workbuddy.ai", "www.workbuddy.ai"),
}

CN_MODELS = [
    ("deepseek-v4.1-flash", "Deepseek-V4.1-Flash"),
    ("deepseek-v4-flash", "Deepseek-V4-Flash"),
    ("deepseek-v4-pro", "Deepseek-V4-Pro"),
    ("glm-5.3", "GLM-5.3"),
    ("glm-5.3-flash", "GLM-5.3-Flash"),
    ("kimi-k3-1", "Kimi-K3"),
    ("kimi-k3.1-pro", "Kimi-K3.1-Pro"),
    ("minimax-m3", "MiniMax-M3"),
    ("hy3", "Hy3"),
]

CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"
CHAT_TIMEOUT = 180
REFRESH_TIMEOUT = 45
REFRESH_MARGIN_SECONDS = 120

_REFRESH_LOCK = threading.Lock()


class WorkbuddyError(Exception):
    """WorkBuddy 调用失败（凭据缺失、过期、上游报错等）。"""


# ---------------------------------------------------------------------------
# 凭据发现
# ---------------------------------------------------------------------------


def vault_dir() -> Path:
    override = os.environ.get("WORKBUDDY_VAULT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".dsh" / "workbuddy-vault"


def _region_from_dir(path: Path) -> str:
    return "global" if path.name == "global" else "cn"


def _iter_credential_files():
    root = vault_dir()
    if not root.is_dir():
        return
    for region_dir in sorted(root.iterdir()):
        if not region_dir.is_dir() or region_dir.name.startswith("."):
            continue
        for item in sorted(region_dir.glob("*.json")):
            if item.name.startswith("."):
                continue
            yield region_dir.name, item


def _read_credential(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbuddyError(f"凭据文件无法读取：{path.name}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise WorkbuddyError(f"凭据文件格式异常：{path.name}")
    # 少数版本写在顶层，做一次兼容收敛
    auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else payload
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    access_token = str(auth.get("accessToken") or "").strip()
    if not access_token:
        raise WorkbuddyError(f"凭据缺少 accessToken：{path.name}")
    return {
        "path": path,
        "account_id": str(payload.get("accountId") or path.stem),
        "access_token": access_token,
        "refresh_token": str(auth.get("refreshToken") or "").strip(),
        "expires_at": int(auth.get("expiresAt") or 0),
        "refresh_expires_at": int(auth.get("refreshExpiresAt") or 0),
        "domain": str(auth.get("domain") or "").strip(),
        "uid": str(account.get("uid") or "").strip(),
        "nickname": str(account.get("nickname") or "").strip(),
        "uin": str(account.get("uin") or "").strip(),
        "raw": payload,
    }


def list_accounts() -> list[dict]:
    """列出全部已登录账号（按昵称排序），失败的单条跳过并记日志。"""

    accounts = []
    for region, path in _iter_credential_files():
        try:
            credential = _read_credential(path)
        except WorkbuddyError as exc:
            logger.warning("workbuddy 凭据跳过：%s", exc)
            continue
        credential["region"] = region
        credential["label"] = credential["nickname"] or credential["uin"] or credential["account_id"]
        accounts.append(credential)
    accounts.sort(key=lambda item: (item["region"] != "cn", item["label"]))
    return accounts


def account_public_view(item: dict) -> dict:
    return {
        "account_id": item["account_id"],
        "label": item["label"],
        "nickname": item["nickname"],
        "uin": item["uin"],
        "region": item["region"],
        "expires_at": item["expires_at"],
    }


def find_account(account_id: str = "") -> dict:
    """按 accountId 找账号；未指定或找不到时回退到第一个可用账号。"""

    accounts = list_accounts()
    if not accounts:
        raise WorkbuddyError(
            "未找到 WorkBuddy 账号。请先在 DSH 的 workbuddy 插件里登录账号，"
            f"凭据目录：{vault_dir()}"
        )
    target = (account_id or "").strip()
    if target:
        for item in accounts:
            if target in (item["account_id"], item["label"], item["nickname"], item["uid"], item["uin"]):
                return item
        logger.warning("workbuddy 指定账号 %s 不存在，回退到默认账号", target)
    return accounts[0]


def is_ready() -> bool:
    """设置页/批改页判断 WorkBuddy 是否可用：凭据目录里有可用账号即可。"""

    try:
        return bool(list_accounts())
    except Exception:
        return False


def chat_base(credential: dict) -> str:
    region = credential.get("region") or "cn"
    default_base, _ = REGION_BASES.get(region, REGION_BASES["cn"])
    return default_base


def origin(credential: dict) -> str:
    domain = credential.get("domain") or ""
    if domain:
        return f"https://{domain}"
    _, default_domain = REGION_BASES.get(credential.get("region") or "cn", REGION_BASES["cn"])
    return f"https://{default_domain}"


# ---------------------------------------------------------------------------
# 请求头
# ---------------------------------------------------------------------------


def _common_headers(credential: dict) -> dict:
    site = origin(credential)
    return {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": site,
        "Referer": f"{site}/",
        "User-Agent": CLIENT_UA,
    }


def _chat_headers(credential: dict) -> dict:
    headers = _common_headers(credential)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "text/event-stream"
    token = credential.get("access_token") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    uid = credential.get("uid") or ""
    headers["X-User-Id" if uid else "X-No-User-Id"] = uid or "1"
    headers["X-No-Enterprise-Id"] = "1"
    domain = credential.get("domain") or ""
    headers["X-Domain" if domain else "X-No-Department-Info"] = domain or "1"
    headers["X-Product"] = "SaaS"
    # 会话三件套：上游要求同时存在，值只做关联用，每次请求重新生成
    headers["X-Conversation-ID"] = str(uuid.uuid4())
    headers["X-Conversation-Request-ID"] = str(uuid.uuid4())
    headers["X-Conversation-Message-ID"] = str(uuid.uuid4())
    return headers


def _refresh_headers(credential: dict) -> dict:
    headers = _common_headers(credential)
    headers["X-Refresh-Token"] = credential.get("refresh_token") or ""
    headers["X-Auth-Refresh-Source"] = "workbuddy"
    return headers


# ---------------------------------------------------------------------------
# Token 刷新（写回 vault，与 DSH 插件共用）
# ---------------------------------------------------------------------------


def _needs_refresh(credential: dict, force: bool = False) -> bool:
    if force:
        return bool(credential.get("refresh_token"))
    expires_at = int(credential.get("expires_at") or 0)
    if not expires_at:
        return False
    return time.time() >= expires_at - REFRESH_MARGIN_SECONDS


def refresh_credential(credential: dict) -> dict:
    """用 refreshToken 换新 accessToken，并把新凭据写回 vault 文件。"""

    if not credential.get("refresh_token"):
        raise WorkbuddyError("凭据没有 refreshToken，无法自动续期，请在 DSH 里重新登录该账号。")
    url = f"{chat_base(credential)}/v2/plugin/auth/token/refresh"
    request = Request(url, data=b"", headers=_refresh_headers(credential), method="POST")
    try:
        with urlopen(request, timeout=REFRESH_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WorkbuddyError(
            f"WorkBuddy 账号续期失败：HTTP {exc.code}。{detail[:300]}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise WorkbuddyError(f"WorkBuddy 账号续期请求失败：{exc}") from exc

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WorkbuddyError("WorkBuddy 续期返回无法解析。") from exc
    if int(envelope.get("code") or 0) != 0:
        raise WorkbuddyError(
            f"WorkBuddy 账号续期被拒绝：{envelope.get('msg') or '未知原因'}。"
            "请在 DSH 里重新登录该账号。"
        )
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    access_token = str(data.get("accessToken") or "").strip()
    if not access_token:
        raise WorkbuddyError("WorkBuddy 账号续期未返回 accessToken，请重新登录该账号。")

    now = int(time.time())
    raw = credential["raw"]
    auth = raw.get("auth") if isinstance(raw.get("auth"), dict) else raw
    auth["accessToken"] = access_token
    refresh_token = str(data.get("refreshToken") or "").strip()
    if refresh_token:
        auth["refreshToken"] = refresh_token
        credential["refresh_token"] = refresh_token
    expires_in = data.get("expiresIn")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        auth["expiresAt"] = now + int(expires_in)
    domain = str(data.get("domain") or "").strip()
    if domain:
        auth["domain"] = domain
        credential["domain"] = domain
    raw["refreshedAtMs"] = int(time.time() * 1000)

    credential["access_token"] = access_token
    credential["expires_at"] = int(auth.get("expiresAt") or 0)

    path: Path = credential["path"]
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        # 写回失败不影响本次调用，但要留下痕迹：下次启动仍会读旧 token
        logger.warning("workbuddy 凭据写回失败（%s）：%s", path.name, exc)
    logger.info("workbuddy 账号 %s 续期完成", credential.get("label") or credential.get("account_id"))
    return credential


def _ensure_fresh(credential: dict) -> dict:
    if not _needs_refresh(credential):
        return credential
    with _REFRESH_LOCK:
        # 双检：另一线程可能刚刷过
        latest = _read_credential(credential["path"])
        latest["region"] = credential.get("region") or "cn"
        latest["label"] = latest["nickname"] or latest["uin"] or latest["account_id"]
        if not _needs_refresh(latest):
            return latest
        return refresh_credential(latest)


# ---------------------------------------------------------------------------
# Chat completion
# ---------------------------------------------------------------------------


def _sse_text(raw: str) -> tuple[str, str]:
    """把 SSE 流解析成 (正文, 推理内容)。"""

    parts: list[str] = []
    reasoning: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            document = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        choices = document.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
        thought = delta.get("reasoning_content")
        if isinstance(thought, str) and thought:
            reasoning.append(thought)
    return "".join(parts), "".join(reasoning)


def _post_chat(credential: dict, payload: dict) -> str:
    url = f"{chat_base(credential)}/v2/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=_chat_headers(credential), method="POST")
    try:
        with urlopen(request, timeout=CHAT_TIMEOUT) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _raise_for_http(exc.code, detail)
    except (URLError, TimeoutError) as exc:
        raise WorkbuddyError(f"WorkBuddy 上游连接失败：{exc}") from exc


def _raise_for_http(status: int, detail: str):
    message = ""
    try:
        payload = json.loads(detail)
        message = str(payload.get("msg") or payload.get("message") or "")
    except json.JSONDecodeError:
        message = detail[:300]
    if status in (401, 403):
        raise WorkbuddyError(
            f"WorkBuddy 账号鉴权失败（HTTP {status}）：{message or '凭据可能已失效'}。"
            "请在设置页换一个账号，或到 DSH 里重新登录。"
        )
    raise WorkbuddyError(f"WorkBuddy 上游请求失败：HTTP {status}。{message[:300]}")


def _setting(settings, key, default=""):
    """settings 可能是 dict 或 sqlite3.Row（没有 .get），统一安全读取。"""

    if settings is None:
        return default
    try:
        value = settings[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


SYSTEM_HEAD_FALLBACK = "You are a helpful assistant."


def chat_completion(settings, prompt, request_options=None):
    """与 gongkao.ai.chat_completion 同签名，返回 (正文, 原始响应)。"""

    request_options = request_options or {}
    # 该模式固定使用 Deepseek-V4.1-Flash：即使上层 request_options 里带了别的 model
    # （Codex/API 模式会传），也不能改变这里选用的模型。
    model = DEFAULT_MODEL
    account_id = str(_setting(settings, "workbuddy_account") or "").strip()
    credential = _ensure_fresh(find_account(account_id))

    system_prompt = "你是专业、严格、可操作的申论批改老师。"
    response_format = request_options.get("response_format")
    wants_json = isinstance(response_format, dict) and response_format.get("type") == "json_object"
    if wants_json:
        system_prompt += (
            " 当前任务要求 JSON 输出：最终内容必须是单个合法 JSON 对象，"
            "不要输出 Markdown、XML 标签或任何额外文字。"
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(_setting(settings, "temperature") or 0.2),
        # 上游不接受非流式请求（400 / 11101），这里恒为 true 并在本地拼回全文
        "stream": True,
    }
    max_tokens = request_options.get("max_tokens")
    if isinstance(max_tokens, int) and 1 <= max_tokens <= 384000:
        payload["max_tokens"] = max_tokens

    raw = _post_chat(credential, payload)
    content, reasoning = _sse_text(raw)
    if not content.strip():
        # 有些情况下思考内容先到、正文被上游掐断，明确报错而不是返回空串
        hint = "模型只返回了思考内容，未给出正文。" if reasoning.strip() else "模型返回内容为空。"
        raise WorkbuddyError(f"{hint}请重试，或在设置页换用其他 WorkBuddy 账号。")
    return content, raw


def test_account(account_id: str = "") -> dict:
    """设置页「测试连接」用：发一条极短请求确认账号可用。"""

    credential = _ensure_fresh(find_account(account_id))
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_HEAD_FALLBACK},
            {"role": "user", "content": "只回复两个字：在线"},
        ],
        "temperature": 0.2,
        "stream": True,
    }
    raw = _post_chat(credential, payload)
    content, _ = _sse_text(raw)
    return {
        "account_id": credential["account_id"],
        "label": credential.get("label") or credential["account_id"],
        "model": DEFAULT_MODEL,
        "reply": content.strip()[:80],
    }
