"""Agent 전체 흐름 실행.

사용법:
    python -m agent.main --dry-run   # AI 없이 수집·1차 거르기 결과만 출력 (비용 0원)
"""

import argparse
import logging
from datetime import datetime, timezone

from agent import store
from agent.collect import collect
from agent.config import DATA_DIR, load_config
from agent.dedupe import (CompanyMatcher, KeywordMatcher, attach_to_issues, drop_irrelevant,
                          drop_seen, group_articles)

log = logging.getLogger("agent")


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    s = cfg.settings
    now = datetime.now(timezone.utc)
    state = store.load_state(DATA_DIR)

    # F1 수집
    since = store.collect_since(state, s["lookback"], now)
    log.info("수집 기준 시각: %s 이후", store.to_iso(since))
    articles, results = collect(cfg, since, now)

    # F2 1차 거르기
    companies = CompanyMatcher(s["companies"])
    watch_aliases = [a for name in s["importance"]["watch_companies"] for a in s["companies"].get(name, [name])]
    keywords = KeywordMatcher(s["keywords"], watch_aliases)
    fresh = drop_seen(articles, state["seen"])
    relevant = drop_irrelevant(fresh, keywords)
    groups = group_articles(relevant, companies, s["dedupe"]["title_similarity"], s["dedupe"]["max_hours_apart"])
    attach_to_issues(groups, [], companies, s["dedupe"]["title_similarity"])

    failed = [sid for sid, r in results.items() if not r["ok"]]
    log.info("수집 %d건 → 새 기사 %d건 → 관련 기사 %d건 → 묶음 %d개 (실패한 매체 %d곳: %s)",
             len(articles), len(fresh), len(relevant), len(groups), len(failed), ", ".join(failed) or "없음")

    if dry_run:
        _print_groups(groups)
        return


def _print_groups(groups) -> None:
    print()
    for g in sorted(groups, key=lambda g: g.lead.published_at, reverse=True):
        lead = g.lead
        extra = f"  (+{len(g.articles) - 1}건 같은 기사)" if len(g.articles) > 1 else ""
        comps = ", ".join(sorted(g.companies))
        print(f"[{lead.published_at:%m-%d %H:%M}Z] {lead.source:<12} {lead.title}{extra}"
              + (f"  <{comps}>" if comps else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="반도체·디스플레이 뉴스 AI Agent")
    parser.add_argument("--dry-run", action="store_true", help="AI 없이 수집·1차 거르기만 실행")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
