/* CDP visualisation kit: stat tiles, line, funnel, stacked columns, bars, customer journey.
   Plain SVG + DOM, no dependencies. Text is inserted with textContent (labels come from data).
   Rules (dataviz skill): one y-axis per chart, thin marks, 4px rounded data ends, 2px surface gaps
   and rings, hover tooltip on every chart, table view on every chart, colour follows the entity. */
(function (w) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const CHANNEL_SLOT = {web: 1, zalo: 2, facebook: 3, email: 4, sales: 5, event: 6, learning: 7, commerce: 8};
  const STAGE_SLOT = {awareness: 1, consideration: 2, intent: 3, purchase: 4, loyalty: 5};
  const channelColor = (c) => `var(--series-${CHANNEL_SLOT[c] || 1})`;
  const stageColor = (s) => `var(--stage-${STAGE_SLOT[s] || 1})`;

  function svg(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function h(tag, attrs, parent, text) {
    const n = document.createElement(tag);
    for (const k in attrs || {}) { if (k === "class") n.className = attrs[k]; else n.setAttribute(k, attrs[k]); }
    if (text !== undefined && text !== null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  function text(parent, x, y, str, attrs) {
    const t = svg("text", Object.assign({x, y}, attrs || {}), parent);
    t.textContent = str;
    return t;
  }

  // ------------------------------------------------------------------ number formats (vi-VN)
  const nf = new Intl.NumberFormat("vi-VN", {maximumFractionDigits: 1});
  function compact(v) {
    v = Number(v) || 0;
    const a = Math.abs(v);
    if (a >= 1e9) return nf.format(v / 1e9) + " tỷ";
    if (a >= 1e6) return nf.format(v / 1e6) + " tr";
    if (a >= 1e3) return nf.format(v / 1e3) + "K";
    return nf.format(v);
  }
  const money = (v) => compact(v) + (Math.abs(v) >= 1e3 ? "" : "đ");
  const pct = (v, d = 0) => (v == null ? "–" : (v * 100).toFixed(d).replace(".", ",") + "%");
  const day = (iso) => { const d = new Date(iso); return `${d.getDate()}/${d.getMonth() + 1}`; };
  const dateTime = (iso) => new Date(iso).toLocaleString("vi-VN", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  // ------------------------------------------------------------------ tooltip
  let tipEl = null;
  function tip(evt, title, rows) {
    if (!tipEl) { tipEl = h("div", {class: "viz-tip viz-root", role: "tooltip"}, document.body); }
    tipEl.innerHTML = "";
    h("b", {}, tipEl, title);
    for (const r of rows || []) {
      const row = h("div", {class: "row"}, tipEl);
      if (r.color) { const k = h("span", {class: "key"}, row); k.style.background = r.color; }
      h("span", {}, row, r.text);
    }
    tipEl.style.display = "block";
    let x = evt.clientX, y = evt.clientY;
    if (x === undefined) { const b = evt.target.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top; }
    const tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    tipEl.style.left = Math.min(window.innerWidth - tw - 8, x + 14) + "px";
    tipEl.style.top = Math.max(8, y - th - 12) + "px";
  }
  function untip() { if (tipEl) tipEl.style.display = "none"; }
  function hover(node, title, rows) {
    node.setAttribute("tabindex", "0");
    node.addEventListener("pointermove", (e) => tip(e, title, rows));
    node.addEventListener("focus", (e) => tip(e, title, rows));
    node.addEventListener("pointerleave", untip);
    node.addEventListener("blur", untip);
  }

  // ------------------------------------------------------------------ card with table view
  function card(container, {title, sub, columns, rows}) {
    container.innerHTML = "";
    container.classList.add("viz-card");
    const head = h("div", {class: "viz-head"}, container);
    const t = h("div", {}, head);
    h("h3", {}, t, title);
    if (sub) h("div", {class: "viz-sub"}, t, sub);
    const body = h("div", {}, container);
    if (columns) {
      const btn = h("button", {class: "viz-toggle", type: "button", "aria-pressed": "false"}, head, "Bảng");
      const tableBox = h("div", {}, container);
      tableBox.hidden = true;
      btn.onclick = () => {
        const show = tableBox.hidden;
        tableBox.hidden = !show; body.hidden = show;
        btn.textContent = show ? "Biểu đồ" : "Bảng";
        btn.setAttribute("aria-pressed", String(show));
        if (show && !tableBox.firstChild) table(tableBox, columns, rows);
      };
    }
    return body;
  }
  function table(parent, columns, rows) {
    const tb = h("table", {class: "viz-table"}, parent);
    const tr = h("tr", {}, tb);
    for (const c of columns) h("th", {class: c.num ? "num" : ""}, tr, c.label);
    for (const r of rows) {
      const row = h("tr", {}, tb);
      for (const c of columns) h("td", {class: c.num ? "num" : ""}, row, c.fmt ? c.fmt(r[c.key], r) : r[c.key]);
    }
    return tb;
  }
  const width = (el, fallback) => Math.max(280, Math.floor(el.clientWidth || fallback || 600));

  // ------------------------------------------------------------------ stat tiles
  function tiles(container, items) {
    container.innerHTML = "";
    container.classList.add("viz-tiles");
    for (const it of items) {
      const tile = h("div", {class: "viz-tile" + (it.hero ? " hero" : "")}, container);
      h("div", {class: "label"}, tile, it.label);
      h("div", {class: "value"}, tile, it.value);
      if (it.delta !== undefined && it.delta !== null && isFinite(it.delta)) {
        const up = it.delta >= 0, good = it.upIsGood === false ? !up : up;
        h("div", {class: "delta " + (it.delta === 0 ? "" : good ? "good" : "bad")}, tile,
          `${up ? "▲" : "▼"} ${pct(Math.abs(it.delta), 1)} so với ${it.period || "kỳ trước"}`);
      }
      if (it.note) h("div", {class: "note"}, tile, it.note);
      if (it.spark && it.spark.length > 1) sparkline(tile, it.spark);
    }
  }
  function sparkline(parent, values) {
    const W = 140, H = 28, max = Math.max(...values, 1);
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, style: "margin-top:6px;max-width:160px", "aria-hidden": "true"}, parent);
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * W},${H - 2 - (v / max) * (H - 4)}`);
    svg("polyline", {points: pts.join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round"}, s);
  }

  // ------------------------------------------------------------------ line (single series, area wash, crosshair)
  function line(container, {title, sub, points, fmt, color, label}) {
    fmt = fmt || compact;
    color = color || "var(--series-1)";
    const body = card(container, {title, sub, columns: [{key: "x", label: "Ngày", fmt: day}, {key: "y", label: label || title, num: true, fmt}], rows: points});
    if (!points.length) { h("div", {class: "viz-empty"}, body, "Chưa có dữ liệu."); return; }
    const W = width(body), H = 190, m = {l: 44, r: 52, t: 10, b: 24};
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title}, body);
    const max = niceMax(Math.max(...points.map((p) => p.y)));
    const x = (i) => m.l + (i / Math.max(1, points.length - 1)) * (W - m.l - m.r);
    const y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
    for (let k = 0; k <= 4; k++) {
      const v = (max / 4) * k;
      svg("line", {class: k ? "grid" : "axis", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v)}, s);
      text(s, m.l - 6, y(v) + 4, fmt(v), {"text-anchor": "end"});
    }
    const step = Math.ceil(points.length / 7);
    points.forEach((p, i) => { if (i % step === 0 || i === points.length - 1) text(s, x(i), H - 6, day(p.x), {"text-anchor": "middle"}); });
    const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.y)}`).join("");
    svg("path", {d: `${path}L${x(points.length - 1)},${y(0)}L${x(0)},${y(0)}Z`, fill: color, "fill-opacity": 0.1}, s);
    svg("path", {d: path, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, s);
    const last = points[points.length - 1];
    svg("circle", {cx: x(points.length - 1), cy: y(last.y), r: 4, fill: color, stroke: "var(--ring)", "stroke-width": 2}, s);
    text(s, x(points.length - 1) + 8, y(last.y) + 4, fmt(last.y), {class: "ink"});
    const cross = svg("line", {class: "axis", y1: m.t, y2: H - m.b, visibility: "hidden"}, s);
    const dot = svg("circle", {r: 4, fill: color, stroke: "var(--ring)", "stroke-width": 2, visibility: "hidden"}, s);
    const overlay = svg("rect", {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, class: "hit"}, s);
    overlay.addEventListener("pointermove", (e) => {
      const r = s.getBoundingClientRect();
      const px = (e.clientX - r.left) * (W / r.width);
      const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - m.l) / (W - m.l - m.r)) * (points.length - 1))));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(points[i].y)); dot.setAttribute("visibility", "visible");
      tip(e, fmt(points[i].y), [{color, text: `${label || title} · ${day(points[i].x)}`}]);
    });
    overlay.addEventListener("pointerleave", () => { untip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  }

  // ------------------------------------------------------------------ horizontal bars (one series, or colour per entity)
  function bars(container, {title, sub, rows, fmt, max, colorOf, extra}) {
    fmt = fmt || compact;
    const columns = [{key: "label", label: "Mục"}, {key: "value", label: "Giá trị", num: true, fmt}];
    if (extra) columns.push({key: "extra", label: extra, num: true});
    const body = card(container, {title, sub, columns, rows});
    if (!rows.length) { h("div", {class: "viz-empty"}, body, "Chưa có dữ liệu."); return; }
    const W = width(body), rowH = 30, labelW = Math.min(190, W * 0.38), H = rows.length * rowH + 4;
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title}, body);
    const top = max || niceMax(Math.max(...rows.map((r) => r.value)));
    const x0 = labelW + 8, x1 = W - 70;
    svg("line", {class: "axis", x1: x0, x2: x0, y1: 0, y2: H}, s);
    rows.forEach((r, i) => {
      const y = i * rowH + 8, len = Math.max(0, (r.value / top) * (x1 - x0)), bh = 14;
      text(s, labelW, y + 11, r.label.length > 30 ? r.label.slice(0, 29) + "…" : r.label, {"text-anchor": "end", class: "ink2"});
      const color = colorOf ? colorOf(r) : "var(--series-1)";
      if (len > 0) svg("path", {d: barPath(x0, y, len, bh), fill: color, class: "mark"}, s);
      text(s, x0 + len + 6, y + 11, fmt(r.value), {class: "ink"});
      const hit = svg("rect", {x: 0, y: y - 6, width: W, height: rowH, class: "hit"}, s);
      hover(hit, fmt(r.value), [{color, text: r.label}].concat(r.sub ? [{text: r.sub}] : []));
    });
  }
  function barPath(x, y, len, bh) {  // 4px rounded data end, square at the baseline
    const r = Math.min(4, len, bh / 2);
    return `M${x},${y}H${x + len - r}Q${x + len},${y} ${x + len},${y + r}V${y + bh - r}Q${x + len},${y + bh} ${x + len - r},${y + bh}H${x}Z`;
  }

  // ------------------------------------------------------------------ funnel (ordinal ramp)
  function funnel(container, {title, sub, steps}) {
    const rows = steps.map((st, i) => Object.assign({}, st, {rate: i ? (steps[i - 1].people ? st.people / steps[i - 1].people : 0) : 1}));
    const body = card(container, {title, sub, columns: [{key: "label", label: "Bước"}, {key: "people", label: "Số khách", num: true},
      {key: "rate", label: "So với bước trước", num: true, fmt: (v) => pct(v, 1)}], rows});
    const W = width(body), rowH = 40, labelW = Math.min(170, W * 0.32), H = rows.length * rowH;
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title}, body);
    const top = Math.max(1, rows[0].people), x0 = labelW + 8, x1 = W - 120;
    rows.forEach((r, i) => {
      const y = i * rowH + 6, len = Math.max(2, (r.people / top) * (x1 - x0)), color = `var(--stage-${i + 1})`;
      text(s, labelW, y + 15, r.label, {"text-anchor": "end", class: "ink2"});
      svg("path", {d: barPath(x0, y + 2, len, 20), fill: color, class: "mark"}, s);
      text(s, x0 + len + 6, y + 16, compact(r.people), {class: "ink"});
      if (i) text(s, x0 + len + 6, y + 30, `${pct(r.rate, 1)} từ bước trước`);
      const hit = svg("rect", {x: 0, y, width: W, height: rowH, class: "hit"}, s);
      hover(hit, `${compact(r.people)} khách`, [{color, text: r.label}, {text: i ? `${pct(r.rate, 1)} so với bước trước` : "điểm bắt đầu"}]);
    });
  }

  // ------------------------------------------------------------------ stacked columns (categorical, legend + table)
  function stacked(container, {title, sub, categories, series, rows, catFmt, catLabel}) {
    catFmt = catFmt || ((c) => c);
    const columns = [{key: "_cat", label: catLabel || "Kỳ", fmt: catFmt}].concat(series.map((sr) => ({key: sr.key, label: sr.label, num: true})));
    const body = card(container, {title, sub, columns, rows: rows.map((r, i) => Object.assign({_cat: categories[i]}, r))});
    if (!rows.length) { h("div", {class: "viz-empty"}, body, "Chưa có dữ liệu."); return; }
    const W = width(body), H = 220, m = {l: 44, r: 8, t: 10, b: 24};
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title}, body);
    const totals = rows.map((r) => series.reduce((a, sr) => a + (r[sr.key] || 0), 0));
    const max = niceMax(Math.max(...totals, 1));
    const y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
    for (let k = 0; k <= 4; k++) {
      const v = (max / 4) * k;
      svg("line", {class: k ? "grid" : "axis", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v)}, s);
      text(s, m.l - 6, y(v) + 4, compact(v), {"text-anchor": "end"});
    }
    const band = (W - m.l - m.r) / rows.length, bw = Math.min(24, band * 0.6);
    rows.forEach((r, i) => {
      const cx = m.l + band * i + band / 2;
      let acc = 0;
      const visible = series.filter((sr) => (r[sr.key] || 0) > 0);
      visible.forEach((sr, j) => {
        const v = r[sr.key], y0 = y(acc), y1 = y(acc + v), isTop = j === visible.length - 1;
        const gap = j ? 2 : 0, hgt = Math.max(0, y0 - y1 - gap);
        if (hgt > 0) {
          const d = isTop ? colPath(cx - bw / 2, y1, bw, hgt) : `M${cx - bw / 2},${y1}h${bw}v${hgt}h${-bw}Z`;
          const p = svg("path", {d, fill: `var(--series-${sr.slot})`, class: "mark"}, s);
          hover(p, compact(v), [{color: `var(--series-${sr.slot})`, text: `${sr.label} · ${catFmt(categories[i])}`}, {text: `Tổng: ${compact(totals[i])}`}]);
        }
        acc += v;
      });
      text(s, cx, H - 6, catFmt(categories[i]), {"text-anchor": "middle"});
    });
    const lg = h("div", {class: "viz-legend"}, body);
    for (const sr of series) { const it = h("span", {}, lg); const sw = h("i", {}, it); sw.style.background = `var(--series-${sr.slot})`; h("span", {}, it, sr.label); }
  }
  function colPath(x, y, bw, hgt) {  // rounded top only
    const r = Math.min(4, bw / 2, hgt);
    return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${y + hgt}Z`;
  }

  // ------------------------------------------------------------------ customer journey (swimlanes by channel)
  function journey(container, data, opts) {
    opts = opts || {};
    container.innerHTML = "";
    const tps = data.touchpoints || [];
    const preds = (data.predictions || {}).next_touchpoints || [];
    const channels = data.meta.channels, stages = data.meta.stages;
    const now = new Date(data.now);
    const columns = [{key: "when", label: "Thời điểm"}, {key: "kind", label: "Loại"}, {key: "channel", label: "Kênh"},
      {key: "stage", label: "Giai đoạn"}, {key: "label", label: "Điểm chạm"}];
    const chName = Object.fromEntries(channels.map((c) => [c.key, c.label]));
    const stName = Object.fromEntries(stages.map((st) => [st.key, st.label]));
    const tableRows = tps.map((t) => ({when: dateTime(t.at), kind: t.direction === "out" ? "DMAG/CDP chủ động" : "Khách chủ động",
      channel: chName[t.channel], stage: stName[t.stage], label: t.label}))
      .concat(preds.map((p) => ({when: `~${nf.format(p.expected_in_days)} ngày tới`, kind: `Dự đoán ${pct(p.probability)}`,
        channel: chName[p.channel], stage: stName[p.stage], label: p.label})));
    const body = card(container, {title: opts.title || "Hành trình khách hàng",
      sub: `${tps.length} điểm chạm · giai đoạn đã tới: ${stName[data.stage_reached] || "–"}`, columns, rows: tableRows});

    // Stage progression strip.
    const reached = stages.findIndex((st) => st.key === data.stage_reached);
    const strip = h("div", {style: "display:grid;grid-template-columns:repeat(5,1fr);gap:2px;margin:4px 0 10px"}, body);
    stages.forEach((st, i) => {
      const cell = h("div", {}, strip);
      const bar = h("div", {style: "height:8px;border-radius:4px"}, cell);
      bar.style.background = i <= reached ? stageColor(st.key) : "var(--grid)";
      h("div", {style: `font-size:11px;margin-top:3px;color:var(${i <= reached ? "--text-primary" : "--text-muted"})`}, cell, st.label);
    });
    if (!tps.length) { h("div", {class: "viz-empty"}, body, "Chưa có điểm chạm."); return; }

    const used = channels.filter((c) => tps.some((t) => t.channel === c.key) || preds.some((p) => p.channel === c.key));
    const W = width(body, 900), labelW = Math.min(170, W * 0.25), laneH = 36, top = 18, axisH = 24;
    const H = top + used.length * laneH + axisH;
    const s = svg("svg", {class: "viz-svg", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Hành trình theo kênh"}, body);
    const first = new Date(tps[0].at);
    const horizon = Math.min(30, Math.max(7, ...preds.map((p) => p.expected_in_days + 2)));
    const end = new Date(now.getTime() + horizon * 864e5);
    const x0 = labelW + 12, x1 = W - 30;
    const span = Math.max(864e5, end - first);
    const x = (d) => x0 + ((new Date(d) - first) / span) * (x1 - x0);
    const nowX = x(now);
    // Future zone + today line.
    svg("rect", {x: nowX, y: top - 14, width: x1 - nowX + 20, height: used.length * laneH + 14, fill: "var(--future-wash)"}, s);
    svg("line", {class: "axis", x1: nowX, x2: nowX, y1: top - 14, y2: top + used.length * laneH}, s);
    text(s, nowX + 4, top - 4, "Hôm nay · dự đoán →", {class: "ink2"});
    // Lanes.
    used.forEach((c, i) => {
      const yc = top + i * laneH + laneH / 2;
      svg("line", {class: "grid", x1: x0, x2: x1, y1: yc, y2: yc}, s);
      svg("rect", {x: 0, y: yc - 5, width: 10, height: 10, rx: 2, fill: channelColor(c.key)}, s);
      text(s, 16, yc + 4, c.label, {class: "ink2"});
    });
    // Time axis.
    const ticks = 6;
    for (let k = 0; k <= ticks; k++) {
      const d = new Date(first.getTime() + (span * k) / ticks);
      text(s, x(d), H - 6, day(d.toISOString()), {"text-anchor": "middle"});
    }
    const laneY = Object.fromEntries(used.map((c, i) => [c.key, top + i * laneH + laneH / 2]));
    // Past touchpoints (light jitter for markers closer than 7px in the same lane).
    const lastX = {};
    tps.forEach((t, idx) => {
      const px = x(t.at);
      let py = laneY[t.channel];
      const k = t.channel;
      if (lastX[k] !== undefined && Math.abs(px - lastX[k].x) < 7) { lastX[k].n = (lastX[k].n + 1) % 3; py += [0, -7, 7][lastX[k].n]; }
      else lastX[k] = {x: px, n: 0};
      const color = stageColor(t.stage);
      if (t.direction === "out") {
        svg("rect", {x: px - 5, y: py - 5, width: 10, height: 10, transform: `rotate(45 ${px} ${py})`, fill: color, stroke: "var(--ring)", "stroke-width": 2, class: "mark"}, s);
      } else {
        svg("circle", {cx: px, cy: py, r: 5, fill: color, stroke: "var(--ring)", "stroke-width": 2, class: "mark"}, s);
      }
      const hit = svg("circle", {cx: px, cy: py, r: 12, class: "hit"}, s);
      hover(hit, t.label, [{color, text: `${stName[t.stage]} · ${chName[t.channel]}`},
        {text: `${dateTime(t.at)} · ${t.direction === "out" ? "DMAG/CDP chủ động" : "khách chủ động"}`}]);
    });
    // Predicted next touchpoints (hollow markers in the future zone).
    preds.forEach((p) => {
      const px = Math.min(x1, x(new Date(now.getTime() + p.expected_in_days * 864e5)) + 6);
      const py = laneY[p.channel];
      const color = stageColor(p.stage);
      svg("circle", {cx: px, cy: py, r: 6, fill: "var(--surface-1)", stroke: color, "stroke-width": 2}, s);
      text(s, px, py - 10, pct(p.probability), {"text-anchor": "middle", class: "ink"});
      const hit = svg("circle", {cx: px, cy: py, r: 14, class: "hit"}, s);
      hover(hit, `${p.label} – ${pct(p.probability)}`, [{color, text: `Dự đoán · ${chName[p.channel]}`},
        {text: `khoảng ${nf.format(p.expected_in_days)} ngày tới`}]);
    });
    const lg = h("div", {class: "viz-legend"}, body);
    stages.forEach((st) => { const it = h("span", {}, lg); const sw = h("i", {class: "dot"}, it); sw.style.background = stageColor(st.key); h("span", {}, it, st.label); });
    const a = h("span", {}, lg); h("i", {class: "dot"}, a).style.background = "var(--text-secondary)"; h("span", {}, a, "khách chủ động");
    const b = h("span", {}, lg); h("i", {class: "diamond"}, b).style.background = "var(--text-secondary)"; h("span", {}, b, "DMAG/CDP chủ động");
    const c = h("span", {}, lg); h("i", {class: "hollow"}, c); h("span", {}, c, "dự đoán");
  }

  // ------------------------------------------------------------------ predictions panel
  function predictions(container, data) {
    container.innerHTML = "";
    const pr = data.predictions || {};
    const products = pr.products || [], nexts = pr.next_touchpoints || [];
    const chName = Object.fromEntries(data.meta.channels.map((c) => [c.key, c.label]));
    const body = card(container, {title: "Dự đoán", sub: "Mô hình CDP – cập nhật mỗi ngày",
      columns: [{key: "kind", label: "Loại"}, {key: "what", label: "Nội dung"}, {key: "p", label: "Xác suất", num: true}, {key: "more", label: "Chi tiết"}],
      rows: products.map((p) => ({kind: "Sẽ mua", what: p.item, p: pct(p.probability), more: (p.reasons || []).join("; ")}))
        .concat(nexts.map((n) => ({kind: "Điểm chạm tiếp theo", what: n.label, p: pct(n.probability), more: `~${nf.format(n.expected_in_days)} ngày · ${chName[n.channel]}`})))});
    if (pr.purchase_probability_30d != null) {
      const t = h("div", {class: "viz-tile", style: "margin-bottom:12px"}, body);
      h("div", {class: "label"}, t, "Khả năng mua ít nhất 1 sản phẩm/dịch vụ trong 30 ngày");
      h("div", {class: "value"}, t, pct(pr.purchase_probability_30d));
      const m = h("div", {class: "viz-meter-wrap"}, t); h("div", {class: "viz-meter"}, m);
      const f = h("div", {class: "fill"}, m); f.style.width = pct(pr.purchase_probability_30d).replace(",", ".");
    }
    h("div", {class: "viz-sub", style: "margin-top:4px;font-weight:600;color:var(--text-primary)"}, body, "Sản phẩm/dịch vụ có khả năng mua");
    if (!products.length) h("div", {class: "viz-empty"}, body, "Chưa đủ dữ liệu để dự đoán.");
    for (const p of products) {
      const row = h("div", {style: "margin:8px 0"}, body);
      const top = h("div", {style: "display:flex;justify-content:space-between;gap:8px;font-size:13px"}, row);
      h("span", {}, top, p.item);
      h("b", {}, top, pct(p.probability));
      const m = h("div", {class: "viz-meter-wrap"}, row); h("div", {class: "viz-meter"}, m);
      const f = h("div", {class: "fill"}, m); f.style.width = (p.probability * 100).toFixed(1) + "%";
      const why = (p.reasons || []).join(" · ");
      h("div", {class: "viz-sub", style: "margin:4px 0 0"}, row, (why ? "Vì: " + why : "") + (p.expected_revenue ? ` · giá trị kỳ vọng ${money(p.expected_revenue)}` : ""));
    }
    h("div", {class: "viz-sub", style: "margin-top:12px;font-weight:600;color:var(--text-primary)"}, body, "Điểm chạm tiếp theo");
    if (!nexts.length) h("div", {class: "viz-empty"}, body, "Chưa đủ dữ liệu.");
    for (const n of nexts) {
      const row = h("div", {style: "display:flex;align-items:center;gap:8px;font-size:13px;margin:6px 0"}, body);
      const sw = h("i", {style: "width:10px;height:10px;border-radius:2px;display:inline-block;flex:none"}, row); sw.style.background = channelColor(n.channel);
      h("span", {style: "flex:1"}, row, `${n.label} (${chName[n.channel]})`);
      h("span", {class: "viz-sub", style: "margin:0"}, row, `~${nf.format(n.expected_in_days)} ngày`);
      h("b", {}, row, pct(n.probability));
    }
  }

  w.CDPViz = {tiles, line, bars, funnel, stacked, journey, predictions, compact, money, pct, day, channelColor, CHANNEL_SLOT};
})(window);
