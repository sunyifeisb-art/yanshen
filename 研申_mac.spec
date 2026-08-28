# -*- mode: python ; coding: utf-8 -*-
# macOS PyInstaller spec for 研申.
#
# Mirrors 研申.spec (Windows) but:
#   * launches launcher_mac.py (pywebview / WKWebView host) instead of
#     launcher.py (C# WebView2 host);
#   * drops the WebView2 native binaries from datas;
#   * adds an .app BUNDLE with the macOS .icns icon;
#   * disables UPX (not used on macOS).

import os
from PyInstaller.utils.hooks import collect_submodules

OPTIONAL_EVAL_EXCLUDES = [
    "ragas",
    "datasets",
    "pyarrow",
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "matplotlib",
    "PIL",
    "reportlab",
    "pdfminer",
    "pypdfium2",
    "pypdfium2_raw",
    "google.cloud",
    "grpc",
    "hf_xet",
    # Optional local semantic-search acceleration is intentionally excluded
    # from the desktop release.  The app defaults to its portable feature-hash
    # retriever and falls back gracefully when these packages are unavailable.
    "fastembed",
    "onnxruntime",
    "tokenizers",
]

AGENT_HIDDENIMPORTS = (
    collect_submodules("langgraph")
    + collect_submodules("langchain")
    + collect_submodules("langchain_core")
    + collect_submodules("langchain_openai")
    + collect_submodules("langgraph.checkpoint.memory")
    + collect_submodules("langgraph.checkpoint.sqlite")
    + collect_submodules("jinja2")
    + collect_submodules("markupsafe")
    + collect_submodules("pydantic")
    + collect_submodules("httpx")
    + collect_submodules("openai")
)

datas = [
    ("static", "static"),
    ("gongkao/web/templates", "gongkao/web/templates"),
    ("templates_data", "templates_data"),
    ("knowledge/manifest.json", "knowledge"),
    ("knowledge/knowledge_cards_v2.jsonl", "knowledge"),
    ("knowledge/shenlun_methodology.jsonl", "knowledge"),
    ("knowledge/saduck_methodology.jsonl", "knowledge"),
    ("data/gongkao_seed.sqlite3", "data"),
    ("assets/app-icon.png", "assets"),
]
if os.path.exists("evals"):
    datas.append(("evals", "evals"))

a = Analysis(
    ["launcher_mac.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("webview") + AGENT_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=OPTIONAL_EVAL_EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="研申",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="build/icon/app-icon.icns",
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="研申",
)

app = BUNDLE(
    coll,
    name="研申.app",
    icon="build/icon/app-icon.icns",
    bundle_identifier="com.nullapse.yanshen",
    info_plist={
        "CFBundleName": "研申",
        "CFBundleDisplayName": "研申",
        "CFBundleShortVersionString": "1.4.9",
        "CFBundleVersion": "1.4.9.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "仅供个人学习与研究使用。MIT License (program code)",
        "LSApplicationCategoryType": "public.app-category.education",
    },
)
