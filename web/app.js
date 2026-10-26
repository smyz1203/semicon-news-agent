// 반도체·디스플레이 뉴스 브리핑 — 화면 그리기
// 안전: 기사 제목·AI 요약 같은 외부 텍스트는 textContent로만 넣고 innerHTML은 쓰지 않는다.

"use strict";

const KST = "Asia/Seoul";
const STALE_HOURS = 6;
const TOP_LIMIT = 6;
const HOUR = 3600000;
const SLOT = 3 * HOUR;          // 수집 주기 3시간 = 그래프 한 칸
const OPS_DAYS = 7;             // '수집 현황' 그래프 기간
const LIST_ROWS = 5;            // 개요 카드의 순위 목록 줄 수

const IMPORTANCE = { 5: "5 매우 중요", 4: "4 중요", 3: "3 보통", 2: "2 낮음", 1: "1 낮음" };
const PERIOD_TEXT = { 1: "오늘", 3: "최근 3일", 7: "최근 7일", 30: "최근 30일" };

const view = { period: 3, region: "all", minImp: 0, category: null, company: null, query: "" };
let issues = [];
let meta = null;

// ── 작은 도우미 ─────────────────────────────────────────────

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const $ = (id) => document.getElementById(id);
const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : null);
const categoryOf = (issue) => issue.category || "미분류";
const num = (n) => Number(n || 0).toLocaleString("ko-KR");
const usd = (n) => `$${Number(n || 0).toFixed(4)}`;
const sourceCount = (issue) => new Set(issue.articles.map((a) => a.source)).size;

function kstDateKey(iso) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: KST }).format(new Date(iso)); // 2026-10-26
}

function dayLabel(key) {
  const today = kstDateKey(new Date().toISOString());
  const yesterday = kstDateKey(new Date(Date.now() - 86400000).toISOString());
  if (key === today) return "오늘";
  if (key === yesterday) return "어제";
  const d = new Date(key + "T12:00:00+09:00");
  return new Intl.DateTimeFormat("ko-KR", { timeZone: KST, month: "long", day: "numeric", weekday: "short" }).format(d);
}

function ago(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

function clock(iso) {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: KST, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(iso));
}

// 한국 시간(UTC+9, 서머타임 없음) 기준으로 3시간 칸·하루 칸의 시작 시각을 구함
const kst = (ms) => new Date(ms + 9 * HOUR);
const slotStart = (ms) => Math.floor((ms + 9 * HOUR) / SLOT) * SLOT - 9 * HOUR;
const dayStart = (ms) => Math.floor((ms + 9 * HOUR) / (24 * HOUR)) * 24 * HOUR - 9 * HOUR;
const shortDay = (ms) => `${kst(ms).getUTCMonth() + 1}/${kst(ms).getUTCDate()}`;
const longDay = (ms) => `${kst(ms).getUTCMonth() + 1}월 ${kst(ms).getUTCDate()}일`;
const slotText = (ms) => `${longDay(ms)} ${kst(ms).getUTCHours()}~${kst(ms).getUTCHours() + 3}시`;

// ── 필터 ───────────────────────────────────────────────────

// 기간의 시작: 한국 시간 달력 기준. 오늘은 오늘 0시, 3일은 그저께 0시부터 (그래프 막대 수와 맞음)
function periodStart() {
  return new Date(dayStart(Date.now()) - (view.period - 1) * 24 * HOUR);
}

function matches(issue, { ignoreCategory = false } = {}) {
  if (new Date(issue.updated_at) < periodStart()) return false;
  if (view.region !== "all" && issue.region !== view.region && issue.region !== "both") return false;
  if (view.minImp && (issue.importance || 0) < view.minImp) return false;
  if (!ignoreCategory && view.category && categoryOf(issue) !== view.category) return false;
  if (view.company && !(issue.companies || []).includes(view.company)) return false;
  if (view.query) {
    const haystack = [
      issue.headline, ...(issue.summary || []), ...(issue.tags || []), ...(issue.companies || []),
      ...issue.articles.map((a) => a.title),
    ].join(" ").toLowerCase();
    if (!haystack.includes(view.query)) return false;
  }
  return true;
}

// 중요도 → 여러 매체가 다룬 것 → 최신 순
const byImportance = (a, b) =>
  (b.importance || 0) - (a.importance || 0) || sourceCount(b) - sourceCount(a) || b.updated_at.localeCompare(a.updated_at);

// ── 이슈 ───────────────────────────────────────────────────

