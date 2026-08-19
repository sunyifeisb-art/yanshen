"""Shared accessors for grading and AI coach model settings."""


def row_mapping(row):
    return dict(row) if row is not None else {}


def load_grading_settings(conn):
    return row_mapping(conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone())


def load_agent_settings(conn):
    return row_mapping(conn.execute("SELECT * FROM agent_ai_settings WHERE id = 1").fetchone())


def load_effective_agent_settings(conn):
    # AI 教练始终与批改保持一致：直接沿用批改的服务商/模型/密钥/温度，
    # 修改批改设置时教练自动跟随。不再支持为教练单独设置连接。
    # chatgpt（ChatGPT/Codex 账号）模式原样传给教练；仅手动 codex 模式转 api，
    # 因为手动模式需要人工复制批改包，无法被教练自动调用。
    effective = {**load_grading_settings(conn)}
    if effective.get("mode") == "codex":
        effective["mode"] = "api"
    return effective
