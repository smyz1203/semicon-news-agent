"""F5 저장: data/ 폴더의 JSON 파일을 읽고 쓴다.

파일은 임시 파일에 먼저 쓴 뒤 바꿔치기(원자적 쓰기)해서, 중간에 멈춰도 깨진 파일이 남지 않는다.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1


# ── 파일 입출력 ─────────────────────────────────────────────


def read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            # ensure_ascii=False: 한글을 \uXXXX가 아닌 그대로 저장
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── 시간 ───────────────────────────────────────────────────


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── state.json: 마지막 성공 시각, 본 URL, 매체 상태 ──────────────


def load_state(data_dir: Path) -> dict:
    state = read_json(data_dir / "state.json", {})
    state.setdefault("version", SCHEMA_VERSION)
    state.setdefault("last_success_at", None)
    state.setdefault("seen", {})
    state.setdefault("sources", {})
    return state


def save_state(data_dir: Path, state: dict) -> None:
    write_json(data_dir / "state.json", state)


def collect_since(state: dict, lookback: dict, now: datetime) -> datetime:
    """이번에 어느 시점 이후 기사를 모을지. 마지막 성공 시각보다 조금 앞부터."""
    if state.get("last_success_at"):
        return from_iso(state["last_success_at"]) - timedelta(minutes=lookback["overlap_minutes"])
    return now - timedelta(hours=lookback["first_run_hours"])


def update_source_health(state: dict, results: dict, now: datetime) -> None:
    for source_id, r in results.items():
        h = state["sources"].setdefault(source_id, {"fail_streak": 0})
        h["last_checked_at"] = to_iso(now)
        if r["ok"]:
            h["fail_streak"] = 0
            h["last_ok_at"] = to_iso(now)
            h.pop("last_error", None)
            if r["count"]:
                h["last_new_at"] = to_iso(now)
        else:
            h["fail_streak"] = h.get("fail_streak", 0) + 1
            h["last_error"] = r["error"]


def mark_seen(state: dict, keys, now: datetime) -> None:
    stamp = to_iso(now)
    for k in keys:
        state["seen"][k] = stamp


def prune_seen(state: dict, days: int, now: datetime) -> None:
    cutoff = now - timedelta(days=days)
    state["seen"] = {k: t for k, t in state["seen"].items() if from_iso(t) >= cutoff}