const isPending = (issue) => issue.status !== "summarized";
const titleOf = (issue) => issue.headline || issue.articles[issue.articles.length - 1].title;

// "디지털타임스" 또는 "연합뉴스 외 2곳"
function sourceLabel(issue) {
  const names = [...new Set([...issue.articles].reverse().map((a) => a.source))];
  return names.length > 1 ? `${names[0]} 외 ${names.length - 1}곳` : names[0] || "";
}

// 중요도: 점 색 + 글자 (색만으로 구분하지 않음)
function importancePill(issue) {
  if (isPending(issue)) return h("span", { class: "imp imp-0" }, "요약 대기");
  const level = issue.importance || 0;
  return h("span", { class: `imp imp-${level}` }, IMPORTANCE[level] || `${level}점`);
}

// 펼쳤을 때 보이는 내용. from=1이면 요약 첫 줄은 이미 카드에 나와 있으므로 뺌
function issueDetail(issue, from = 0) {
  const parts = [];
  if (!isPending(issue)) {
    const lines = issue.summary.slice(from);
    if (lines.length) parts.push(h("ul", { class: "summary" }, lines.map((line) => h("li", {}, line))));
    if (issue.tags.length) {
      parts.push(h("div", { class: "tags" }, issue.tags.map((tag) =>
        h("button", { class: "tag", type: "button", onclick: () => setQuery(tag) }, tag))));
    }
    parts.push(h("p", { class: "reason" }, h("b", {}, "판단 근거 "), issue.importance_reason));
  }
  const articles = [...issue.articles].reverse().map((a) => {
    const url = safeUrl(a.url);
    const lang = a.lang === "en" ? "en" : null;
    return h("li", {},
      h("span", { class: "src" }, a.source),
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", lang }, a.title) : h("span", { lang }, a.title));
  });
  parts.push(h("ul", { class: "articles" }, articles));
  return h("div", { class: "detail" }, parts);
}

// 주요 이슈 카드: 한눈에 제목과 첫 문장, 나머지는 펼쳐서
function topCard(issue) {
  const lead = isPending(issue) ? "요약 대기 중입니다. 다음 실행에서 요약합니다." : issue.summary[0];
  return h("article", { class: "panel top-card" },
    h("div", { class: "card-meta" },
      importancePill(issue),
      h("span", { class: "where" }, `${categoryOf(issue)} · ${sourceLabel(issue)}`),
      h("span", { class: "when", title: clock(issue.updated_at) }, ago(issue.updated_at))),
    h("h3", {}, titleOf(issue)),
    h("p", { class: "lead-line" }, lead),
    h("details", {},
      h("summary", { class: "toggle" }, `요약 전체 · 기사 ${issue.articles.length}건`),
      issueDetail(issue, 1)));
}

// 목록 한 줄: 중요도 · 제목 · 분야 · 매체 · 시각. 누르면 펼쳐짐
function issueRow(issue) {
  return h("details", { class: "row" },
    h("summary", {},
      importancePill(issue),
      h("span", { class: "row-title", title: titleOf(issue) }, titleOf(issue)),
      h("span", { class: "row-cat" }, h("span", {}, categoryOf(issue)), h("span", { class: "row-cat-src" }, ` · ${sourceLabel(issue)}`)),
      h("span", { class: "row-src" }, sourceLabel(issue)),
      h("span", { class: "row-time", title: clock(issue.updated_at) }, ago(issue.updated_at))),
    issueDetail(issue));
}

// ── 화면 1. 브리핑 ───────────────────────────────────────────

function render() {
  const shown = issues.filter((i) => matches(i)).sort(byImportance);
  const high = shown.filter((i) => !isPending(i) && i.importance >= 4);

  // 주요 이슈: 4~5점 중 위에서부터
  const top = high.slice(0, TOP_LIMIT);
  $("top").hidden = top.length === 0;
  $("top-list").replaceChildren(...top.map(topCard));
  $("top-note").textContent = high.length > top.length ? `중요도 4~5점 ${num(high.length)}건 중 상위 ${top.length}건` : "중요도 4~5점";

  // 그 밖의 이슈: 주요 이슈에 나온 것은 빼고, 날짜(한국 시간)별로
  const topIds = new Set(top.map((i) => i.id));
  const rest = shown.filter((i) => !topIds.has(i.id));
  const days = new Map();
  for (const issue of rest) {
    const key = kstDateKey(issue.updated_at);
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(issue);
  }
  const rows = [...days.keys()].sort().reverse().flatMap((key) => [
    h("h3", { class: "day-head" }, dayLabel(key), h("span", { class: "num" }, `${num(days.get(key).length)}건`)),
    ...days.get(key).map(issueRow),
  ]);
  $("timeline").replaceChildren(...rows);
  $("all-label").textContent = top.length ? "그 밖의 이슈" : "이슈";
  $("count").textContent = `${num(rest.length)}건`;
  $("empty").hidden = rest.length > 0;
  $("empty").textContent = shown.length
    ? "주요 이슈 말고 다른 이슈는 없습니다."
    : "조건에 맞는 이슈가 없습니다. 기간을 늘리거나 필터를 풀어 보세요.";

  renderFinding(shown, high);
  renderMix(shown);
  renderTrend(shown);
  renderCategories();
  renderCompanies(shown);
}

// 제목 아래 한 줄: 이 기간에 무슨 일이 있었나
function renderFinding(shown, high) {
  const lead = high[0];
  if (!shown.length) {
    $("finding").replaceChildren(`${PERIOD_TEXT[view.period]} 조건에 맞는 이슈가 없습니다.`);
    return;
  }
  $("finding").replaceChildren(h("span", {},
    `${PERIOD_TEXT[view.period]} 이슈 `, h("strong", {}, `${num(shown.length)}건`),
    high.length ? [", 중요도 4~5점 ", h("strong", {}, `${num(high.length)}건`), "."] : ".",
    lead ? ` 가장 높은 이슈는 ‘${titleOf(lead)}’(${lead.importance}점)입니다.` : null));
}

// 이슈 수와 중요도 분포
function renderMix(shown) {
  const done = shown.filter((i) => !isPending(i));
  const parts = [
    { label: "5점", value: done.filter((i) => i.importance === 5).length, color: "var(--imp-5)" },
    { label: "4점", value: done.filter((i) => i.importance === 4).length, color: "var(--imp-4)" },
    { label: "3점", value: done.filter((i) => i.importance === 3).length, color: "var(--imp-3)" },
    { label: "2점 이하", value: done.filter((i) => (i.importance || 0) <= 2).length, color: "var(--imp-2)" },
  ];
  const waiting = shown.length - done.length;
  if (waiting) parts.push({ label: "요약 대기", value: waiting, color: "var(--none)" });
  console.assert(parts.reduce((sum, p) => sum + p.value, 0) === shown.length, "중요도 분포의 합이 이슈 수와 다름");

  $("issue-total").textContent = num(shown.length);
  $("mix-note").textContent = PERIOD_TEXT[view.period];
  Charts.stack($("mix-bar"), parts, `중요도 분포: ${parts.map((p) => `${p.label} ${p.value}건`).join(", ")}`);
  $("mix-legend").replaceChildren(...parts.map((p) => h("li", {},
    h("span", { class: "count" }, num(p.value),
      h("span", { class: "share" }, shown.length ? `${Math.round((p.value / shown.length) * 100)}%` : "–")),
    h("span", { class: "label" }, h("span", { class: "swatch", style: `background:${p.color}` }), p.label))));
}

// 새로 잡힌 이슈: 오늘은 3시간 단위, 그 밖의 기간은 하루 단위
function renderTrend(shown) {
  const now = Date.now();
  const len = view.period === 1 ? SLOT : 24 * HOUR;
  const first = view.period === 1 ? dayStart(now) : dayStart(periodStart().getTime());
  const last = view.period === 1 ? slotStart(now) : dayStart(now);
  const bins = [];
  for (let t = first; t <= last; t += len) bins.push(t);
  const values = bins.map((t) => shown.filter((i) => {
    const seen = new Date(i.first_seen).getTime();
    return seen >= t && seen < t + len;
  }).length);
  const labels = bins.map((t) => (view.period === 1 ? `${kst(t).getUTCHours()}시` : shortDay(t)));
  const names = bins.map((t) => (view.period === 1 ? slotText(t) : longDay(t)));

  $("trend-note").textContent = view.period === 1 ? "3시간 단위" : "하루 단위";
  Charts.columns($("trend-chart"), {
    labels, values, highlight: bins.length - 1, height: 176,
    tipText: (i) => [`${num(values[i])}건`, names[i]],
    caption: `새로 잡힌 이슈 수, ${names[0]}부터 ${names[names.length - 1]}까지`,
  });
}

// 분야별 이슈: 누르면 그 분야만 봄. 필터 줄의 분야 선택과 같은 값
function renderCategories() {
  const counts = new Map();
  for (const issue of issues.filter((i) => matches(i, { ignoreCategory: true }))) {
    counts.set(categoryOf(issue), (counts.get(categoryOf(issue)) || 0) + 1);
  }
  const names = [...(meta?.categories || []), "기타", "미분류"].filter((c, i, all) =>
    all.indexOf(c) === i && (counts.get(c) || view.category === c));
  const total = [...counts.values()].reduce((a, b) => a + b, 0);

  $("category").replaceChildren(
    h("option", { value: "" }, `모든 분야 (${num(total)})`),
    ...names.map((c) => h("option", { value: c, selected: view.category === c }, `${c} (${num(counts.get(c) || 0)})`)));

  const ranked = names.map((c) => ({ label: c, value: counts.get(c) || 0, selected: view.category === c }))
    .sort((a, b) => b.value - a.value);
  let rows = ranked.slice(0, LIST_ROWS);
  const picked = ranked.find((r) => r.selected);
  if (picked && !rows.includes(picked)) rows = [...rows.slice(0, LIST_ROWS - 1), picked];
  const others = ranked.filter((r) => !rows.includes(r));

  $("cat-note").textContent = `${names.length}개 분야`;
  Charts.barList($("category-chart"), rows, {
    caption: "분야별 이슈 수",
    onSelect: (r) => { view.category = view.category === r.label ? null : r.label; render(); },
  });
  if (others.length) {
    $("category-chart").append(h("p", { class: "barlist-more" },
      `그 밖의 ${others.length}개 분야 ${num(others.reduce((s, r) => s + r.value, 0))}건`));
  }
  if (!rows.length) $("category-chart").replaceChildren(h("p", { class: "barlist-more" }, "이 조건에 맞는 이슈가 없습니다."));
}

// 많이 나온 기업: 누르면 그 기업으로 검색
function renderCompanies(shown) {
  const counts = new Map();
  for (const issue of shown) for (const name of new Set(issue.companies || [])) counts.set(name, (counts.get(name) || 0) + 1);
  const rows = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ko"))
    .slice(0, LIST_ROWS)
    .map(([label, value]) => ({ label, value, selected: view.company === label }));
  if (!rows.length) {
    $("company-chart").replaceChildren(h("p", { class: "barlist-more" }, "이 조건의 이슈에는 기업 이름이 없습니다."));
    return;
  }
  Charts.barList($("company-chart"), rows, {
    thin: true,
    caption: "많이 나온 기업과 이슈 수",
    onSelect: (r) => { view.company = r.selected ? null : r.label; render(); },
  });
}

// ── 화면 2. 수집 현황 ────────────────────────────────────────

function renderMeta() {
  if (meta.ai) $("ai-name").textContent = meta.ai;
  const lastRun = meta.last_run_at;
  if (!lastRun) {
    $("freshness").textContent = "실행 전";
    if (meta.mode === "local") {
      showNotice("아직 실행 기록이 없습니다. 오른쪽 위 새로고침 버튼을 누르면 내 PC에서 뉴스 수집을 시작합니다.");
    }
  }
  if (lastRun) {
    $("freshness").textContent = `업데이트 ${ago(lastRun)}`;
    $("freshness").title = clock(lastRun);
    const hours = (Date.now() - new Date(lastRun)) / 3600000;
    $("stale").hidden = hours <= STALE_HOURS;
    if (hours > STALE_HOURS) {
      $("stale").textContent = meta.mode === "local"
        ? `마지막 업데이트가 ${Math.floor(hours)}시간 전입니다. 오른쪽 위 새로고침 버튼으로 업데이트하세요.`
        : `자동 업데이트가 ${Math.floor(hours)}시간째 멈춰 있습니다. GitHub Actions 실행 기록을 확인하세요.`;
    }
  }
  renderOps();
}

function runState(run) {
  if (run.alerts && run.alerts.length) return "alert";
  if ((run.sources_ok ?? 0) < (run.sources_total ?? 0)) return "partial";
  return "ok";
}
const STATE_TEXT = { ok: "정상", partial: "일부 매체 실패", alert: "경고", none: "실행 없음" };

function renderOps() {
  const now = Date.now();
  const runs = meta.runs || [];
  const sum = (list, key) => list.reduce((s, r) => s + (r[key] || 0), 0);

  // 한 줄 요약: 매체 상태 · 마지막 실행 · 24시간 비용
  const day = runs.filter((r) => new Date(r.at) >= now - 24 * HOUR);
  const okSources = meta.sources.filter((s) => s.ok !== false).length;
  const failing = meta.sources.length - okSources;
  const shaky = day.filter((r) => (r.sources_ok ?? 0) < (r.sources_total ?? 0)).length;
  $("ops-finding").replaceChildren(h("span", {},
    "매체 ", h("strong", {}, `${meta.sources.length}곳`),
    failing ? [" 중 ", h("strong", {}, `${failing}곳 수집 실패`)] : " 지금 모두 정상",
    shaky ? ` (최근 24시간 실행 ${day.length}회 중 ${shaky}회는 일부 매체 실패)` : "",
    meta.last_run_at ? ` · 마지막 실행 ${clock(meta.last_run_at)}` : " · 아직 실행 전",
    ` · 최근 24시간 AI 비용 ${usd(sum(day, "cost_usd"))}`));

  // 기사가 이슈가 되기까지 (최근 24시간)
  const relevant = sum(day, "relevant");
  const made = sum(day, "new_issues");
  const summarized = sum(day, "summarized");
  const waiting = day.length ? day[day.length - 1].pending || 0 : 0;
  const cells = [
    { label: "관련 기사", value: num(relevant), sub: `매체 ${meta.sources.length}곳에서 새로 수집` },
    { label: "새 이슈", value: num(made), sub: made ? `관련 기사 ${(relevant / made).toFixed(1)}건당 1개` : "같은 사건끼리 묶음", arrow: true },
    { label: "요약", value: num(summarized), sub: `새 이슈와 기사가 늘어난 이슈 · 대기 ${num(waiting)}건`, arrow: true },
    { label: "AI 비용", value: usd(sum(day, "cost_usd")), sub: `실행 ${num(day.length)}회` },
  ];
  $("pipeline").replaceChildren(...cells.map((c) => h("li", {},
    c.arrow ? h("span", { class: "arrow", "aria-hidden": "true" }, "→") : null,
    h("div", { class: "label" }, c.label),
    h("div", { class: "value num" }, c.value),
    h("div", { class: "sub" }, c.sub))));

  // 실행 기록: 3시간 칸마다 상태 띠, 그 아래 관련 기사 수
  const end = slotStart(now);
  const slots = [];
  for (let t = end - (OPS_DAYS * 8 - 1) * SLOT; t <= end; t += SLOT) slots.push(t);
  const inSlot = slots.map((t) => runs.filter((r) => { const at = new Date(r.at).getTime(); return at >= t && at < t + SLOT; }));
  const states = inSlot.map((list) => {
    if (!list.length) return "none";
    const found = list.map(runState);
    return found.includes("alert") ? "alert" : found.includes("partial") ? "partial" : "ok";
  });
  Charts.tracker($("tracker"), slots.map((t, i) => {
    const list = inSlot[i];
    const worst = list.reduce((m, r) => Math.min(m, r.sources_ok ?? 0), Infinity);
    const total = list.length ? list[0].sources_total : 0;
    const detail = list.length ? ` · 실행 ${list.length}회 · 매체 ${worst}/${total}` : "";
    return { state: states[i], lines: [`${STATE_TEXT[states[i]]}${detail}`, slotText(t)] };
  }), "최근 7일 3시간 단위 실행 상태");
  $("tracker-legend").replaceChildren(...["ok", "partial", "alert", "none"].filter((s) => states.includes(s)).map((s) =>
    h("li", {}, h("span", { class: `swatch tracker-cell is-${s}` }), STATE_TEXT[s])));

  const relevantBySlot = inSlot.map((list) => sum(list, "relevant"));
  Charts.columns($("runs-chart"), {
    labels: slots.map(shortDay),
    xAt: slots.map((t, i) => (kst(t).getUTCHours() === 0 ? i : -1)).filter((i) => i >= 0),
    values: relevantBySlot,
    height: 150,
    tipText: (i) => [`관련 기사 ${num(relevantBySlot[i])}건`, slotText(slots[i])],
    caption: "최근 7일 3시간 단위 관련 기사 수",
  });

  // 수집 매체
  $("sources-note").textContent = failing ? `${meta.sources.length}곳 중 ${failing}곳 실패` : `${meta.sources.length}곳 정상`;
  $("sources").style.setProperty("--rows", Math.ceil(meta.sources.length / 2));  // 두 줄로 나눠 세로로 채움
  $("sources").replaceChildren(...meta.sources.map((s) => {
    const state = s.ok === false
      ? h("span", { class: "state state-bad" }, `실패 ${s.fail_streak}회 연속`)
      : h("span", { class: "state state-quiet" }, s.ok ? "정상" : "확인 전");
    return h("li", {},
      h("span", { class: "name", title: s.name }, s.name, s.lang === "en" ? h("span", { class: "lang" }, "영문") : null),
      h("span", { class: "when" }, s.last_new_at ? `새 기사 ${ago(s.last_new_at)}` : "새 기사 없음"),
      state);
  }));

  // 기사를 많이 낸 매체 (최근 7일 이슈에 묶인 기사)
  const since = now - OPS_DAYS * 24 * HOUR;
  const outlets = new Map();
  for (const issue of issues) {
    if (new Date(issue.updated_at) < since) continue;
    for (const a of issue.articles) outlets.set(a.source, (outlets.get(a.source) || 0) + 1);
  }
  const outletRows = [...outlets].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([label, value]) => ({ label, value }));
  if (outletRows.length) Charts.barList($("outlet-chart"), outletRows, { caption: "최근 7일 매체별 기사 수" });
  else $("outlet-chart").replaceChildren(h("p", { class: "barlist-more" }, "최근 7일 동안 이슈에 묶인 기사가 없습니다."));

  // 최근 실행 8회
  const cell = (value, text = num(value)) =>
    value === null || value === undefined ? h("td", { class: "num zero" }, "–") : h("td", { class: value ? "num" : "num zero" }, text);
  $("runs").replaceChildren(...[...runs].reverse().slice(0, 8).map((r) =>
    h("tr", { class: r.alerts && r.alerts.length ? "alert" : null, title: (r.alerts || []).join("\n") || null },
      h("td", {}, clock(r.at)),
      cell(r.relevant),
      cell(r.new_issues),
      cell(r.summarized),
      h("td", { class: r.sources_ok < r.sources_total ? "num" : "num zero" }, `${r.sources_ok ?? "–"}/${r.sources_total ?? "–"}`),
      cell(r.cost_usd, usd(r.cost_usd)))));
}

