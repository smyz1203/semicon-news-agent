// 반도체·디스플레이 뉴스 브리핑 — 화면 그리기
// 안전: 기사 제목·AI 요약 같은 외부 텍스트는 textContent로만 넣고 innerHTML은 쓰지 않는다.

"use strict";

const KST = "Asia/Seoul";
const STALE_HOURS = 3;   // 1시간마다 자동 실행하므로 3시간 넘게 그대로면 멈춘 것으로 봄
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

  // 이 Agent가 한 일을 한 줄로: 관련 기사 몇 건을 이슈 몇 개로 묶었는지
  const dayAgo = Date.now() - 86400000;
  const recentRuns = meta.runs.filter((r) => new Date(r.at) >= dayAgo);
  const articles = recentRuns.reduce((sum, r) => sum + (r.relevant || 0), 0);
  const newIssues = issues.filter((i) => new Date(i.first_seen) >= dayAgo).length;
  const sourceCount = meta.sources.length;
  $("pipeline").replaceChildren(
    "최근 24시간 동안 관련 기사 ", h("strong", {}, articles.toLocaleString("ko-KR")), "건을 이슈 ",
    h("strong", {}, newIssues.toLocaleString("ko-KR")), `개로 묶어 요약했습니다. 국내·해외 매체 ${sourceCount}곳을 1시간마다 확인합니다.`);

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
  startAutoWatch();
}

// ── 업데이트 (GitHub Actions) ───────────────────────────────
// 이 페이지는 정적 파일이라 직접 Agent를 돌리지 않는다. 실행은 GitHub Actions가 한다.
//   - 1시간마다 자동 실행, 또는 휴대폰 GitHub 앱 등에서 "Run workflow"로 실행
//   - 페이지는 실행이 시작되면 스스로 알아채 진행 상황을 보여주고, 끝나면 저절로 새로고침 (토큰 불필요: 공개 저장소)
//   - 새로고침 버튼: 이 브라우저에 토큰을 저장해 둔 경우(주인 PC)만 바로 실행 요청, 아니면 최신 결과 확인
//   - 토큰 저장 창은 주소 끝에 #setup 을 붙여 열 때만 나옴 (다른 컴퓨터에서 토큰을 묻지 않도록)

const TOKEN_KEY = "semicon-news:github-token";
const RUN_POLL_MS = 8000;                 // (토큰 있음) 실행 상황·새 결과를 8초마다 확인
const PUBLIC_RUN_POLL_MS = 30000;         // (토큰 없음) GitHub API는 토큰 없이 시간당 60회까지라 30초마다
const AUTO_META_MS = 60000;               // 새 결과가 올라왔는지 1분마다 확인
const AUTO_RUN_MS = 120000;               // 실행이 시작됐는지 2분마다 확인
const FIND_RUN_LIMIT_MS = 3 * 60000;      // 요청한 실행이 3분 안에 GitHub에 안 보이면 그만 기다림
const RUN_LIMIT_MS = 40 * 60000;          // 실행이 40분 넘게 끝나지 않으면 그만 기다림 (Actions 제한 시간)
const FAILED_GRACE_MS = 90000;            // 실행이 실패로 끝나면 90초만 더 기다려 봄
const DEPLOY_GRACE_MS = 3 * 60000;        // 실행이 끝난 뒤 페이지에 반영되기까지 최대 3분
const COOLDOWN_MS = 5 * 60000;            // 마지막 실행이 시작된 뒤 5분 안에는 다시 시작하지 않음
const RUNNING = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
let updating = false;
let watching = false;

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

// 잠깐 상단 시각 자리에 메시지를 보여줬다가 원래대로
function flashFreshness(text) {
  $("freshness").textContent = text;
  setTimeout(() => { if (!updating && meta) renderMeta(); }, 4000);
}

// token이 없으면 인증 없이 요청 (공개 저장소의 실행 기록은 누구나 볼 수 있음)
function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function githubError(status) {
  if (status === 401) return new Error("토큰이 올바르지 않거나 만료되었습니다. 새 토큰이 필요합니다.");
  if (status === 403 || status === 404) {
    return new Error("이 토큰에는 이 저장소의 Actions 실행 권한이 없습니다. 토큰 설정을 확인해 주세요.");
  }
  return new Error(`GitHub 요청이 실패했습니다 (${status}). 잠시 후 다시 시도해 주세요.`);
}

// GitHub API 호출 (네트워크 오류는 알아보기 쉬운 한국어 메시지로)
async function githubFetch(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    throw Object.assign(new Error("GitHub에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요."), { network: true });
  }
}

