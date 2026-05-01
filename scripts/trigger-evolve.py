"""Stop hook trigger: writes pending flags for Task Scheduler to pick up."""
import json, sys, pathlib

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

try:
    payload = json.loads(sys.stdin.read())
    session_id = payload.get("session_id", "")
except Exception:
    session_id = ""

(DATA_DIR / "pending_evolve.txt").write_text(session_id, encoding="utf-8")
(DATA_DIR / "pending_audit.txt").write_text("triggered", encoding="utf-8")
(DATA_DIR / "pending_session_wrap.txt").write_text(session_id, encoding="utf-8")
