"""F6 웹 페이지 만들기: web/ 파일과 데이터를 _site/ 폴더로 모은다 (GitHub Pages가 이 폴더를 게시).

사용법:
    python -m agent.build_site                       # data/ → _site/
    python -m agent.build_site --data-dir local-data # 다른 데이터 폴더로
    python -m http.server -d _site                   # 로컬 미리보기 → http://localhost:8000
"""

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from agent import store
from agent.config import DATA_DIR, SITE_DIR, WEB_DIR, load_config

RUN_FIELDS = ("at", "collected", "relevant", "groups", "deferred", "new_issues", "summarized", "resummarized",
              "classified", "pending", "failed", "sources_ok", "sources_total", "ai_model", "cost_usd", "alerts")
AI_NAMES = {"openai": "OpenAI", "claude": "Claude"}
# 웹에 게시하는 필드만 골라 담음 (내부 관리용 필드나 실수로 들어간 값이 공개되지 않도록)
ISSUE_FIELDS = ("id", "headline", "summary", "category", "tags", "companies", "importance", "importance_raw",
                "importance_reason", "status", "region", "first_seen", "updated_at")
ARTICLE_FIELDS = ("title", "url", "source", "lang", "published_at")


def build(data_dir: Path = DATA_DIR, out_dir: Path = SITE_DIR, local: bool = False) -> Path:
    """local=True면 내 PC 서버(agent.serve)용: 새로고침 버튼이 GitHub 대신 내 PC에서 Agent를 실행한다."""
    cfg = load_config()
    now = datetime.now(timezone.utc)
    issues = store.read_json(data_dir / "issues.json", {"version": store.SCHEMA_VERSION, "issues": []})
    state = store.load_state(data_dir)
    runs = store.read_json(data_dir / "runs.json", [])

    meta = {
        "built_at": store.to_iso(now),
        "last_run_at": runs[-1]["at"] if runs else None,
        "last_success_at": state["last_success_at"],
        "categories": cfg.settings["categories"],
        "ai": _ai_label(cfg.settings, runs),
        "rubric": _rubric(cfg.settings),
        # '지금 업데이트' 버튼이 실행을 요청할 저장소. Actions에서는 GitHub이 알려주는 값을 우선 사용
        "repo": os.environ.get("GITHUB_REPOSITORY") or cfg.settings.get("github_repo"),
        # 새로고침 버튼 동작: local = 내 PC 서버, github = GitHub Actions 실행 요청
        "mode": "local" if local else "github",
        "sources": _outlet_status(cfg.sources, state["sources"]),
        "runs": [{k: r.get(k) for k in RUN_FIELDS} for r in runs],  # 보관된 실행 전부 (settings.yaml의 runs_keep개): '수집 현황' 3일 그래프용
    }

    published = {
        "updated_at": issues.get("updated_at"),
        "issues": [_public_issue(i) for i in issues["issues"]],
    }

    # 옆 폴더에 먼저 다 만든 뒤 바꿔치기 (로컬 서버가 페이지를 보여주는 중에도 반쯤 만든 폴더가 보이지 않게)
    tmp = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(WEB_DIR, tmp, dirs_exist_ok=True)  # 지난번 임시 폴더가 다 안 지워졌어도 덮어씀
    store.write_json(tmp / "data" / "issues.json", published)
    store.write_json(tmp / "data" / "meta.json", meta)

    # 브라우저가 예전 파일을 캐시해서 보여주지 않도록 빌드 시각을 주소에 붙임
    index = tmp / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("__BUILD__", now.strftime("%Y%m%d%H%M%S")),
                     encoding="utf-8", newline="\n")
    (tmp / ".nojekyll").touch()

    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        tmp.rename(out_dir)
    except OSError:  # 윈도에서 파일이 잠시 잠겨 옛 폴더를 다 지우지 못한 경우: 내용만 덮어씀
        shutil.copytree(tmp, out_dir, dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
    return out_dir


def _ai_label(settings: dict, runs: list[dict]) -> str:
    """요약을 실제로 만든 모델 이름. 실행 기록에 남은 모델을 쓰고, 기록이 없으면 현재 설정을 씀."""
    used = []
    for r in reversed(runs):  # 최근 실행부터
        if r.get("ai_model") and r.get("summarized") and r["ai_model"] not in used:
            used.append(r["ai_model"])
    if not used:
        provider = settings.get("provider", "")
        used = [f"{provider}/{settings.get(provider, {}).get('model', '')}"]
    labels = []
    for value in used:
        provider, _, model = value.partition("/")
        labels.append(f"{AI_NAMES.get(provider, provider)} {model}".strip())
    return ", ".join(labels)


def _rubric(settings: dict) -> dict:
    """브리핑 맨 아래 '중요도 기준표' 참고자료. settings.yaml의 기준표를 그대로 옮기므로 설정을 고치면 표도 바뀐다."""
    imp = settings["importance"]
    adjust = imp["adjust"]
    conditions = [
        (adjust["watch_company"], "관심 기업이 사건의 직접 당사자"),
        (adjust["wide_coverage"], f"서로 다른 매체 {imp['wide_coverage_sources']}곳 이상이 보도"),
        (adjust["unconfirmed"], "검토·추진·계획·전망·루머처럼 아직 확정되지 않은 사건"),
    ]
    return {
        "event_types": [{"name": t["name"], "score": t["score"], "desc": t["desc"]} for t in imp["event_types"]],
        "adjust": [{"value": value, "condition": text} for value, text in conditions if value],  # 0으로 끈 항목은 뺌
        "watch_companies": list(imp["watch_companies"]),
    }


def _public_issue(issue: dict) -> dict:
    out = {k: issue.get(k) for k in ISSUE_FIELDS}
    out["articles"] = [{k: a.get(k) for k in ARTICLE_FIELDS} for a in issue.get("articles", [])]
    return out


def _outlet_status(sources, health: dict) -> list[dict]:
    """'수집 현황'의 매체 목록. 같은 언론사의 분야별 RSS(예: 디지털타임스 IT·산업)는 한 줄로 합친다.
    수집은 RSS마다 그대로 하고, 화면에서만 매체 하나로 보여준다."""
    outlets: dict[str, list] = {}
    for source in sources:
        outlets.setdefault(source.name, []).append(source)
    rows = []
    for name, feeds in outlets.items():
        known = [health[f.id] for f in feeds if health.get(f.id)]
        failed = [h for h in known if h.get("fail_streak", 0)]
        langs = {f.lang for f in feeds}
        rows.append({
            "name": name,
            "lang": langs.pop() if len(langs) == 1 else "both",
            "sections": [f.section for f in feeds if f.section],  # 받는 분야. 화면에서 마우스를 올리면 보임
            "rss": len(feeds),
            "failed_rss": len(failed),
            "ok": not failed if known else None,  # 아직 한 번도 수집하지 않았으면 None
            "fail_streak": max((h["fail_streak"] for h in failed), default=0),
            "last_ok_at": max((h["last_ok_at"] for h in known if h.get("last_ok_at")), default=None),
            "last_new_at": max((h["last_new_at"] for h in known if h.get("last_new_at")), default=None),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="웹 페이지(_site/) 만들기")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=SITE_DIR)
    parser.add_argument("--local", action="store_true", help="내 PC 서버(agent.serve)용으로 만들기")
    args = parser.parse_args()
    out = build(args.data_dir, args.out_dir, local=args.local)
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
