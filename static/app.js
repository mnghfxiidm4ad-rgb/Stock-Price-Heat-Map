const state = {
  market: "日本株",
  metric: "全体",
  size: "売買代金",
  sector: "全業種",
  query: "",
  asof: "",
  latestDay: "",
  historyReady: false,
  days: [],
  rows: [],
  hit: [],
  useApi: false,
  sort: { key: "change_pct", dir: -1 },
  calYear: 2026,
  calMonth: 8,
};

const $ = (id) => document.getElementById(id);
const map = $("map");
const tip = $("tip");
const ctx = map.getContext("2d");

function setBusy(on, text) {
  const el = $("busy");
  el.classList.toggle("hide", !on);
  if (text) el.textContent = text;
}

function setChips(ids, onId) {
  ids.forEach((id) => $(id).classList.toggle("on", id === onId));
}

function formatPrice(p) {
  const n = Number(p);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1000) return n.toLocaleString("ja-JP", { maximumFractionDigits: 0 });
  return n.toLocaleString("ja-JP", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMoney(value, market) {
  const n = Number(value) || 0;
  if (n <= 0) return "-";
  if (market === "日本株") {
    if (n >= 1e12) return (n / 1e12).toFixed(2) + "兆円";
    if (n >= 1e8) return (n / 1e8).toFixed(1) + "億円";
    if (n >= 1e4) return (n / 1e4).toFixed(0) + "万円";
    return n.toLocaleString("ja-JP") + "円";
  }
  if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + n.toLocaleString("en-US");
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function colorChange(pct) {
  const t = Math.max(-8, Math.min(8, pct)) / 8;
  let r, g, b;
  if (t >= 0) {
    r = lerp(28, 176, t);
    g = lerp(14, 22, t);
    b = lerp(16, 28, t);
  } else {
    r = lerp(28, 16, -t);
    g = lerp(14, 118, -t);
    b = lerp(16, 48, -t);
  }
  return `rgb(${r | 0},${g | 0},${b | 0})`;
}

function tileSize(row, sizeMetric) {
  if (sizeMetric === "時価総額") return Math.max(Number(row.market_cap) || 0, 1);
  if (sizeMetric === "売買代金") return Math.max(Number(row.turnover) || 0, 1);
  return Math.max(Number(row.volume) || 0, 1);
}

function isLatestDay() {
  return Boolean(state.asof) && state.asof === state.latestDay;
}

function effectiveSize() {
  if (state.size === "時価総額" && !isLatestDay()) return "売買代金";
  if (state.size === "時価総額" && !state.rows.some((r) => Number(r.market_cap) > 0)) return "売買代金";
  return state.size;
}

function filteredRows() {
  let rows = state.rows;
  if (state.sector !== "全業種") {
    rows = rows.filter((r) => (r.sector || "その他") === state.sector);
  }
  if (state.metric === "暴騰率") rows = rows.filter((r) => r.change_pct > 0);
  if (state.metric === "暴落率") rows = rows.filter((r) => r.change_pct < 0);
  const q = state.query.trim().toLowerCase();
  if (q) {
    rows = rows.filter(
      (r) =>
        String(r.name || "").toLowerCase().includes(q) ||
        String(r.ticker || "").toLowerCase().includes(q)
    );
  }
  return rows;
}

function splitLayout(items, x, y, w, h, out) {
  const stack = [[items, x, y, w, h]];
  while (stack.length) {
    const cur = stack.pop();
    items = cur[0];
    x = cur[1];
    y = cur[2];
    w = cur[3];
    h = cur[4];
    if (!items.length || w < 2 || h < 2) continue;
    if (items.length === 1) {
      out.push({ item: items[0], x, y, w, h });
      continue;
    }
    const total = items.reduce((s, it) => s + it.value, 0) || 1;
    let acc = 0;
    let i = 0;
    while (i < items.length && acc < total / 2) acc += items[i++].value;
    if (i <= 0) i = 1;
    if (i >= items.length) i = items.length - 1;
    const a = items.slice(0, i);
    const b = items.slice(i);
    const fa = acc / total;
    if (w >= h) {
      stack.push([b, x + w * fa, y, w * (1 - fa), h]);
      stack.push([a, x, y, w * fa, h]);
    } else {
      stack.push([b, x, y + h * fa, w, h * (1 - fa)]);
      stack.push([a, x, y, w, h * fa]);
    }
  }
}

function resizeCanvas() {
  const wrap = $("map-wrap");
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(100, wrap.clientWidth);
  const h = Math.max(100, wrap.clientHeight);
  map.width = Math.floor(w * dpr);
  map.height = Math.floor(h * dpr);
  map.style.width = w + "px";
  map.style.height = h + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w, h };
}

function drawEmpty(text) {
  const { w, h } = resizeCanvas();
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#8092a6";
  ctx.font = "14px Yu Gothic UI, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, w / 2, h / 2);
  state.hit = [];
}

function draw() {
  const rows = filteredRows();
  if (!state.rows.length) {
    drawEmpty(
      state.market === "日本株"
        ? "日本株の日足がありません。C:\\data\\日本株\\start.bat で取得してください"
        : "米株の日足がありません。C:\\data\\日本株\\start_us.bat で取得してください"
    );
    return;
  }
  if (!rows.length) {
    const empty =
      state.sector !== "全業種" && state.metric === "全体"
        ? state.sector + " の銘柄がありません"
        : state.metric === "暴騰率"
          ? "プラスの銘柄がありません"
          : state.metric === "暴落率"
            ? "マイナスの銘柄がありません"
            : "表示できる銘柄がありません";
    drawEmpty(empty);
    updateMetrics([]);
    renderTable([]);
    return;
  }

  updateMetrics(rows);
  renderTable(rows);

  const { w, h } = resizeCanvas();
  ctx.clearRect(0, 0, w, h);
  const sizeMetric = effectiveSize();
  const bySector = {};
  for (const r of rows) {
    const key = r.sector || "その他";
    if (!bySector[key]) bySector[key] = [];
    bySector[key].push({ row: r, value: tileSize(r, sizeMetric) });
  }
  const sectors = Object.keys(bySector).map((name) => ({
    name,
    value: bySector[name].reduce((s, x) => s + x.value, 0),
    children: bySector[name].sort((a, b) => b.value - a.value),
  }));
  sectors.sort((a, b) => b.value - a.value);

  const boxes = [];
  splitLayout(sectors, 0, 0, w, h, boxes);
  state.hit = [];
  const grouped = state.sector === "全業種" && sectors.length > 1;
  const gap = grouped ? 3 : 0;

  for (const box of boxes) {
    let sx = box.x + gap;
    let sy = box.y + gap;
    let sw = Math.max(2, box.w - gap * 2);
    let sh = Math.max(2, box.h - gap * 2);
    const head = grouped && sh >= 40 && sw >= 70 ? 18 : 0;
    if (head) {
      ctx.fillStyle = "#121820";
      ctx.fillRect(sx, sy, sw, head);
      ctx.fillStyle = "#e8eef4";
      ctx.font = "bold 11px Yu Gothic UI, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(box.item.name, sx + 8, sy + head / 2);
    }
    const inner = [];
    splitLayout(box.item.children, sx, sy + head, sw, Math.max(2, sh - head), inner);
    for (const cell of inner) {
      paintStock(cell.item.row, cell.x, cell.y, cell.w, cell.h);
    }
  }
}

function paintStock(r, x, y, cw, ch) {
  ctx.fillStyle = colorChange(r.change_pct);
  ctx.fillRect(x, y, cw, ch);
  ctx.strokeStyle = "#07090d";
  ctx.lineWidth = 1;
  ctx.strokeRect(x, y, cw, ch);
  if (cw >= 48 && ch >= 36) {
    const size = Math.max(8, Math.min(14, Math.min(cw / 8, ch / 4.4)));
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `bold ${size}px Yu Gothic UI, sans-serif`;
    ctx.fillText(r.name, x + cw / 2, y + ch / 2 - 12, cw - 6);
    ctx.font = `${Math.max(8, size)}px Yu Gothic UI, sans-serif`;
    ctx.fillText(formatPrice(r.price), x + cw / 2, y + ch / 2 + 4, cw - 6);
    ctx.font = `${Math.max(8, size - 1)}px Yu Gothic UI, sans-serif`;
    ctx.fillText(
      (r.change_pct >= 0 ? "+" : "") + r.change_pct.toFixed(2) + "%",
      x + cw / 2,
      y + ch / 2 + 18,
      cw - 6
    );
  } else if (cw >= 48 && ch >= 28) {
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const size = Math.max(8, Math.min(12, Math.min(cw / 8, ch / 3.2)));
    ctx.font = `bold ${size}px Yu Gothic UI, sans-serif`;
    ctx.fillText(r.name, x + cw / 2, y + ch / 2 - 7, cw - 4);
    ctx.font = `${size}px Yu Gothic UI, sans-serif`;
    ctx.fillText(formatPrice(r.price), x + cw / 2, y + ch / 2 + 8, cw - 4);
  }
  state.hit.push({ x0: x, y0: y, x1: x + cw, y1: y + ch, row: r });
}

function updateMetrics(rows) {
  if (!rows.length) {
    $("k-n").textContent = "0";
    $("k-ud").textContent = "-";
    $("k-avg").textContent = "-";
    $("k-date").textContent = state.asof || "-";
    return;
  }
  const up = rows.filter((r) => r.change_pct > 0).length;
  const down = rows.filter((r) => r.change_pct < 0).length;
  const avg = rows.reduce((s, r) => s + r.change_pct, 0) / rows.length;
  $("k-n").textContent = String(rows.length);
  $("k-ud").textContent = up + " / " + down;
  $("k-avg").textContent = (avg >= 0 ? "+" : "") + avg.toFixed(2) + "%";
  $("k-date").textContent = rows[0].asof || state.asof || "-";
}

function renderTable(rows) {
  const body = $("tbody");
  const { key, dir } = state.sort;
  const sorted = rows.slice().sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv), "ja") * dir;
  });
  const max = 500;
  const shown = sorted.slice(0, max);
  $("table-count").textContent =
    rows.length > max ? `${max} / ${rows.length} 件` : `${rows.length} 件`;
  body.innerHTML = shown
    .map((r) => {
      const cls = r.change_pct > 0 ? "up" : r.change_pct < 0 ? "down" : "";
      return `<tr data-ticker="${r.ticker}">
        <td>${escapeHtml(r.name)}</td>
        <td>${escapeHtml(r.ticker)}</td>
        <td>${escapeHtml(r.sector || "その他")}</td>
        <td class="num">${formatPrice(r.price)}</td>
        <td class="num ${cls}">${(r.change_pct >= 0 ? "+" : "") + r.change_pct.toFixed(2)}</td>
        <td class="num">${Number(r.volume).toLocaleString("ja-JP")}</td>
        <td class="num">${Number(r.vol_ratio).toFixed(2)}</td>
      </tr>`;
    })
    .join("");
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function hitAt(mx, my) {
  for (let i = state.hit.length - 1; i >= 0; i--) {
    const h = state.hit[i];
    if (mx >= h.x0 && mx <= h.x1 && my >= h.y0 && my <= h.y1) return h.row;
  }
  return null;
}