// 가장 최근 뉴스 업데이트 실행: state는 none / running / done
async function latestRun(token) {
  const res = await githubFetch(
    `https://api.github.com/repos/${meta.repo}/actions/workflows/update.yml/runs?per_page=1`,
    { headers: githubHeaders(token), cache: "no-store" },
  );
  if (!res.ok) throw githubError(res.status);
  const run = ((await res.json()).workflow_runs || [])[0];
  if (!run) return { state: "none" };
  return {
    id: run.id,
    state: RUNNING.has(run.status) ? "running" : "done",
    status: run.status,
    conclusion: run.conclusion,
    created_at: run.created_at,
  };
}

async function dispatchUpdate(token) {
  const res = await githubFetch(`https://api.github.com/repos/${meta.repo}/actions/workflows/update.yml/dispatches`, {
    method: "POST",
    headers: { ...githubHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main" }),
  });
  if (res.status !== 204 && res.status !== 200) throw githubError(res.status);
}

// 이 실행의 결과가 이미 페이지에 올라와 있는지 (Agent 실행 시각은 항상 실행 생성 시각보다 뒤)
const publishedFor = (runAt, run) => Boolean(runAt && run?.created_at && Date.parse(runAt) >= Date.parse(run.created_at));

async function startUpdate() {
  if (updating) return;
  if (meta?.mode === "local") return startLocalUpdate();
  $("stale").hidden = true;  // 지난 안내 문구 지우기
  setUpdating(true, "최신 결과 확인 중…");
  // 오래 열어 둔 페이지일 수 있으니 최신 데이터부터 다시 불러옴
  const before = meta?.last_run_at;
  await load(Date.now());
  if (!meta?.repo) {
    setUpdating(false);
    showNotice("데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.");
    return;
  }
  const token = readToken();
  if (!token) return checkWithoutToken(before);
  setUpdating(true, "업데이트 요청 중…");
  try {
    const current = await latestRun(token);
    if (current.state === "running") {
      if (publishedFor(meta.last_run_at, current)) {
        // 결과는 이미 화면에 있고 마무리 점검만 남은 실행
        setUpdating(false);
        showNotice("방금 업데이트된 결과입니다. 잠시 후 다시 눌러 주세요.");
        return;
      }
      watchRun(token, { runId: current.id });  // 이미 실행 중이면 새로 시작하지 않고 그 실행을 기다림
      return;
    }
    const sinceLast = current.created_at ? Date.now() - Date.parse(current.created_at) : Infinity;
    if (sinceLast < COOLDOWN_MS) {
      setUpdating(false);
      showNotice(`방금 업데이트가 실행됐어요. ${Math.max(1, Math.ceil((COOLDOWN_MS - sinceLast) / 60000))}분 뒤에 다시 눌러 주세요.`);
      return;
    }
    await dispatchUpdate(token);
    watchRun(token, { beforeId: current.id ?? null });
  } catch (error) {
    setUpdating(false);
    if (error.network) showNotice(error.message);
    else openUpdateDialog(error.message);  // 저장해 둔 토큰이 만료되는 등 (주인 PC에서만 생기는 경우)
  }
}

// 토큰이 없는 컴퓨터(면접관 PC 등): 실행을 요청하지 않고, 진행 중인 실행이 있으면 그걸 보여주고 아니면 최신 결과만 확인
async function checkWithoutToken(previousRunAt) {
  try {
    const run = await latestRun(null);
    if (run.state === "running" && !publishedFor(meta.last_run_at, run)) {
      watchRun(null, { runId: run.id });
      return;
    }
  } catch { /* GitHub 확인이 안 돼도 최신 결과 확인은 이미 했음 */ }
  setUpdating(false);
  flashFreshness(meta.last_run_at !== previousRunAt ? "새 뉴스로 업데이트했어요" : "최신 결과예요");
}

// 페이지를 열어 두는 동안: 새 결과가 올라오면 저절로 새로고침하고, 실행이 시작되면 진행 상황을 보여줌.
// (휴대폰 GitHub 앱이나 1시간마다 자동 실행으로 업데이트가 돌면 면접관 화면이 스스로 갱신됨)
let autoWatchStarted = false;
function startAutoWatch() {
  if (autoWatchStarted || meta?.mode !== "github") return;
  autoWatchStarted = true;
  let lastRunCheck = 0;
  const tick = async () => {
    if (document.visibilityState === "visible" && !updating && !watching) {
      try {
        const latest = await fetchJson(`data/meta.json?t=${Date.now()}`);
        if (latest.last_run_at && latest.last_run_at !== meta.last_run_at && !updating && !watching) {
          await load(Date.now());
          flashFreshness("새 뉴스로 업데이트했어요");
        }
      } catch { /* 다음 확인에서 다시 시도 */ }
      if (Date.now() - lastRunCheck >= AUTO_RUN_MS && !updating && !watching) {
        lastRunCheck = Date.now();
        try {
          const token = readToken() || null;
          const run = await latestRun(token);
          if (run.state === "running" && !publishedFor(meta.last_run_at, run) && !updating && !watching) {
            watchRun(token, { runId: run.id });
          }
        } catch { /* 토큰 없이 확인하는 횟수 제한에 걸리면 다음에 다시 */ }
      }
    }
    setTimeout(tick, AUTO_META_MS);
  };
  tick();
}

// 실행 상황을 보여주다가, 그 실행의 결과가 페이지에 올라오면 다시 불러온다.
// 어떤 실행인지는 컴퓨터 시계가 아니라 GitHub 실행 번호로 구분한다.
//   runId: 이미 돌고 있던 실행을 기다릴 때 그 번호
//   beforeId: 방금 실행을 요청했을 때, 요청 직전의 최근 실행 번호 (이것과 다른 새 실행이 '우리 실행')
function watchRun(token, { runId = null, beforeId = null }) {
  if (watching) return;
  watching = true;
  const started = Date.now();
  let ours = null;       // 우리 실행 (찾으면 채움)
  let doneAt = null;
  let stopped = false;
  let timer = null;

  const finish = (notice) => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    watching = false;
    setUpdating(false);
    if (notice) showNotice(notice);
  };

  const tick = async () => {
    // 1) GitHub 실행 상황
    try {
      const run = await latestRun(token);
      if (stopped) return;
      const isOurs = runId ? run.id === runId : (run.id && run.id !== beforeId);
      if (isOurs) ours = run;
    } catch { /* 다음 확인에서 다시 시도 */ }

    // 2) 우리 실행의 결과가 페이지에 올라왔으면 다시 불러오고 끝
    try {
      const latest = await fetchJson(`data/meta.json?t=${Date.now()}`);
      if (stopped) return;
      if (latest.last_run_at !== meta.last_run_at) await load(Date.now());  // 더 새로운 데이터는 일단 보여줌
      if (ours && publishedFor(latest.last_run_at, ours)) {
        const warned = ours.state === "done" && ours.conclusion !== "success";
        finish(warned ? "업데이트는 반영됐지만 경고가 있습니다. 아래 '최근 실행'을 확인하세요." : null);
        if (!warned) flashFreshness("새 뉴스로 업데이트했어요");
        return;
      }
    } catch { /* 배포 중에는 잠깐 못 읽을 수 있음 */ }
    if (stopped) return;

    // 3) 진행 상황 표시와 그만 기다릴 조건
    const minutes = Math.floor((Date.now() - started) / 60000);
    if (!ours) {
      $("freshness").textContent = "업데이트 대기 중…";
      if (Date.now() - started > FIND_RUN_LIMIT_MS) return finish("요청한 업데이트가 시작되지 않았습니다. GitHub Actions 실행 기록을 확인하세요.");
    } else if (ours.state === "running") {
      $("freshness").textContent = ours.status === "queued" ? "업데이트 대기 중…" : `뉴스 수집·요약 중 · ${minutes}분 경과`;
      if (Date.now() - started > RUN_LIMIT_MS) return finish("업데이트가 너무 오래 걸립니다. GitHub Actions 실행 기록을 확인하세요.");
    } else {
      doneAt = doneAt || Date.now();
      $("freshness").textContent = "페이지에 반영 중…";
      if (ours.conclusion !== "success" && Date.now() - doneAt > FAILED_GRACE_MS) {
        return finish("업데이트 실행이 실패했습니다. GitHub Actions 실행 기록을 확인하세요.");
      }
      if (Date.now() - doneAt > DEPLOY_GRACE_MS) {
        return finish("업데이트는 끝났지만 페이지 반영이 늦어지고 있습니다. 잠시 후 페이지를 새로고침해 보세요.");
      }
    }
    // 이번 확인이 끝난 뒤에 다음 확인을 예약 (겹치지 않게). 토큰 없이 확인할 때는 횟수 제한 때문에 천천히
    timer = setTimeout(tick, token ? RUN_POLL_MS : PUBLIC_RUN_POLL_MS);
  };

  setUpdating(true, "업데이트 시작 중…");
  tick();
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
$("token-clear").addEventListener("click", () => {
  clearToken();
  $("token-clear").hidden = true;
  $("dialog-error").textContent = "저장된 토큰을 삭제했습니다.";
  $("dialog-error").hidden = false;
});

// 주인 PC에서 토큰을 저장할 때만: 주소 끝에 #setup 을 붙여 열면 토큰 저장 창이 뜸
async function openSetupIfRequested() {
  if (location.hash !== "#setup" || window.top !== window.self) return;
  for (let i = 0; i < 50 && !meta; i++) await new Promise((r) => setTimeout(r, 200));
  if (meta) openUpdateDialog();
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
openSetupIfRequested();
