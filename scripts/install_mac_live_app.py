"""Create a macOS app that launches the current checkout on every open.

The installed app contains only an icon and a small launcher.  Python modules,
templates, and static files are read from the checkout when the app starts, so
local changes are available on the next launch without another PyInstaller run.
"""

from __future__ import annotations

import argparse
import plistlib
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "研申"
BUNDLE_ID = "com.nullapse.yanshen"


def install(target: Path, source: Path = PROJECT_ROOT) -> None:
    source = source.resolve()
    python = source / ".venv" / "bin" / "python"
    entry = source / "launcher_mac.py"
    icon = source / "build" / "icon" / "app-icon.icns"
    for required in (python, entry, icon):
        if not required.exists():
            raise FileNotFoundError(required)

    runtime = (source / "gongkao" / "web" / "runtime.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION = "([^"]+)"', runtime, re.MULTILINE)
    if not match:
        raise ValueError("APP_VERSION was not found")
    version = match.group(1)

    target = target.expanduser().absolute()
    if target.exists():
        raise FileExistsError(target)
    contents = target / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleName": APP_NAME,
                "CFBundleDisplayName": APP_NAME,
                "CFBundleExecutable": APP_NAME,
                "CFBundleIdentifier": BUNDLE_ID,
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": f"{version}.1",
                "CFBundleIconFile": "app-icon.icns",
                "LSApplicationCategoryType": "public.app-category.education",
                "LSMinimumSystemVersion": "12.0",
                "NSHighResolutionCapable": True,
            }
        )
    )
    version_parts = subprocess.check_output(
        [str(python), "-c", "import sys,site; print(sys.base_prefix); print(site.getsitepackages()[0]); print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).splitlines()
    base_prefix, site_packages, python_version = version_parts
    config = Path(base_prefix) / "bin" / f"python{python_version}-config"
    if not config.exists():
        config = Path(shutil.which(f"python{python_version}-config") or "")
    if not config.is_file():
        raise FileNotFoundError(f"python{python_version}-config")
    flags = subprocess.check_output([str(config), "--embed", "--cflags", "--ldflags"], text=True)

    # Keep Python in this bundle's process, so macOS identifies the Dock app
    # as 研申 rather than the generic Python.app used by a shell `exec`.
    c_source = f'''#include <Python.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {{
    setenv("PYTHONHOME", {c_literal(base_prefix)}, 1);
    setenv("PYTHONPATH", {c_literal(str(source) + ':' + site_packages)}, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    unsetenv("PYTHONEXECUTABLE");
    if (chdir({c_literal(str(source))}) != 0) return 1;
    char *python_argv[] = {{argv[0], {c_literal(str(entry))}, NULL}};
    return Py_BytesMain(2, python_argv);
}}
'''
    launcher = macos / APP_NAME
    with tempfile.TemporaryDirectory() as temp_dir:
        c_file = Path(temp_dir) / "launcher.c"
        c_file.write_text(c_source, encoding="utf-8")
        subprocess.run(
            ["/usr/bin/clang", str(c_file), "-o", str(launcher), *shlex.split(flags)],
            check=True,
        )
    shutil.copy2(icon, resources / "app-icon.icns")


def c_literal(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Path of the new .app bundle")
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    install(args.target, args.source)
