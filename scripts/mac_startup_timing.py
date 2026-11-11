"""PyInstaller runtime hook: stamp when the frozen Python interpreter boots.

Writes a single line to /tmp/yanshen-startup-timing.txt so startup phases can
be split: dyld/pre-python vs frozen-python import work.
"""

import time

try:
    with open("/tmp/yanshen-startup-timing.txt", "a", encoding="utf-8") as fh:
        fh.write(f"t={time.time():.3f} python_boot\n")
except Exception:
    pass