function showTip(ev, r) {
  const rect = map.getBoundingClientRect();
  const cap =
    isLatestDay() && Number(r.market_cap) > 0
      ? `時価総額 ${formatMoney(r.market_cap, state.market)}<br>`
      : "";
  tip.hidden = false;
  tip.innerHTML =
    `<b>${escapeHtml(r.name)}</b> ${escapeHtml(r.ticker)}<br>` +
    `${escapeHtml(r.sector || "その他")}<br>` +
    `終値 ${formatPrice(r.price)}　前日比 ${(r.change_pct >= 0 ? "+" : "") + r.change_pct.toFixed(2)}%<br>` +
    cap +
    `売買代金 ${formatMoney(r.turnover, state.market)}<br>` +
    `出来高 ${Number(r.volume).toLocaleString("ja-JP")}　倍率 ${Number(r.vol_ratio).toFixed(2)}倍`;
  let x = ev.clientX - rect.left + 12;
  let y = ev.clientY - rect.top + 12;
  if (x + 220 > rect.width) x -= 230;
  if (y + 110 > rect.height) y -= 110;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function yahooUrl(ticker) {
  if (state.market === "日本株") return "https://finance.yahoo.co.jp/quote/" + encodeURIComponent(ticker);
  return "https://finance.yahoo.com/quote/" + encodeURIComponent(ticker);
}

function syncControls() {
  const latest = isLatestDay();
  $("s-cap").disabled = !latest;
  if (!latest && state.size === "時価総額") {
    state.size = "売買代金";
    setChips(["s-cap", "s-turn", "s-vol"], "s-turn");
    $("size-hint").textContent = "過去日のため売買代金で表示（時価総額は最新日のみ）";
  } else {
    $("size-hint").textContent = "時価総額は最新日のみ";
  }
  const ready = state.historyReady && state.days.length > 0;
  $("d-prev").disabled = !ready;
  $("d-next").disabled = !ready;
  $("d-cal").disabled = !ready;
  $("d-latest").disabled = !ready;
  $("date-hint").textContent = ready
    ? "カレンダーか ◀▶ で日付を変更できます"
    : state.useApi
      ? "最新日を表示中。履歴の準備ができれば日付を変えられます"
      : "公開サイトは最新の終値です。東証17時以降・米株16:00 ET引け後に更新";
}

function fillSectors(sectors) {
  const sel = $("sector");
  const values = ["全業種", ...(sectors || [])];
  if (!values.includes(state.sector)) state.sector = "全業種";
  sel.innerHTML = values.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  sel.value = state.sector;
}

async function tryApiQuotes(asof) {
  const host = location.hostname;
  if (host !== "localhost" && host !== "127.0.0.1") return null;
  try {
    const params = new URLSearchParams({ market: state.market });
    if (asof) params.set("asof", asof);
    const res = await fetch("/api/quotes?" + params.toString(), {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ctype = res.headers.get("content-type") || "";
    if (!ctype.includes("json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function loadQuotes(asof) {
  setBusy(true, "読み込み中…");
  $("status").textContent = "データを読み込んでいます…";
  try {
    let data = await tryApiQuotes(asof);
    state.useApi = Boolean(data && Array.isArray(data.quotes));
    if (!state.useApi) {
      const file = state.market === "日本株" ? "data/jp.json" : "data/us.json";
      const res = await fetch(file + "?t=" + Date.now());
      if (!res.ok) throw new Error("data file " + res.status);
      data = await res.json();
    }
    state.rows = data.quotes || [];
    state.asof = data.asof || "";
    state.historyReady = Boolean(data.history_ready);
    state.latestDay = (data.status && data.status.max_day) || state.asof;
    $("date").value = state.asof;
    fillSectors(data.sectors || []);
    syncControls();
    setBusy(false);
    if (!data.ok) {
      $("status").textContent = data.message || "データがありません";
      draw();
      return;
    }
    const extra = state.historyReady
      ? ""
      : state.useApi
        ? "　過去日付を準備中…"
        : data.updated_at
          ? "　更新 " + String(data.updated_at).replace("T", " ").slice(0, 16)
          : "　最新の終値";
    $("status").textContent = `${state.market}  ${state.rows.length.toLocaleString("ja-JP")}銘柄　${state.asof}${extra}`;
    draw();
    if (state.useApi && !state.historyReady) pollMeta();
    else if (state.useApi && !state.days.length) loadMeta();
  } catch (err) {
    setBusy(false);
    $("status").textContent = "読み込みに失敗しました: " + err;
  }
}

async function loadMeta() {
  try {
    const res = await fetch("/api/meta?market=" + encodeURIComponent(state.market));
    const data = await res.json();
    state.days = data.days || [];
    state.historyReady = Boolean(data.history_ready);
    state.latestDay = data.max_day || state.latestDay;
    syncControls();
    if (data.history_loading) {
      const p = data.history_progress || {};
      $("status").textContent =
        `${state.market}  ${state.rows.length.toLocaleString("ja-JP")}銘柄　${state.asof}　過去日付を準備中… ${(p.done || 0).toLocaleString("ja-JP")} / ${(p.total || 0).toLocaleString("ja-JP")}`;
    }
    return data;
  } catch {
    return null;
  }
}

let pollTimer = 0;
function pollMeta() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    const data = await loadMeta();
    if (data && data.history_ready) {
      $("status").textContent = `${state.market}  ${state.rows.length.toLocaleString("ja-JP")}銘柄　${state.asof}`;
      return;
    }
    if (data && data.history_loading) pollMeta();
  }, 1200);
}

function shiftDate(step) {
  if (!state.days.length || !state.asof) return;
  const i = state.days.indexOf(state.asof);
  const idx = i < 0 ? state.days.length - 1 : i;
  const next = state.days[Math.max(0, Math.min(state.days.length - 1, idx + step))];
  if (next && next !== state.asof) loadQuotes(next);
}

function snapDate(text) {
  const days = state.days;
  if (!days.length) return text;
  if (days.includes(text)) return text;
  let lo = -1;
  for (let i = 0; i < days.length; i++) {
    if (days[i] <= text) lo = i;
    else break;
  }
  if (lo < 0) return days[0];
  return days[lo];
}

function renderCalendar() {
  $("cal-title").textContent = `${state.calYear}年 ${state.calMonth}月`;
  const first = new Date(state.calYear, state.calMonth - 1, 1);
  const start = (first.getDay() + 6) % 7;
  const last = new Date(state.calYear, state.calMonth, 0).getDate();
  const daySet = new Set(state.days);
  const grid = $("cal-grid");
  const cells = [];
  for (let i = 0; i < start; i++) cells.push("<span></span>");
  for (let d = 1; d <= last; d++) {
    const key = `${state.calYear}-${String(state.calMonth).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const on = daySet.has(key) ? "on" : "";
    const cur = key === state.asof ? "cur" : "";
    cells.push(`<button type="button" data-day="${key}" class="${on} ${cur}">${d}</button>`);
  }
  grid.innerHTML = cells.join("");
}

function openCalendar() {
  if (!state.days.length) return;
  const parts = (state.asof || state.days[state.days.length - 1]).split("-").map(Number);
  state.calYear = parts[0];
  state.calMonth = parts[1];
  renderCalendar();
  $("cal").hidden = false;
}

function closeCalendar() {
  $("cal").hidden = true;
}

$("m-jp").onclick = () => {
  state.market = "日本株";
  state.sector = "全業種";
  state.days = [];
  setChips(["m-jp", "m-us"], "m-jp");
  loadQuotes();
};
$("m-us").onclick = () => {
  state.market = "米株";
  state.sector = "全業種";
  state.days = [];
  setChips(["m-jp", "m-us"], "m-us");
  loadQuotes();
};
$("v-all").onclick = () => {
  state.metric = "全体";
  setChips(["v-all", "v-up", "v-down"], "v-all");
  draw();
};
$("v-up").onclick = () => {
  state.metric = "暴騰率";
  setChips(["v-all", "v-up", "v-down"], "v-up");
  draw();
};
$("v-down").onclick = () => {
  state.metric = "暴落率";
  setChips(["v-all", "v-up", "v-down"], "v-down");
  draw();
};
$("s-cap").onclick = () => {
  if (!isLatestDay()) return;
  state.size = "時価総額";
  setChips(["s-cap", "s-turn", "s-vol"], "s-cap");
  draw();
};
$("s-turn").onclick = () => {
  state.size = "売買代金";
  setChips(["s-cap", "s-turn", "s-vol"], "s-turn");
  draw();
};
$("s-vol").onclick = () => {
  state.size = "出来高";
  setChips(["s-cap", "s-turn", "s-vol"], "s-vol");
  draw();
};
$("sector").onchange = (e) => {
  state.sector = e.target.value;
  draw();
};
$("q").oninput = (e) => {
  state.query = e.target.value;
  draw();
};
$("show-table").onchange = (e) => {
  $("table-panel").hidden = !e.target.checked;
  requestAnimationFrame(draw);
};
$("d-prev").onclick = () => shiftDate(-1);
$("d-next").onclick = () => shiftDate(1);
$("d-latest").onclick = () => {
  if (state.latestDay) loadQuotes(state.latestDay);
};
$("d-cal").onclick = () => {
  if ($("cal").hidden) openCalendar();
  else closeCalendar();
};
$("cal-prev").onclick = () => {
  state.calMonth -= 1;
  if (state.calMonth < 1) {
    state.calMonth = 12;
    state.calYear -= 1;
  }
  renderCalendar();
};
$("cal-next").onclick = () => {
  state.calMonth += 1;
  if (state.calMonth > 12) {
    state.calMonth = 1;
    state.calYear += 1;
  }
  renderCalendar();
};
$("cal-grid").onclick = (e) => {
  const btn = e.target.closest("button[data-day]");
  if (!btn || !btn.classList.contains("on")) return;
  closeCalendar();
  loadQuotes(btn.dataset.day);
};
$("date").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const snapped = snapDate($("date").value.trim());
    loadQuotes(snapped);
  }
});
$("reload").onclick = async () => {
  setBusy(true, "再読み込み中…");
  if (state.useApi) {
    try {
      await fetch("/api/reload?market=" + encodeURIComponent(state.market), { method: "POST" });
    } catch {
      /* static host */
    }
    state.days = [];
    state.historyReady = false;
  }
  await loadQuotes();
};
document.addEventListener("click", (e) => {
  if (!$("cal").hidden && !e.target.closest("#cal") && !e.target.closest("#d-cal")) closeCalendar();
});
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea")) return;
  if (e.key === "ArrowLeft") shiftDate(-1);
  if (e.key === "ArrowRight") shiftDate(1);
});

map.addEventListener("mousemove", (e) => {
  const rect = map.getBoundingClientRect();
  const r = hitAt(e.clientX - rect.left, e.clientY - rect.top);
  if (r) showTip(e, r);
  else tip.hidden = true;
});
map.addEventListener("mouseleave", () => {
  tip.hidden = true;
});
map.addEventListener("click", (e) => {
  const rect = map.getBoundingClientRect();
  const r = hitAt(e.clientX - rect.left, e.clientY - rect.top);
  if (r) window.open(yahooUrl(r.ticker), "_blank", "noopener");
});

$("tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ticker]");
  if (tr) window.open(yahooUrl(tr.dataset.ticker), "_blank", "noopener");
});

document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.onclick = () => {
    const key = th.dataset.sort;
    if (state.sort.key === key) state.sort.dir *= -1;
    else {
      state.sort.key = key;
      state.sort.dir = key === "name" || key === "ticker" || key === "sector" ? 1 : -1;
    }
    renderTable(filteredRows());
  };
});

window.addEventListener("resize", () => draw());
loadQuotes();
