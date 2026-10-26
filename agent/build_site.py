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

RUN_FIELDS = ("at", "collected", "relevant", "groups", "new_issues", "summarized", "pending",
              "sources_ok", "sources_total", "cost_usd", "alerts")


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
        "sources": [_source_status(s, state["sources"].get(s.id, {})) for s in cfg.sources],
        "runs": [{k: r.get(k) for k in RUN_FIELDS} for r in runs[-24:]],
    }

    shutil.rmtree(out_dir, ignore_errors=True)
    shutil.copytree(WEB_DIR, out_dir)
    store.write_json(out_dir / "data" / "issues.json", issues)
    store.write_json(out_dir / "data" / "meta.json", meta)

    # 브라우저가 예전 파일을 캐시해서 보여주지 않도록 빌드 시각을 주소에 붙임
    index = out_dir / "index.html"
    index.write_text(index.read_text(encoding="utf-8").replace("__BUILD__", now.strftime("%Y%m%d%H%M%S")),
                     encoding="utf-8", newline="\n")
    (out_dir / ".nojekyll").touch()
    return out_dir


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
