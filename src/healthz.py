"""
healthz.py — 讀 babysit 寫的 heartbeat.json 回報健康狀態。

退出碼：
  0  健康（last_run 新鮮 + 所有 agent SSH 通）
  1  不健康（缺檔 / 損壞 / stale / 任一 agent SSH fail）

用法：
  python src/healthz.py                 # 人類可讀
  python src/healthz.py --json          # 機器可讀
  python src/healthz.py --max-age 600   # 自訂閾值（秒）
  python src/healthz.py --allow-partial # 部分 agent 失敗仍視為健康
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.babysit import HEARTBEAT_FILE  # 共用路徑常數，避免讀寫兩端不一致

DEFAULT_MAX_AGE_SECONDS = 300  # 5 分鐘 = 2.5 倍 babysit 週期，容忍 1 次 lock skip


def load_heartbeat(path: Path) -> dict | None:
    """讀 heartbeat.json，失敗回 None。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def evaluate(hb: dict | None, max_age_seconds: int,
             allow_partial: bool = False,
             now_ts: float | None = None) -> tuple[bool, list[str]]:
    """評估 heartbeat 健康狀態。
    回 (healthy, messages)。messages 是要印給用戶的多行報告。
    """
    if now_ts is None:
        now_ts = time.time()

    if hb is None:
        return False, ["heartbeat 檔案不存在或損壞"]

    msgs = []
    healthy = True

    # 1. 檢查必要欄位
    last_run = hb.get("last_run_ts")
    agents = hb.get("agents_pinged")
    if last_run is None or agents is None:
        return False, ["heartbeat 缺少必要欄位（last_run_ts 或 agents_pinged）"]
    if not isinstance(last_run, (int, float)) or not isinstance(agents, dict):
        return False, ["heartbeat 欄位型別錯誤"]
    if not all(isinstance(info, dict) for info in agents.values()):
        return False, ["agents_pinged 內項目格式錯誤（非 dict）"]

    # 2. 檢查新鮮度（容忍時鐘漂移：未來時間視為健康）
    age = now_ts - last_run
    if age > max_age_seconds:
        healthy = False
        msgs.append(f"last_run 已過期 {int(age)}s（上限 {max_age_seconds}s）")
    else:
        msgs.append(f"last_run {int(max(age, 0))}s 前")

    # 3. 檢查每個 agent
    if not agents:
        msgs.append("沒有 enabled agent")
    else:
        ok_agents = [n for n, info in agents.items() if info.get("ssh_ok")]
        fail_agents = [n for n, info in agents.items() if not info.get("ssh_ok")]
        msgs.append(f"agents OK: {ok_agents or '無'}; FAIL: {fail_agents or '無'}")
        if fail_agents:
            if allow_partial and ok_agents:
                pass  # 部分通就 OK
            else:
                healthy = False

    duration = hb.get("last_run_duration_ms")
    if isinstance(duration, int):
        msgs.append(f"last_run 耗時 {duration}ms")

    return healthy, msgs


def main():
    parser = argparse.ArgumentParser(description="babysit 健康檢查")
    parser.add_argument("--max-age", type=int, default=DEFAULT_MAX_AGE_SECONDS,
                        help=f"last_run 最大允許過期秒數（預設 {DEFAULT_MAX_AGE_SECONDS}）")
    parser.add_argument("--allow-partial", action="store_true",
                        help="部分 agent SSH fail 仍視為健康（至少一個通即可）")
    parser.add_argument("--json", action="store_true",
                        help="輸出 JSON 格式給機器讀")
    parser.add_argument("--heartbeat-file", type=str,
                        default=None,
                        help="自訂 heartbeat 檔路徑（預設 data/heartbeat.json）")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    hb_path = Path(args.heartbeat_file) if args.heartbeat_file \
              else base_dir / HEARTBEAT_FILE

    hb = load_heartbeat(hb_path)
    healthy, msgs = evaluate(hb, args.max_age, args.allow_partial)

    if args.json:
        out = {
            "healthy": healthy,
            "messages": msgs,
            "heartbeat": hb,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        status = "OK" if healthy else "UNHEALTHY"
        print(f"[healthz] {status}")
        for m in msgs:
            print(f"  - {m}")

    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
