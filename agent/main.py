"""Agent 전체 흐름 실행: 수집 → 1차 거르기 → AI 묶기 → AI 요약·분류 → 중요도 계산 → 저장.

사용법:
    python -m agent.main --dry-run            # AI 없이 수집·1차 거르기 결과만 출력 (비용 0원)
    python -m agent.main --limit 5            # 소량만 실제로 처리 (테스트용)
    python -m agent.main                      # 전체 실행 (GitHub Actions가 1시간마다 실행)
    python -m agent.main --data-dir local-data  # 결과를 다른 폴더에 저장
    python -m agent.main --rebuild            # 처음부터 다시 만들기 (기준을 바꾼 뒤 기존 이슈도 새 기준으로)
"""

import argparse
import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from agent import store
from agent.analyze import KEY_ENV, AIError, AIStopped, Analyzer
from agent.collect import collect
from agent.config import DATA_DIR, load_config
from agent.dedupe import (CompanyMatcher, KeywordMatcher, attach_to_issues, drop_irrelevant,
                          drop_seen, group_articles, guard_ai_merges)
from agent.score import Scorer

log = logging.getLogger("agent")


def run(data_dir: Path = DATA_DIR, dry_run: bool = False, limit: int | None = None, rebuild: bool = False) -> dict | None:
    cfg = load_config()
    s = cfg.settings
    if s.get("provider") not in KEY_ENV:
        raise SystemExit(f"config/settings.yaml의 provider는 {', '.join(KEY_ENV)} 중 하나여야 합니다 (지금: {s.get('provider')})")
    limits = dict(s["limits"])
    if limit:
        limits["max_articles_per_run"] = limit
        limits["max_summaries_per_run"] = limit
    now = datetime.now(timezone.utc)
    state = store.load_state(data_dir)
    issues = store.load_issues(data_dir)
    if rebuild:
        # 처음부터 다시 만들기: 이슈와 '이미 본 기사' 목록을 비우고 최근 48시간 기사를 지금 기준으로 다시 처리.
        # 매체 상태는 남기고, 실행 기록은 저장할 때 비움. 예전 데이터는 git 기록에 남아 있음
        log.warning("처음부터 다시 만들기: 이슈 %d개와 이미 본 기사 %d건을 비움", len(issues), len(state["seen"]))
        issues, state["seen"] = [], {}

    # ── F1 수집 ──────────────────────────────────────────────
    articles, results = collect(cfg, now)

    # ── F2 1차 거르기 ────────────────────────────────────────
    companies = CompanyMatcher(s["companies"])
    # 관련성 판단용 기업 (중요도 가산점을 주는 관심 기업과는 따로 설정)
    relevance_names = s["keywords"].get("companies", s["importance"]["watch_companies"])
    relevance_aliases = [a for name in relevance_names for a in s["companies"].get(name, [name])]
    fresh = drop_seen(articles, state["seen"])
    unseen = Counter(a.source_id for a in fresh)
    for source_id, r in results.items():
        if r["ok"]:
            r["count"] = unseen[source_id]  # 매체 상태의 '마지막 새 기사'는 처음 보는 기사 기준
    relevant = drop_irrelevant(fresh, KeywordMatcher(s["keywords"], relevance_aliases))
    threshold = s["dedupe"]["title_similarity"]
    groups = group_articles(relevant, companies, threshold, s["dedupe"]["max_hours_apart"])
    active = store.active_issues(issues, s["retention"]["active_issue_hours"], now)
    attach_to_issues(groups, active, companies, threshold)

    sources_ok = sum(1 for r in results.values() if r["ok"])
    log.info("수집 %d건 → 새 기사 %d건 → 관련 기사 %d건 → 묶음 %d개 (매체 %d/%d곳 성공)",
             len(articles), len(fresh), len(relevant), len(groups), sources_ok, len(results))

    if dry_run:
        _print_groups(groups)
        return None

    # 비용 폭주 방지: AI에 넘길 묶음 수 제한
    # 넘친 묶음은 seen에 기록하지 않으므로, 48시간 안이라면 다음 실행에서 다시 수집·처리됨
    to_ai = sorted((g for g in groups if g.issue_id is None), key=lambda g: g.lead.published_at, reverse=True)
    deferred = to_ai[limits["max_articles_per_run"]:]
    to_ai = to_ai[:limits["max_articles_per_run"]]
    if deferred:
        log.warning("상한(%d)을 넘은 %d개 묶음은 다음 실행으로 미룸", limits["max_articles_per_run"], len(deferred))
    deferred_ids = {id(g) for g in deferred}
    processed = [g for g in groups if id(g) not in deferred_ids]

    analyzer, ai_stopped = None, None
    ai_deadline = time.monotonic() + limits["ai_time_budget_minutes"] * 60
    key_env = KEY_ENV[s["provider"]]
    if os.environ.get(key_env):
        analyzer = Analyzer({**s, "limits": limits})
        log.info("AI: %s / %s", s["provider"], analyzer.model)
    else:
        ai_stopped = f"{key_env}가 설정되지 않음"

    # ── F3 AI 이슈 묶기·분류 ─────────────────────────────────
    # 묶기를 못 하면(키 없음, AI 중단, 실패) 새 이슈를 만들지 않고 다음 실행으로 미룸.
    # 묶지 않은 채 이슈를 만들면 나중에 AI가 돌아와도 중복 이슈가 그대로 남기 때문.
    targets: dict[int, str | None] = {}
    if to_ai:
        try:
            if not analyzer:
                raise AIStopped(ai_stopped)
            targets = analyzer.cluster(to_ai, active)
            rejected = guard_ai_merges(to_ai, targets, active, companies)
            if rejected:
                log.info("회사가 서로 다른 기사를 묶은 AI 판단 %d건을 되돌림", rejected)
        except (AIError, AIStopped) as e:
            if isinstance(e, AIStopped):
                ai_stopped = str(e)
            log.warning("이슈 묶기를 못 해 새 기사 묶음 %d개를 다음 실행으로 미룸: %s", len(to_ai), e)
            deferred = deferred + to_ai
            to_ai = []
            deferred_ids = {id(g) for g in deferred}
            processed = [g for g in groups if id(g) not in deferred_ids]
    index_of = {id(g): i for i, g in enumerate(to_ai)}

    by_id = {i["id"]: i for i in issues}
    new_keys: dict[str, str] = {}
    new_count = irrelevant = 0
    for g in processed:
        if g.issue_id:
            target = g.issue_id
        else:
            i = index_of[id(g)]
            target = targets.get(i, f"new:a{i}")
        if target is None:
            irrelevant += 1
            continue
        if target.startswith("new:"):
            if target not in new_keys:
                issue = store.new_issue(state, now)
                issues.append(issue)
                by_id[issue["id"]] = issue
                new_keys[target] = issue["id"]
                new_count += 1
            target = new_keys[target]
        store.add_articles(by_id[target], g.articles, now)  # 새 매체 기사면 needs_summary가 켜짐

    # ── F4 AI 요약·사건 분류 ─────────────────────────────────
    # needs_summary는 저장되므로, 이번에 못 한 요약(실패·AI 중단·상한 초과)은 다음 실행에서 이어서 함
    texts = {a.key: a.text for g in processed for a in g.articles}
    queue = [i for i in issues
             if i.get("needs_summary") and i.get("attempts", 0) < limits["summary_max_attempts"]]
    queue.sort(key=lambda i: (len(i["articles"]), i["updated_at"]), reverse=True)
    summarized = resummarized = 0
    for issue in queue[:limits["max_summaries_per_run"]]:
        if not analyzer or ai_stopped:
            break
        if time.monotonic() > ai_deadline:
            log.warning("AI 시간 예산(%d분) 초과, 남은 요약은 다음 실행으로", limits["ai_time_budget_minutes"])
            break
        try:
            again = issue.get("status") == "summarized"  # 다른 매체 기사가 붙어 다시 요약하는 이슈
            issue.update(analyzer.summarize(issue, texts))
            issue.update(status="summarized", needs_summary=False, attempts=0)
            summarized += 1
            resummarized += again
            log.info("요약 [%s] %s", issue["event_type"], issue["headline"])
        except AIError as e:
            issue["attempts"] = issue.get("attempts", 0) + 1
            log.warning("요약 실패 %s (%d회째): %s", issue["id"], issue["attempts"], e)
        except AIStopped as e:
            ai_stopped = str(e)

    # 예전 방식(AI가 점수를 매김)으로 요약한 이슈나, 기준표의 사건 유형이 바뀐 이슈는 분류만 다시 함 (요약은 그대로)
    scorer = Scorer(s)
    reclassify = [i for i in issues
                  if scorer.needs_classification(i) and i.get("attempts", 0) < limits["summary_max_attempts"]]
    reclassify.sort(key=lambda i: i["updated_at"], reverse=True)
    classified = 0
    for issue in reclassify[:limits["max_summaries_per_run"]]:
        if not analyzer or ai_stopped:
            break
        if time.monotonic() > ai_deadline:
            log.warning("AI 시간 예산(%d분) 초과, 남은 분류는 다음 실행으로", limits["ai_time_budget_minutes"])
            break
        try:
            issue.update(analyzer.classify(issue))
            issue["attempts"] = 0
            classified += 1
        except AIError as e:
            issue["attempts"] = issue.get("attempts", 0) + 1
            log.warning("분류 실패 %s (%d회째): %s", issue["id"], issue["attempts"], e)
        except AIStopped as e:
            ai_stopped = str(e)
    if ai_stopped:
        log.error("AI 중단: %s → 요약하지 못한 이슈는 '요약 대기'로 게시", ai_stopped)

    # 중요도: AI가 고른 사실과 보도 매체 수로 모든 이슈를 기준표대로 다시 계산 (AI 호출 없음)
    scorer.apply(issues)

    # ── F5 저장 ──────────────────────────────────────────────
    store.mark_seen(state, (a.key for g in processed for a in g.articles), now)
    # 1차 거르기에서 빠진 기사도 기록해야 다음 실행에서 또 '새 기사'로 세지 않음
    # (미룬 관련 기사는 다음 실행에서 다시 처리하도록 기록하지 않음)
    relevant_keys = {a.key for a in relevant}
    store.mark_seen(state, (a.key for a in fresh if a.key not in relevant_keys), now)
    store.prune_seen(state, s["retention"]["seen_url_days"], now)
    store.update_source_health(state, results, now)
    issues = store.archive_old(data_dir, issues, s["retention"]["issues_days"], now)
    if sources_ok * 2 >= len(results):
        state["last_success_at"] = store.to_iso(now)

    waiting = [i for i in issues if i.get("needs_summary")]
    record = {
        "at": store.to_iso(now),
        "collected": len(articles),
        "new": len(fresh),
        # 이번에 처리한 관련 기사. 미룬 묶음의 기사는 다음 실행에서 다시 들어오므로 여기서 세지 않음 (두 번 세지 않도록)
        "relevant": len(relevant) - sum(len(g.articles) for g in deferred),
        "groups": len(groups),
        "deferred": len(deferred),
        "irrelevant_by_ai": irrelevant,
        "new_issues": new_count,
        "summarized": summarized,          # 요약한 횟수 = 처음 요약 + 다시 요약
        "resummarized": resummarized,
        "classified": classified,
        # 요약 대기: 다음 실행에서 다시 시도할 이슈. 실패 횟수를 다 쓴 이슈는 failed로 따로 셈
        "pending": sum(1 for i in waiting if i.get("attempts", 0) < limits["summary_max_attempts"]),
        "failed": sum(1 for i in waiting if i.get("attempts", 0) >= limits["summary_max_attempts"]),
        "sources_ok": sources_ok,
        "sources_total": len(results),
        "ai_model": f"{s['provider']}/{s[s['provider']]['model']}",
        "ai_calls": analyzer.usage["calls"] if analyzer else 0,
        "tokens": analyzer.usage if analyzer else None,
        "cost_usd": analyzer.cost_usd() if analyzer else 0.0,
        "ai_stopped": ai_stopped,
    }
    record["alerts"] = _alerts(record, state, now)
    store.save_issues(data_dir, issues, now)
    store.save_state(data_dir, state)
    if rebuild:
        store.write_json(data_dir / "runs.json", [])  # 수집 현황 숫자도 다시 만든 시점부터 셈
    store.append_run(data_dir, record, s["retention"]["runs_keep"])
    log.info("완료: 새 이슈 %d개, 요약 %d건(다시 요약 %d), 재분류 %d건, 요약 대기 %d건, AI 호출 %d회, 추정 비용 $%.4f",
             new_count, summarized, resummarized, classified, record["pending"], record["ai_calls"], record["cost_usd"])
    for alert in record["alerts"]:
        log.error("경고: %s", alert)
    return record


def _alerts(record: dict, state: dict, now: datetime) -> list[str]:
    alerts = []
    if record["sources_ok"] * 2 < record["sources_total"]:
        alerts.append(f"매체 과반 수집 실패 ({record['sources_ok']}/{record['sources_total']})")
    if record["ai_stopped"]:
        alerts.append(f"AI 중단: {record['ai_stopped']}")
    last_new = [store.from_iso(h["last_new_at"]) for h in state["sources"].values() if h.get("last_new_at")]
    if not last_new or max(last_new) < now - timedelta(hours=24):
        alerts.append("24시간 동안 새 기사가 없음")
    return alerts


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
    parser.add_argument("--limit", type=int, help="AI로 처리할 묶음·요약 수 제한 (테스트용)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="결과를 저장할 폴더")
    parser.add_argument("--rebuild", action="store_true",
                        help="처음부터 다시 만들기: 이슈·이미 본 기사·실행 기록을 비우고 최근 48시간 기사를 다시 처리")
    args = parser.parse_args()
    load_dotenv()  # 로컬 실행용 .env 파일에서 API 키 읽기 (OPENAI_API_KEY 또는 ANTHROPIC_API_KEY)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    run(data_dir=args.data_dir, dry_run=args.dry_run, limit=args.limit, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
