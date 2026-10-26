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


# ── issues.json: 웹에 보여줄 이슈 목록 ──────────────────────────


def load_issues(data_dir: Path) -> list[dict]:
    return read_json(data_dir / "issues.json", {"issues": []})["issues"]


def save_issues(data_dir: Path, issues: list[dict], now: datetime) -> None:
    ordered = sorted(issues, key=lambda i: i["updated_at"], reverse=True)
    write_json(data_dir / "issues.json", {"version": SCHEMA_VERSION, "updated_at": to_iso(now), "issues": ordered})


def active_issues(issues: list[dict], hours: int, now: datetime) -> list[dict]:
    """새 기사를 붙일 수 있는 최근 이슈."""
    cutoff = now - timedelta(hours=hours)
    return [i for i in issues if from_iso(i["updated_at"]) >= cutoff]


def new_issue(state: dict, now: datetime) -> dict:
    state["issue_seq"] = state.get("issue_seq", 0) + 1
    return {
        "id": f"c{now:%Y%m%d}-{state['issue_seq']:04d}",
        "headline": "",
        "summary": [],
        "category": "",
        "tags": [],
        "companies": [],
        "importance": 0,
        "importance_reason": "",
        "status": "pending",   # 요약 전. 요약이 끝나면 summarized
        "region": "",
        "first_seen": to_iso(now),
        "updated_at": to_iso(now),
        "articles": [],
    }


def add_articles(issue: dict, articles, now: datetime) -> bool:
    """이슈에 기사를 붙인다. 새 매체의 기사가 붙었으면 True (요약을 다시 할 가치가 있음).

    공개 저장소에 올라가므로 기사 본문·요약문은 저장하지 않고 제목·링크·매체·시각만 남긴다.
    """
    keys = {a["key"] for a in issue["articles"]}
    sources = {a["source"] for a in issue["articles"]}
    added = new_source = False
    for art in articles:
        if art.key in keys:
            continue
        keys.add(art.key)
        issue["articles"].append({
            "key": art.key,
            "title": art.title,
            "url": art.url,
            "source": art.source,
            "lang": art.lang,
            "published_at": to_iso(art.published_at),
        })
        added = True
        if art.source not in sources:
            sources.add(art.source)
            new_source = True
    if added:
        issue["articles"].sort(key=lambda a: a["published_at"])
        langs = {a["lang"] for a in issue["articles"]}
        issue["region"] = "both" if len(langs) > 1 else next(iter(langs))
        issue["updated_at"] = to_iso(now)
    return new_source


def archive_old(data_dir: Path, issues: list[dict], days: int, now: datetime) -> list[dict]:
    """오래된 이슈를 data/archive/YYYY-MM.json으로 옮기고, 남길 이슈만 돌려준다."""
    cutoff = now - timedelta(days=days)
    keep, by_month = [], {}
    for issue in issues:
        if from_iso(issue["updated_at"]) >= cutoff:
            keep.append(issue)
        else:
            by_month.setdefault(issue["first_seen"][:7], []).append(issue)
    for month, old in by_month.items():
        path = data_dir / "archive" / f"{month}.json"
        archived = read_json(path, {"issues": []})["issues"]
        known = {i["id"] for i in archived}
        archived.extend(i for i in old if i["id"] not in known)
        write_json(path, {"version": SCHEMA_VERSION, "issues": archived})
    return keep


# ── runs.json: 실행 기록 ─────────────────────────────────────


def append_run(data_dir: Path, record: dict, keep: int) -> None:
    runs = read_json(data_dir / "runs.json", [])
    runs.append(record)
    write_json(data_dir / "runs.json", runs[-keep:])
