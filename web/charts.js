// 뉴스 브리핑 — 작은 그래프 도구 (라이브러리 없이 SVG와 HTML로 그림)
// 규칙: 막대는 얇게(최대 24px), 위쪽만 4px 둥글게, 바닥은 0에서 시작. 가로 눈금선 3~5개, 눈금은 최댓값보다 위에서 끝남.
// 색은 style.css가 칠한다(.chart-grid, .chart-tick, .chart-mark). 그래서 밝은/어두운 화면이 바뀌면 바로 따라감.
// 글자는 모두 textContent로만 넣는다. 마우스를 올리거나, 키보드로 그래프에 와서 좌우 화살표를 누르면 값이 말풍선으로 보이고,
// 같은 값을 화면 낭독기가 읽을 수 있도록 숨긴 표와 알림 영역에도 넣는다.

"use strict";

(() => {
  const NS = "http://www.w3.org/2000/svg";
  const nf = new Intl.NumberFormat("ko-KR");

  function svg(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) if (value !== null && value !== undefined) node.setAttribute(key, value);
    if (parent) parent.appendChild(node);
    return node;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 0부터 시작하는 정수 눈금(간격 1·2·5·10…). 맨 위 눈금은 최댓값보다 크게 (막대가 천장에 닿지 않게)
  function ticks(max, count = 4) {
    const raw = Math.max(max, 1) / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = Math.max(1, [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw));
    const top = (Math.floor(max / step) + 1) * step;
    const out = [];
    for (let v = 0; v <= top; v += step) out.push(v);
    return out;
  }

  // x축 이름표: 폭에 들어가는 만큼만, 같은 간격으로, 마지막 칸에서 끝나게 고름 (11px 글자 기준)
  function pickLabels(labels, width, at) {
    const candidates = at || labels.map((_, i) => i);
    const longest = Math.max(1, ...candidates.map((i) => String(labels[i]).length));
    const cap = Math.max(2, Math.floor(width / (longest * 6.5 + 12)));
    const step = Math.ceil(candidates.length / cap);
    return candidates.filter((_, k) => (candidates.length - 1 - k) % step === 0);
  }

  // 말풍선 하나와 화면 낭독기용 알림 영역을 그래프 상자 안에 두고 다시 씀
  function tip(box) {
    let node = box.querySelector(".chart-tip");
    let live = box.querySelector(".chart-live");
    if (!node) {
      node = el("div", "chart-tip");
      node.setAttribute("aria-hidden", "true");
      live = el("div", "sr-only chart-live");
      live.setAttribute("aria-live", "polite");
      box.append(node, live);
    }
    return {
      // below=true면 (x, y) 아래에, 아니면 위에 띄움
      show(lines, x, y, below = false) {
        node.replaceChildren(...lines.map((line, i) => el("div", i === 0 ? "tip-value" : "tip-label", line)));
        node.style.opacity = "1";
        const left = Math.max(0, Math.min(x - node.offsetWidth / 2, box.clientWidth - node.offsetWidth));
        node.style.left = `${left}px`;
        node.style.top = `${below ? y + 8 : Math.max(0, y - node.offsetHeight - 8)}px`;
        live.textContent = [...lines].reverse().join(", ");
      },
      hide() { node.style.opacity = "0"; },
    };
  }

  // 키보드: 그래프에 키보드로 오면(마우스 클릭이 아니라) 좌우 화살표로 칸을 옮겨 가며 값을 봄
  // 마우스로 고른 칸도 기억해 두어, 이어서 화살표를 누르면 그 칸에서 시작함
  function keyboard(target, count, onPick, onLeave) {
    let index = count - 1;
    target.setAttribute("tabindex", "0");
    target.addEventListener("focus", () => { if (target.matches(":focus-visible")) onPick(index); });
    target.addEventListener("blur", onLeave);
    target.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: -1, ArrowRight: 1, Home: -count, End: count };
      if (!(event.key in moves)) return;
      event.preventDefault();
      index = Math.max(0, Math.min(count - 1, index + moves[event.key]));
      onPick(index);
    });
    return (i) => { index = i; };
  }

  // 화면 낭독기용 표 (보이지 않음). 표는 폭 1px를 따르지 않으므로 숨김 상자로 감쌈
  function srTable(caption, head, rows) {
    const table = el("table");
    table.append(el("caption", null, `${caption} (표)`));
    const tr = el("tr");
    for (const name of head) tr.append(el("th", null, name));
    table.append(tr);
    for (const row of rows) {
      const line = el("tr");
      for (const cell of row) line.append(el("td", null, cell));
      table.append(line);
    }
    const wrap = el("div", "sr-only");
    wrap.append(table);
    return wrap;
  }

  // 위쪽 모서리만 둥근 막대 (바닥은 각지게 0선에 붙음)
  function barPath(x, y, w, h) {
    const r = Math.min(4, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  /**
   * 세로 막대 그래프.
   * labels: x축 이름표, values: 값, tipText(i): 말풍선 줄 [값, 구간 이름], highlight: 파랑으로 둘 칸(나머지는 회색, null이면 모두 파랑),
   * xAt: 이름표를 붙일 수 있는 칸 번호, caption: 화면 낭독기용 설명.
   * 그린 뒤 바깥 카드에 --plot-left(눈금 글자 폭)를 남겨, 같은 칸을 쓰는 상태 띠가 막대와 줄을 맞출 수 있게 함
   */
  function columns(box, { labels, values, tipText, highlight = null, xAt = null, height = 160, caption = "" }) {
    const draw = () => {
      box.replaceChildren();
      const width = Math.max(200, box.clientWidth);
      const yt = ticks(Math.max(0, ...values));
      const top = yt[yt.length - 1];
      const padL = 12 + 7 * String(nf.format(top)).length;
      const padT = 8;
      const padB = 24;
      const plotH = height - padT - padB;
      const band = (width - padL) / values.length;
      const barW = Math.min(24, Math.max(2, band - 2));  // 막대 사이는 최소 2px 띄움
      const y = (v) => padT + plotH * (1 - v / top);
      box.parentElement.style.setProperty("--plot-left", `${padL}px`);
      const s = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height, role: "img", "aria-label": caption, class: "chart-svg" }, box);

      for (const v of yt) {
        svg("line", { x1: padL, x2: width, y1: y(v), y2: y(v), class: "chart-grid", "shape-rendering": "crispEdges" }, s);
        svg("text", { x: padL - 8, y: y(v) + 4, "text-anchor": "end", class: "chart-tick" }, s).textContent = nf.format(v);
      }
      for (const i of pickLabels(labels, width - padL, xAt)) {
        const cx = padL + band * i + band / 2;
        const anchor = i === 0 && band < 30 ? "start" : i === labels.length - 1 && band < 30 ? "end" : "middle";
        const x = anchor === "start" ? padL + band * i : anchor === "end" ? width : cx;
        svg("text", { x, y: height - 6, "text-anchor": anchor, class: "chart-tick" }, s).textContent = labels[i];
      }

      const marks = values.map((v, i) => {
        if (v <= 0) return null;
        const x = padL + band * i + (band - barW) / 2;
        const muted = highlight !== null && highlight !== i;
        return svg("path", { d: barPath(x, y(v), barW, y(0) - y(v)), class: muted ? "chart-mark is-muted" : "chart-mark" }, s);
      });

      // 보이지 않는 넓은 칸이 마우스를 받음 (가는 막대를 정확히 겨누지 않아도 됨)
      const bubble = tip(box);
      let remember = () => {};
      const pick = (i) => {
        remember(i);
        marks.forEach((m, k) => m && m.classList.toggle("is-picked", k === i));
        s.classList.add("is-picking");
        const scale = box.clientWidth / width;
        bubble.show(tipText(i), (padL + band * i + band / 2) * scale, y(values[i]));
      };
      const leave = () => { marks.forEach((m) => m && m.classList.remove("is-picked")); s.classList.remove("is-picking"); bubble.hide(); };
      values.forEach((_, i) => {
        const hit = svg("rect", { x: padL + band * i, y: padT, width: band, height: plotH, fill: "transparent" }, s);
        hit.addEventListener("pointerenter", () => pick(i));
      });
      s.addEventListener("pointerleave", (event) => { if (event.pointerType !== "touch") leave(); });
      remember = keyboard(s, values.length, pick, leave);
      box.append(srTable(caption, ["구간", "값"], values.map((_, i) => {
        const [value, name] = tipText(i);
        return [name || labels[i] || String(i + 1), value];
      })));
    };
    draw();
    watch(box, draw);
  }

  /**
   * 100% 누적 막대 (전체 중 비율). parts: [{ label, value, color }]
   * 조각 사이는 2px 띄움. 값이 0인 조각은 그리지 않음
   */
  function stack(box, parts, caption) {
    const total = parts.reduce((sum, p) => sum + p.value, 0);
    const bar = el("div", "stack");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", caption);
    for (const p of parts) {
      if (!p.value) continue;
      const seg = el("span", "stack-seg");
      seg.style.flexGrow = String(p.value);
      seg.style.background = p.color;
      seg.title = `${p.label} ${nf.format(p.value)}건`;
      bar.append(seg);
    }
    if (!total) bar.classList.add("is-empty");
    box.replaceChildren(bar);
  }

  /**
   * 순위 목록 (행 배경이 곧 막대). rows: [{ label, value, selected }]
   * onSelect가 있으면 행이 버튼이 되어 누르면 그 값으로 걸러 봄. rank면 앞에 순위 번호
   */
  function barList(box, rows, { unit = "건", onSelect = null, rank = false, caption = "" } = {}) {
    const max = Math.max(1, ...rows.map((r) => r.value));
    const list = el("ol", "barlist");
    list.setAttribute("aria-label", caption);
    rows.forEach((r, k) => {
      const item = el("li");
      const row = el(onSelect ? "button" : "div", "barlist-row" + (r.selected ? " is-selected" : ""));
      if (onSelect) {
        row.type = "button";
        row.setAttribute("aria-pressed", String(Boolean(r.selected)));
        row.addEventListener("click", () => onSelect(r));
      }
      const fill = el("span", "barlist-fill");
      fill.style.width = `${(r.value / max) * 100}%`;
      const name = el("span", "barlist-label", r.label);
      name.title = r.label;
      const value = el("span", "barlist-value num", nf.format(r.value));
      value.append(el("span", "unit", unit));
      row.append(fill);
      if (rank) row.append(el("span", "barlist-rank num", String(k + 1)));
      row.append(name, value);
      item.append(row);
      list.append(item);
    });
    box.replaceChildren(list);
  }

  /**
   * 상태 띠 (GitHub Status처럼 칸 하나가 한 구간). cells: [{ state: 'ok'|'partial'|'alert'|'none', lines: [상태, 구간 이름] }]
   * 띠 전체가 마우스를 받아, 칸 사이 틈에 있어도 가장 가까운 칸을 고름. 말풍선은 띠 아래에 띄움
   */
  function tracker(box, cells, caption) {
    box.replaceChildren();
    const strip = el("div", "tracker");
    strip.setAttribute("role", "img");
    strip.setAttribute("aria-label", caption);
    const nodes = cells.map((c) => {
      const cell = el("span", `tracker-cell is-${c.state}`);
      strip.append(cell);
      return cell;
    });
    box.append(strip);
    const bubble = tip(box);
    let remember = () => {};
    const pick = (i) => {
      remember(i);
      nodes.forEach((n, k) => n.classList.toggle("is-picked", k === i));
      strip.classList.add("is-picking");
      const rect = nodes[i].getBoundingClientRect();
      const base = box.getBoundingClientRect();
      bubble.show(cells[i].lines, rect.left - base.left + rect.width / 2, strip.getBoundingClientRect().bottom - base.top, true);
    };
    const leave = () => { nodes.forEach((n) => n.classList.remove("is-picked")); strip.classList.remove("is-picking"); bubble.hide(); };
    strip.addEventListener("pointermove", (event) => {
      const r = strip.getBoundingClientRect();
      pick(Math.max(0, Math.min(cells.length - 1, Math.floor(((event.clientX - r.left) / r.width) * cells.length))));
    });
    strip.addEventListener("pointerleave", (event) => { if (event.pointerType !== "touch") leave(); });
    remember = keyboard(strip, cells.length, pick, leave);
    box.append(srTable(caption, ["구간", "상태"], cells.map((c) => [c.lines[1] || "", c.lines[0]])));
  }

  // 상자 폭이 바뀌면 다시 그림 (막대 폭과 이름표 간격이 폭에 따라 달라짐). 다시 부르면 이전 감시는 끔
  function watch(box, draw) {
    if (box._watch) box._watch.disconnect();
    if (!("ResizeObserver" in window)) return;
    let width = box.clientWidth;
    box._watch = new ResizeObserver(() => {
      if (Math.abs(box.clientWidth - width) > 4) { width = box.clientWidth; draw(); }
    });
    box._watch.observe(box);
  }

  window.Charts = { columns, stack, barList, tracker };
})();
