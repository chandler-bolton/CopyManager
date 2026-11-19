"""Every file location the app uses, in one place.

The C++ monitor uses the same files, so keep the two sides in sync if you
move anything.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APP_SCRIPT = ROOT / "app.pyw"
MONITOR_EXE = ROOT / "bin" / "monitor.exe"         # built from src/monitor.cpp by build_monitor.bat

# Shared with the C++ monitor
SETTINGS_PATH = ROOT / "utils" / "settings.json"   # settings + preferences (monitor reads)
HISTORY_PATH = ROOT / "src" / "history.json"       # history + pinned items (monitor writes)

# Logs
LOG_DIR = ROOT / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"                 # written by this GUI
MONITOR_LOG_PATH = LOG_DIR / "monitor.log"         # written by the C++ monitor, shown in Output
MONITOR_STATUS_PATH = LOG_DIR / "monitor-status.json"  # what the monitor has active (e.g. paste shortcuts)