// ── 화면 바꾸기 (브리핑 / 수집 현황) ─────────────────────────

function showView() {
  const name = location.hash === "#ops" ? "ops" : "brief";
  $("view-brief").hidden = name !== "brief";
  $("view-ops").hidden = name !== "ops";
  for (const tab of document.querySelectorAll(".tabs a")) {
    if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  document.title = name === "ops" ? "수집 현황 · 칩앤패널 브리핑" : "반도체·디스플레이 뉴스 브리핑";
}

// ── 입력 연결 ───────────────────────────────────────────────

function setQuery(text) {
  $("query").value = text;
  view.query = text.trim().toLowerCase();
  render();
  if (!text) return;
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: $("query").getBoundingClientRect().top + window.scrollY - 80, behavior: smooth ? "smooth" : "auto" });
}

function bindSegmented(id, key, parse) {
  const group = $(id);
  const mark = (pressed) => {
    for (const b of group.querySelectorAll("button")) {
      b.classList.toggle("on", b === pressed);
      b.setAttribute("aria-pressed", String(b === pressed));
    }
  };
  mark(group.querySelector("button.on"));
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    mark(button);
    view[key] = parse(button.dataset.value);
    render();
  });
}

function fetchJson(url) {
  return fetch(url, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });
}

// version: 주소 끝에 붙여 예전 파일이 캐시에서 나오지 않게 하는 값
async function load(version = window.BUILD || Date.now()) {
  const v = encodeURIComponent(version);
  try {
    const [issueData, metaData] = await Promise.all([
      fetchJson(`data/issues.json?v=${v}`),
      fetchJson(`data/meta.json?v=${v}`),
    ]);
    issues = issueData.issues || [];
    meta = metaData;
  } catch (error) {
    $("timeline").replaceChildren(h("p", { class: "empty" }, "데이터를 불러오지 못했습니다. 잠시 후 새로고침해 보세요."));
    return;
  }
  renderMeta();
  render();
  resumeLocalJob();
}

