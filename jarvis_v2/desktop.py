"""Entrypoint that lets the packaged desktop app launch its own runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if "--runtime" in sys.argv:
        sys.argv.remove("--runtime")
        os.environ.setdefault("JARVIS_ROOT", str(Path(sys.executable).resolve().parent))
        from jarvis_v2.app import main as runtime_main

        runtime_main()
        return
    from jarvis_v2.gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
