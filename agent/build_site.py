"""F6 웹 페이지 만들기: web/ 파일과 데이터를 _site/ 폴더로 모은다 (GitHub Pages가 이 폴더를 게시).

사용법:
    python -m agent.build_site                       # data/ → _site/
    python -m agent.build_site --data-dir local-data # 다른 데이터 폴더로
    python -m http.server -d _site                   # 로컬 미리보기 → http://localhost:8000
"""

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from agent import store
from agent.config import DATA_DIR, SITE_DIR, WEB_DIR, load_config

RUN_FIELDS = ("at", "collected", "relevant", "groups", "deferred", "new_issues", "summarized", "pending",
              "sources_ok", "sources_total", "ai_model", "cost_usd", "alerts")
AI_NAMES = {"openai": "OpenAI", "claude": "Claude"}
# 웹에 게시하는 필드만 골라 담음 (내부 관리용 필드나 실수로 들어간 값이 공개되지 않도록)
ISSUE_FIELDS = ("id", "headline", "summary", "category", "tags", "companies", "importance", "importance_reason",
                "status", "region", "first_seen", "updated_at")
ARTICLE_FIELDS = ("title", "url", "source", "lang", "published_at")


def build(data_dir: Path = DATA_DIR, out_dir: Path = SITE_DIR) -> Path:
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
        "ai": _ai_label(cfg.settings),
        "sources": [_source_status(s, state["sources"].get(s.id, {})) for s in cfg.sources],
        "runs": [{k: r.get(k) for k in RUN_FIELDS} for r in runs[-24:]],
    }

    published = {
        "updated_at": issues.get("updated_at"),
        "issues": [_public_issue(i) for i in issues["issues"]],
    }

    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.copytree(WEB_DIR, out_dir)
    store.write_json(out_dir / "data" / "issues.json", published)
    store.write_json(out_dir / "data" / "meta.json", meta)

    # 브라우저가 예전 파일을 캐시해서 보여주지 않도록 빌드 시각을 주소에 붙임
    index = out_dir / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("__BUILD__", now.strftime("%Y%m%d%H%M%S")),
                     encoding="utf-8", newline="\n")
    (out_dir / ".nojekyll").touch()
    return out_dir


def _ai_label(settings: dict) -> str:
    provider = settings["provider"]
    return f"{AI_NAMES.get(provider, provider)} {settings[provider]['model']}"


def _public_issue(issue: dict) -> dict:
    out = {k: issue.get(k) for k in ISSUE_FIELDS}
    out["articles"] = [{k: a.get(k) for k in ARTICLE_FIELDS} for a in issue.get("articles", [])]
    return out


def _source_status(source, health: dict) -> dict:
    return {
        "name": f"{source.name} {source.section}".strip(),
        "lang": source.lang,
        "ok": health.get("fail_streak", 0) == 0 if health else None,
        "fail_streak": health.get("fail_streak", 0),
        "last_ok_at": health.get("last_ok_at"),
        "last_new_at": health.get("last_new_at"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="웹 페이지(_site/) 만들기")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=SITE_DIR)
    args = parser.parse_args()
    out = build(args.data_dir, args.out_dir)
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