// ── 지금 업데이트 ───────────────────────────────────────────
// 이 페이지는 정적 파일이라 직접 Agent를 돌릴 수 없으므로, GitHub에 "지금 실행"을 요청한다.
// 요청에는 저장소 주인이 만든 토큰(Actions 쓰기 권한만)이 필요하며, 토큰은 이 브라우저에만 저장한다.

const TOKEN_KEY = "semicon-news:github-token";
const POLL_MS = 20000;              // 새 결과가 올라왔는지 20초마다 확인
const POLL_LIMIT_MS = 12 * 60000;   // 12분이 지나도 안 바뀌면 그만 기다림
let updating = false;

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
function saveToken(value) {
  try { localStorage.setItem(TOKEN_KEY, value); return true; } catch { return false; }
}
function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* 저장소를 쓸 수 없는 환경 */ }
}

function setUpdating(on, text) {
  updating = on;
  const button = $("refresh");
  button.classList.toggle("running", on);
  button.setAttribute("aria-busy", String(on));
  button.title = on ? "업데이트 중" : "지금 업데이트";
  if (text) $("freshness").textContent = text;
  else if (meta) renderMeta();
}

async function dispatchUpdate(token) {
  const res = await fetch(`https://api.github.com/repos/${meta.repo}/actions/workflows/update.yml/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status === 204) return;
  if (res.status === 401) throw new Error("토큰이 올바르지 않거나 만료되었습니다. 새 토큰을 등록해 주세요.");
  if (res.status === 403 || res.status === 404) {
    throw new Error("이 토큰에는 이 저장소의 Actions 쓰기 권한이 없습니다. 토큰 설정을 확인해 주세요.");
  }
  throw new Error(`GitHub 요청이 실패했습니다 (${res.status}). 잠시 후 다시 시도해 주세요.`);
}

async function startUpdate() {
  if (updating) return;
  if (meta?.mode === "local") return startLocalUpdate();
  const token = readToken();
  if (!token || !meta?.repo) {
    openUpdateDialog();
    return;
  }
  setUpdating(true, "업데이트 요청 중…");
  try {
    await dispatchUpdate(token);
  } catch (error) {
    setUpdating(false);
    openUpdateDialog(error.message);
    return;
  }
  setUpdating(true, "업데이트 실행 중 · 약 3~4분");
  waitForNewData(meta.last_run_at);
}

function waitForNewData(previousRunAt) {
  const started = Date.now();
  const timer = setInterval(async () => {
    try {
      const latest = await fetchJson(`data/meta.json?t=${Date.now()}`);
      if (latest.last_run_at && latest.last_run_at !== previousRunAt) {
        clearInterval(timer);
        await load(Date.now());
        setUpdating(false);
        return;
      }
    } catch { /* 배포 중에는 잠깐 못 읽을 수 있음. 다음 확인에서 다시 시도 */ }
    if (Date.now() - started > POLL_LIMIT_MS) {
      clearInterval(timer);
      setUpdating(false);
      $("stale").textContent = "업데이트가 아직 반영되지 않았습니다. GitHub Actions에서 실행 상태를 확인해 주세요.";
      $("stale").hidden = false;
    }
  }, POLL_MS);
}

// ── 내 PC 모드 (python -m agent.serve) ──────────────────────
// 새로고침 버튼이 GitHub 대신 이 컴퓨터의 로컬 서버에 실행을 요청한다. 토큰이 필요 없다.

const LOCAL_POLL_MS = 3000;
const LOCAL_MAX_FAILURES = 10;          // 상태 확인이 연속 10번(약 30초) 실패하면 서버가 꺼진 것으로 봄
const LOCAL_LIMIT_MS = 25 * 60000;      // 25분이 지나도 안 끝나면 그만 기다림
let localTimer = null;

function showNotice(text) {
  $("stale").textContent = text;
  $("stale").hidden = false;
}

async function startLocalUpdate() {
  setUpdating(true, "내 PC에서 업데이트 시작…");
  let res;
  try {
    res = await fetch("api/update", {
      method: "POST",
      headers: { "X-Agent-Request": "1", "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    setUpdating(false);
    showNotice("로컬 서버에 연결하지 못했습니다. python -m agent.serve 가 켜져 있는지 확인하세요.");
    return;
  }
  if (res.status !== 202 && res.status !== 409) {  // 409 = 이미 실행 중 → 그 실행을 기다림
    setUpdating(false);
    showNotice(`업데이트를 시작하지 못했습니다 (${res.status}).`);
    return;
  }
  const body = await res.json().catch(() => ({}));
  watchLocalJob(body.started_at);
}

// startedAt: 기다리는 실행의 시작 시각. 서버가 다시 켜져 다른 실행이 되면 '중단'으로 알림
function watchLocalJob(startedAt) {
  if (localTimer) return;  // 이미 기다리는 중이면 또 만들지 않음
  setUpdating(true, "업데이트 실행 중 (내 PC) · 1~3분");
  let failures = 0;
  const deadline = Date.now() + LOCAL_LIMIT_MS;
  const finish = (notice) => {
    clearInterval(localTimer);
    localTimer = null;
    setUpdating(false);
    if (notice) showNotice(notice);
  };
  localTimer = setInterval(async () => {
    let status;
    try {
      status = await fetchJson(`api/status?t=${Date.now()}`);
      failures = 0;
    } catch {
      if (++failures >= LOCAL_MAX_FAILURES) finish("로컬 서버가 응답하지 않습니다. 서버 창이 켜져 있는지 확인하세요.");
      return;
    }
    if (status.running) {
      if (Date.now() > deadline) finish("업데이트가 너무 오래 걸립니다. 서버 창의 로그를 확인하세요.");
      return;
    }
    if (startedAt && status.started_at !== startedAt) {
      finish("업데이트가 중단되었습니다 (로컬 서버가 다시 시작됨). 새로고침 버튼으로 다시 실행하세요.");
      return;
    }
    await load(Date.now());
    if (status.ok === false) finish(`업데이트 실패: ${status.message}`);
    else if (status.alerts?.length) finish(`업데이트 완료 · 경고: ${status.alerts.join(" / ")}`);
    else finish(null);
  }, LOCAL_POLL_MS);
}

// 페이지를 열었을 때 이미 실행 중이면(예약 실행 등) 진행 상황을 이어서 보여줌
async function resumeLocalJob() {
  if (meta?.mode !== "local" || updating || localTimer) return;
  try {
    const status = await fetchJson(`api/status?t=${Date.now()}`);
    if (status.running && !updating && !localTimer) watchLocalJob(status.started_at);
  } catch { /* 로컬 서버 없이 파일만 연 경우 */ }
}

function openUpdateDialog(errorMessage) {
  const repo = meta?.repo || "";
  $("actions-link").href = repo ? `https://github.com/${repo}/actions/workflows/update.yml` : "https://github.com";
  $("repo-name").textContent = repo || "이 저장소";
  $("dialog-error").textContent = errorMessage || "";
  $("dialog-error").hidden = !errorMessage;
  $("token-clear").hidden = !readToken();
  $("token-input").value = "";
  $("update-dialog").showModal();
}

// 다른 사이트가 이 페이지를 iframe에 몰래 넣어 버튼을 누르게 하는 것(클릭재킹)을 막기 위해,
// 다른 페이지 안에 들어가 있으면 새로고침 버튼을 숨김 (GitHub Pages는 막는 헤더를 보낼 수 없어서 화면에서 처리)
if (window.top !== window.self) {
  $("refresh").hidden = true;
} else {
  $("refresh").addEventListener("click", startUpdate);
}
$("token-save").addEventListener("click", () => {
  const value = $("token-input").value.trim();
  if (!/^(github_pat_|ghp_)\w+$/.test(value)) {
    $("dialog-error").textContent = "GitHub 토큰은 github_pat_ 로 시작합니다. 다시 확인해 주세요.";
    $("dialog-error").hidden = false;
    return;
  }
  if (!saveToken(value)) {
    $("dialog-error").textContent = "이 브라우저에는 토큰을 저장할 수 없습니다. 방법 1을 이용해 주세요.";
    $("dialog-error").hidden = false;
    return;
  }
  $("update-dialog").close();
  startUpdate();
});
$("token-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("token-save").click(); }
});
$("token-clear").addEventListener("click", () => {
  clearToken();
  $("token-clear").hidden = true;
  $("dialog-error").textContent = "저장된 토큰을 삭제했습니다.";
  $("dialog-error").hidden = false;
});

bindSegmented("period", "period", Number);
bindSegmented("region", "region", String);
$("importance").addEventListener("change", (e) => { view.minImp = Number(e.target.value); render(); });
$("category").addEventListener("change", (e) => { view.category = e.target.value || null; render(); });
window.addEventListener("hashchange", () => { showView(); window.scrollTo(0, 0); });
showView();
let typing;
$("query").addEventListener("input", (e) => {
  clearTimeout(typing);
  typing = setTimeout(() => { view.query = e.target.value.trim().toLowerCase(); render(); }, 150);
});
load();
