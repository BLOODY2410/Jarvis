"""Entrypoint that lets the packaged desktop app launch its own runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if "--runtime" in sys.argv:
        sys.argv.remove("--runtime")
        root = Path(os.environ.setdefault("JARVIS_ROOT", str(Path(sys.executable).resolve().parent)))
        # A PyInstaller --windowed build has no useful console. Always send
        # startup diagnostics to a line-buffered local log so a failed device
        # or SDK initialization can be diagnosed from the GUI.
        log_path = root / ".run" / "gui-runtime.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
        from jarvis_v2.app import main as runtime_main

        runtime_main()
        return
    from jarvis_v2.gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
