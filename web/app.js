// 반도체·디스플레이 뉴스 브리핑 — 화면 그리기
// 안전: 기사 제목·AI 요약 같은 외부 텍스트는 textContent로만 넣고 innerHTML은 쓰지 않는다.

"use strict";

const KST = "Asia/Seoul";
const STALE_HOURS = 6;
const TOP_LIMIT = 6;

const view = { period: 3, region: "all", minImp: 0, category: null, query: "" };
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

// ── 필터 ───────────────────────────────────────────────────

function periodStart() {
  if (view.period === 1) {
    return new Date(kstDateKey(new Date().toISOString()) + "T00:00:00+09:00"); // 한국 시간 오늘 0시
  }
  return new Date(Date.now() - view.period * 86400000);
}

function matches(issue, { ignoreCategory = false } = {}) {
  if (new Date(issue.updated_at) < periodStart()) return false;
  if (view.region !== "all" && issue.region !== view.region && issue.region !== "both") return false;
  if (view.minImp && (issue.importance || 0) < view.minImp) return false;
  if (!ignoreCategory && view.category && categoryOf(issue) !== view.category) return false;
  if (view.query) {
    const haystack = [
      issue.headline, ...(issue.summary || []), ...(issue.tags || []), ...(issue.companies || []),
      ...issue.articles.map((a) => a.title),
    ].join(" ").toLowerCase();
    if (!haystack.includes(view.query)) return false;
  }
  return true;
}

const byImportance = (a, b) =>
  (b.importance || 0) - (a.importance || 0) || b.updated_at.localeCompare(a.updated_at);

// ── 이슈 카드 ───────────────────────────────────────────────

function issueCard(issue, headingTag) {
  const pending = issue.status !== "summarized";
  const level = pending ? 0 : issue.importance;
  const sources = new Set(issue.articles.map((a) => a.source)).size;
  const title = issue.headline || issue.articles[issue.articles.length - 1].title;

  const head = h("div", { class: "issue-head" },
    h("span", { class: "badge", title: pending ? "요약 대기" : `중요도 ${level}점` }, pending ? "–" : level),
    h("div", {},
      h(headingTag, { class: "headline" }, title),
      h("div", { class: "meta" },
        issue.category ? h("span", { class: "cat" }, issue.category) : null,
        h("span", {}, `매체 ${sources}곳 · 기사 ${issue.articles.length}건`),
        h("span", { title: clock(issue.updated_at) }, ago(issue.updated_at)),
        pending ? h("span", { class: "pending" }, "요약 대기") : null,
      ),
    ),
  );

  const body = [];
  if (!pending) {
    body.push(h("p", { class: "summary-label" }, "AI 요약"));
    body.push(h("ul", { class: "summary" }, issue.summary.map((line) => h("li", {}, line))));
    if (issue.tags.length) {
      body.push(h("div", { class: "tags" }, issue.tags.map((tag) =>
        h("button", { class: "tag", type: "button", onclick: () => setQuery(tag) }, tag))));
    }
  }

  const articles = [...issue.articles].reverse().map((a) => {
    const url = safeUrl(a.url);
    return h("li", {},
      h("span", { class: "src" }, a.source),
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, a.title) : a.title);
  });
  body.push(h("details", { class: "more" },
    h("summary", {}, pending ? `관련 기사 ${issue.articles.length}건` : `관련 기사 ${issue.articles.length}건 · 판단 근거`),
    pending ? null : h("p", { class: "reason" }, `판단 근거: ${issue.importance_reason}`),
    h("ul", { class: "articles" }, articles),
  ));

  return h("article", { class: `issue imp-${level}` }, head, body);
}

// ── 화면 그리기 ─────────────────────────────────────────────

function render() {
  const shown = issues.filter((i) => matches(i)).sort(byImportance);

  // 주요 이슈: 중요도 4~5점
  const top = shown.filter((i) => i.status === "summarized" && i.importance >= 4).slice(0, TOP_LIMIT);
  $("top").hidden = top.length === 0;
  $("top-list").replaceChildren(...top.map((i) => issueCard(i, "h3")));

  // 전체 이슈: 주요 이슈에 나온 것은 빼고, 날짜(한국 시간)별로
  const topIds = new Set(top.map((i) => i.id));
  const rest = shown.filter((i) => !topIds.has(i.id));
  const days = new Map();
  for (const issue of rest) {
    const key = kstDateKey(issue.updated_at);
    if (!days.has(key)) days.set(key, []);
    days.get(key).push(issue);
  }
  const sections = [...days.keys()].sort().reverse().map((key) =>
    h("section", { class: "day" },
      h("h3", {}, dayLabel(key)),
      h("div", { class: "day-list" }, days.get(key).map((i) => issueCard(i, "h4")))));
  $("timeline").replaceChildren(...sections);
  $("count").textContent = `${shown.length}건`;
  $("empty").hidden = shown.length > 0;

  renderCategories();
}

function renderCategories() {
  const counts = new Map();
  for (const issue of issues.filter((i) => matches(i, { ignoreCategory: true }))) {
    counts.set(categoryOf(issue), (counts.get(categoryOf(issue)) || 0) + 1);
  }
  const names = [...(meta?.categories || []), "기타", "미분류"].filter((c, i, all) =>
    all.indexOf(c) === i && (counts.get(c) || view.category === c));
  const chip = (label, value, n) => h("button", {
    class: view.category === value ? "chip on" : "chip",
    type: "button",
    "aria-pressed": String(view.category === value),
    onclick: () => { view.category = view.category === value || value === null ? null : value; render(); },
  }, label, n === undefined ? null : h("span", { class: "n" }, n));
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  $("categories").replaceChildren(chip("전체", null, total), ...names.map((c) => chip(c, c, counts.get(c) || 0)));
}

function renderMeta() {
  const lastRun = meta.last_run_at;
  if (lastRun) {
    $("freshness").textContent = `업데이트 ${ago(lastRun)}`;
    $("freshness").title = clock(lastRun);
    const hours = (Date.now() - new Date(lastRun)) / 3600000;
    if (hours > STALE_HOURS) {
      $("stale").textContent = `자동 업데이트가 ${Math.floor(hours)}시간째 멈춰 있습니다. GitHub Actions 실행 기록을 확인하세요.`;
      $("stale").hidden = false;
    }
  }

  // 이 Agent가 한 일을 한 줄로: 관련 기사 몇 건을 이슈 몇 개로 묶었는지
  const dayAgo = Date.now() - 86400000;
  const recentRuns = meta.runs.filter((r) => new Date(r.at) >= dayAgo);
  const articles = recentRuns.reduce((sum, r) => sum + (r.relevant || 0), 0);
  const newIssues = issues.filter((i) => new Date(i.first_seen) >= dayAgo).length;
  const sourceCount = meta.sources.length;
  $("pipeline").replaceChildren(
    "최근 24시간 동안 관련 기사 ", h("strong", {}, articles.toLocaleString("ko-KR")), "건을 이슈 ",
    h("strong", {}, newIssues.toLocaleString("ko-KR")), `개로 묶어 요약했습니다. 국내·해외 매체 ${sourceCount}곳을 3시간마다 확인합니다.`);

  $("sources").replaceChildren(...meta.sources.map((s) => {
    let state = "확인 전";
    let cls = "state";
    if (s.ok === false) { state = `실패 ${s.fail_streak}회 연속`; cls = "state bad"; }
    else if (s.last_new_at) state = `새 기사 ${ago(s.last_new_at)}`;
    else if (s.ok) state = "정상";
    return h("li", {}, h("span", {}, `${s.name}${s.lang === "en" ? " (영문)" : ""}`), h("span", { class: cls }, state));
  }));

  $("runs").replaceChildren(...[...meta.runs].reverse().slice(0, 8).map((r) =>
    h("tr", { class: r.alerts && r.alerts.length ? "alert" : null, title: (r.alerts || []).join("\n") || null },
      h("td", {}, clock(r.at)),
      h("td", {}, r.relevant),
      h("td", {}, r.new_issues),
      h("td", {}, r.summarized),
      h("td", {}, `$${(r.cost_usd || 0).toFixed(3)}`))));
}

// ── 입력 연결 ───────────────────────────────────────────────

function setQuery(text) {
  $("query").value = text;
  view.query = text.trim().toLowerCase();
  render();
  window.scrollTo({ top: $("query").getBoundingClientRect().top + window.scrollY - 80, behavior: "smooth" });
}

function bindSegmented(id, key, parse) {
  const group = $(id);
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    for (const b of group.querySelectorAll("button")) b.classList.toggle("on", b === button);
    view[key] = parse(button.dataset.value);
    render();
  });
}

async function load() {
  const v = encodeURIComponent(window.BUILD || Date.now());
  try {
    const [issueData, metaData] = await Promise.all([
      fetch(`data/issues.json?v=${v}`).then((r) => r.json()),
      fetch(`data/meta.json?v=${v}`).then((r) => r.json()),
    ]);
    issues = issueData.issues || [];
    meta = metaData;
  } catch (error) {
    $("timeline").replaceChildren(h("p", { class: "empty" }, "데이터를 불러오지 못했습니다. 잠시 후 새로고침해 보세요."));
    return;
  }
  renderMeta();
  render();
}

bindSegmented("period", "period", Number);
bindSegmented("region", "region", String);
$("importance").addEventListener("change", (e) => { view.minImp = Number(e.target.value); render(); });
let typing;
$("query").addEventListener("input", (e) => {
  clearTimeout(typing);
  typing = setTimeout(() => { view.query = e.target.value.trim().toLowerCase(); render(); }, 150);
});
load();
