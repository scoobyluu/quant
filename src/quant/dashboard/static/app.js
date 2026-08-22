// Quant Dashboard — vanilla JS single-page app.
// Routes (hash-based):
//   #/                 -> Watchlist
//   #/stock/:symbol    -> Stock detail
//   #/holdings         -> Holdings

const app = document.getElementById("app");

// ---------- helpers ----------

const api = {
  quote: (s) => j(`/api/quote/${encodeURIComponent(s)}`),
  history: (s, period = "6mo", interval = "1d") =>
    j(`/api/history/${encodeURIComponent(s)}?period=${period}&interval=${interval}`),
  info: (s) => j(`/api/info/${encodeURIComponent(s)}`),
  analyst: (s) => j(`/api/analyst/${encodeURIComponent(s)}`),
  earnings: (s) => j(`/api/earnings/${encodeURIComponent(s)}`),
  options: (s, exp) =>
    j(`/api/options/${encodeURIComponent(s)}${exp ? `?expiration=${encodeURIComponent(exp)}` : ""}`),
  news: (s) => j(`/api/news/${encodeURIComponent(s)}`),
  newsFeed: (symbols) =>
    j(`/api/news${symbols && symbols.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : ""}`),
  search: (q) => j(`/api/search?q=${encodeURIComponent(q)}`),
  watchlistGet: () => j(`/api/watchlist`),
  watchlistQuotes: () => j(`/api/watchlist/quotes`),
  watchlistAdd: (symbol) => j(`/api/watchlist`, "POST", { symbol }),
  watchlistRemove: (symbol) => j(`/api/watchlist/${encodeURIComponent(symbol)}`, "DELETE"),
  holdings: () => j(`/api/holdings`),
  holdingAdd: (symbol, shares, costBasis) =>
    j(`/api/holdings`, "POST", { symbol, shares, costBasis }),
  holdingRemove: (id) => j(`/api/holdings/${encodeURIComponent(id)}`, "DELETE"),
  quotes: (symbols) => j(`/api/quotes?symbols=${encodeURIComponent(symbols.join(","))}`),
  portfolioAnalytics: (period = "1y") =>
    j(`/api/portfolio/analytics?period=${encodeURIComponent(period)}`),
  watchlistAnalytics: () => j(`/api/watchlist/analytics`),
};

async function j(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function fmtMoney(n, currency = "USD") {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `$${n.toFixed(2)}`;
  }
}
function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const s = n >= 0 ? "+" : "";
  return `${s}${n.toFixed(2)}%`;
}
function fmtNum(n, digits = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function sma(points, window) {
  if (!points || points.length < window) return [];
  const out = [];
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    sum += points[i].y;
    if (i >= window) sum -= points[i - window].y;
    if (i >= window - 1) out.push({ x: points[i].x, y: sum / window });
  }
  return out;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// ---------- watchlist ----------

let watchlistTimer = null;

async function renderWatchlist() {
  clearInterval(watchlistTimer);
  const root = h("div", { class: "stack" });
  app.replaceChildren(root);

  const header = h(
    "div",
    { class: "row between" },
    h(
      "div",
      {},
      h("h1", {}, "Watchlist"),
      h("div", { class: "muted small" }, "Live quotes refresh every 30s.")
    ),
    mountSearch((sym) => addWatch(sym))
  );
  const panel = h("div", { class: "panel", style: { padding: 0, overflow: "hidden" } });
  const newsSlot = h("div");
  root.append(header, panel, newsSlot);

  async function addWatch(sym) {
    await api.watchlistAdd(sym);
    draw();
    loadNews();
  }
  async function removeWatch(sym) {
    await api.watchlistRemove(sym);
    draw();
    loadNews();
  }

  async function loadNews() {
    newsSlot.replaceChildren(
      h(
        "div",
        { class: "panel" },
        h("h2", {}, "Recent News"),
        h("div", { class: "muted" }, "Loading…")
      )
    );
    try {
      const { items, symbols } = await api.newsFeed();
      newsSlot.replaceChildren(renderFeedNews(items || [], symbols || []));
    } catch (e) {
      newsSlot.replaceChildren(
        h(
          "div",
          { class: "panel" },
          h("h2", {}, "Recent News"),
          h("div", { class: "error" }, `News unavailable: ${e.message}`)
        )
      );
    }
  }

  async function draw() {
    let payload;
    try {
      payload = await api.watchlistQuotes();
    } catch {
      payload = { symbols: [], quotes: [] };
    }
    const symbols = payload.symbols || [];
    const quotes = payload.quotes || [];
    const tbl = h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Symbol"),
          h("th", {}, "Name"),
          h("th", {}, "Price"),
          h("th", {}, "Change"),
          h("th", {}, "Change %"),
          h("th", {}, "Volume"),
          h("th", {}, "1M"),
          h("th", {}, "YTD"),
          h("th", {}, "Sharpe"),
          h("th", {})
        )
      ),
      h("tbody", {})
    );
    const body = tbl.querySelector("tbody");
    if (!symbols.length) {
      body.append(
        h(
          "tr",
          {},
          h(
            "td",
            { colspan: 10, class: "muted", style: { textAlign: "center", padding: "24px" } },
            "Watchlist is empty. Add a ticker above."
          )
        )
      );
    } else {
      for (const sym of symbols) {
        const tr = h(
          "tr",
          {},
          h("td", {}, h("a", { href: `#/stock/${sym}` }, sym)),
          h("td", { class: "muted", id: `wname-${sym}` }, "…"),
          h("td", { id: `wprice-${sym}` }, "…"),
          h("td", { id: `wchg-${sym}` }, "…"),
          h("td", { id: `wpct-${sym}` }, "…"),
          h("td", { class: "muted", id: `wvol-${sym}` }, "…"),
          h("td", { class: "muted", id: `w1m-${sym}` }, "…"),
          h("td", { class: "muted", id: `wytd-${sym}` }, "…"),
          h("td", { class: "muted", id: `wsharpe-${sym}` }, "…"),
          h(
            "td",
            { class: "right" },
            h("button", { class: "ghost", onclick: () => removeWatch(sym) }, "Remove")
          )
        );
        body.append(tr);
      }
    }
    panel.replaceChildren(tbl);
    if (symbols.length) {
      for (const q of quotes) fillRow(q);
      loadAnalytics();
    }
  }

  async function loadAnalytics() {
    try {
      const { metrics } = await api.watchlistAnalytics();
      for (const m of metrics || []) fillAnalytics(m);
    } catch {}
  }

  function fillAnalytics(m) {
    if (!m || !m.symbol) return;
    const sym = m.symbol;
    setPctCell(`w1m-${sym}`, m.oneMonth);
    setPctCell(`wytd-${sym}`, m.ytd);
    const sh = document.getElementById(`wsharpe-${sym}`);
    if (sh) {
      sh.textContent = typeof m.sharpe === "number" ? fmtNum(m.sharpe, 2) : "—";
      sh.className = typeof m.sharpe === "number" && m.sharpe >= 0 ? "up" : "muted";
    }
  }

  function setPctCell(id, v) {
    const el = document.getElementById(id);
    if (!el) return;
    if (typeof v !== "number") {
      el.textContent = "—";
      el.className = "muted";
      return;
    }
    el.textContent = fmtPct(v * 100);
    el.className = v >= 0 ? "up" : "down";
  }

  function fillRow(q) {
    if (!q || !q.symbol || q.error) {
      if (q && q.symbol) setText(`wprice-${q.symbol}`, "err");
      return;
    }
    const sym = q.symbol;
    const up = (q.change ?? 0) >= 0;
    setText(`wname-${sym}`, q.name ?? "—");
    setText(`wprice-${sym}`, fmtMoney(q.price, q.currency || "USD"));
    const chg = document.getElementById(`wchg-${sym}`);
    const pct = document.getElementById(`wpct-${sym}`);
    if (chg) {
      chg.textContent = fmtMoney(q.change ?? null, q.currency || "USD");
      chg.className = up ? "up" : "down";
    }
    if (pct) {
      pct.textContent = fmtPct(q.changePercent);
      pct.className = up ? "up" : "down";
    }
    setText(`wvol-${sym}`, fmtNum(q.volume ?? null, 0));
  }

  await draw();
  loadNews();
  watchlistTimer = setInterval(async () => {
    try {
      const { quotes } = await api.watchlistQuotes();
      for (const q of quotes || []) fillRow(q);
    } catch {}
  }, 30000);
}

function renderFeedNews(items, symbols) {
  const panel = h(
    "div",
    { class: "panel" },
    h(
      "div",
      { class: "row between" },
      h("h2", { style: { margin: 0 } }, "Recent News"),
      h(
        "div",
        { class: "muted small" },
        symbols.length ? `From ${symbols.join(", ")}` : ""
      )
    )
  );
  if (!items.length) {
    panel.append(
      h("div", { class: "muted", style: { marginTop: "8px" } }, "No recent news.")
    );
    return panel;
  }
  const list = h("div", { class: "stack", style: { marginTop: "12px" } });
  for (const n of items) {
    const when =
      typeof n.publishedAt === "number"
        ? new Date(n.publishedAt * 1000).toLocaleString()
        : n.publishedAt
        ? new Date(n.publishedAt).toLocaleString()
        : "";
    const text = h(
      "div",
      { style: { flex: "1", minWidth: "0" } },
      h(
        "div",
        { class: "row", style: { gap: "8px", alignItems: "baseline" } },
        n.symbol
          ? h("a", { href: `#/stock/${n.symbol}`, class: "pill" }, n.symbol)
          : null,
        h(
          "a",
          {
            href: n.link || "#",
            target: "_blank",
            rel: "noreferrer",
            style: { fontWeight: "500" },
          },
          n.title || "Untitled"
        )
      ),
      h("div", { class: "muted small" }, `${n.publisher ?? "—"} · ${when}`),
      n.summary
        ? h("div", { class: "muted small", style: { marginTop: "4px" } }, n.summary)
        : null
    );
    list.append(
      h(
        "div",
        {
          class: "news-item",
          style: {
            paddingBottom: "12px",
            borderBottom: "1px solid #1f2530",
          },
        },
        n.thumbnail ? h("img", { class: "news-thumb", src: n.thumbnail, loading: "lazy", alt: "" }) : null,
        text
      )
    );
  }
  panel.append(list);
  return panel;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ---------- search box ----------

function mountSearch(onPick, placeholder = "Search ticker…") {
  const wrap = h("div", { class: "search-wrap" });
  const input = h("input", { placeholder, autocomplete: "off" });
  const box = h("div", { class: "search-results", style: { display: "none" } });
  wrap.append(input, box);

  let t = null;
  input.addEventListener("input", () => {
    clearTimeout(t);
    const q = input.value.trim();
    if (!q) {
      box.style.display = "none";
      return;
    }
    t = setTimeout(async () => {
      try {
        const { results } = await api.search(q);
        box.replaceChildren();
        if (!results.length) {
          box.style.display = "none";
          return;
        }
        for (const r of results) {
          const btn = h(
            "button",
            {
              onclick: () => {
                onPick(r.symbol.toUpperCase());
                input.value = "";
                box.style.display = "none";
              },
            },
            h(
              "div",
              { class: "row between" },
              h("span", { style: { fontWeight: "500" } }, r.symbol),
              h("span", { class: "muted small" }, r.exchange ?? "")
            ),
            h("div", { class: "muted small" }, r.name ?? "")
          );
          box.append(btn);
        }
        box.style.display = "block";
      } catch {
        box.style.display = "none";
      }
    }, 200);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      onPick(input.value.trim().toUpperCase());
      input.value = "";
      box.style.display = "none";
    }
  });
  document.addEventListener("mousedown", (e) => {
    if (!wrap.contains(e.target)) box.style.display = "none";
  });
  return wrap;
}

// ---------- stock detail ----------

let stockTimers = [];

async function renderStock(symbol) {
  stockTimers.forEach(clearInterval);
  stockTimers = [];
  symbol = (symbol || "").toUpperCase();

  const root = h("div", { class: "stack" });
  app.replaceChildren(root);

  // Header
  const header = h(
    "div",
    {},
    h("div", { class: "muted small", id: "sname" }, "—"),
    h("h1", {}, symbol),
    h(
      "div",
      { class: "row", style: { alignItems: "baseline" } },
      h("span", { class: "price-big", id: "sprice" }, "…"),
      h("span", { id: "schange" }, "")
    ),
    h("div", { class: "muted small", id: "smeta" }, "")
  );
  root.append(header);

  // Chart panel
  const periods = [
    ["1D", "1d", "1m"],
    ["5D", "5d", "30m"],
    ["1M", "1mo", "1d"],
    ["6M", "6mo", "1d"],
    ["1Y", "1y", "1d"],
    ["5Y", "5y", "1wk"],
    ["MAX", "max", "1mo"],
  ];
  const chartButtons = h("div", { class: "row" });
  const chartWrap = h("div", { class: "chart-wrap" }, h("canvas", { id: "pricechart" }));
  const chartPanel = h("div", { class: "panel stack" }, chartButtons, chartWrap);
  root.append(chartPanel);

  let chartObj = null;
  let currentPeriod = 3;
  let prevClose = null;
  periods.forEach(([label], i) => {
    const btn = h(
      "button",
      {
        class: "ghost" + (i === currentPeriod ? " active" : ""),
        onclick: () => {
          currentPeriod = i;
          for (const b of chartButtons.querySelectorAll("button")) b.classList.remove("active");
          btn.classList.add("active");
          loadChart();
        },
      },
      label
    );
    chartButtons.append(btn);
  });

  async function loadChart() {
    const [, period, interval] = periods[currentPeriod];
    try {
      const { candles } = await api.history(symbol, period, interval);
      const data = candles.filter((c) => c.close != null).map((c) => ({ x: c.t, y: c.close }));
      const isIntraday = currentPeriod === 0; // "1D"
      const isFiveDay = currentPeriod === 1; // "5D"
      // Anchor the change comparison to the baseline: prev close for 1D,
      // the first candle's close (≈price at start of window) for 5D.
      const baselineValue = isIntraday && prevClose != null
        ? prevClose
        : data[0]?.y ?? 0;
      const baselineLabel = isIntraday ? "Prev close" : isFiveDay ? "5D ago" : null;
      const last = data[data.length - 1]?.y ?? 0;
      const up = last >= baselineValue;
      const color = up ? "#22c55e" : "#ef4444";
      if (chartObj) chartObj.destroy();
      const ctx = document.getElementById("pricechart").getContext("2d");
      const grad = ctx.createLinearGradient(0, 0, 0, 340);
      grad.addColorStop(0, color + "55");
      grad.addColorStop(1, color + "00");

      // For 1D, pin x-axis to the full US equities session (9:30–16:00 local
      // to the first candle) so early-session data doesn't compress to a dot.
      let xMin, xMax;
      if (isIntraday && data.length) {
        const first = new Date(data[0].x);
        const sessionOpen = new Date(first);
        sessionOpen.setHours(9, 30, 0, 0);
        const sessionClose = new Date(first);
        sessionClose.setHours(16, 0, 0, 0);
        xMin = sessionOpen.toISOString();
        xMax = sessionClose.toISOString();
      }

      const datasets = [
        {
          label: symbol,
          data,
          borderColor: color,
          backgroundColor: grad,
          borderWidth: 2,
          pointRadius: 0,
          fill: true,
          tension: 0.1,
          spanGaps: true,
        },
      ];
      if (baselineLabel && data.length) {
        datasets.push({
          label: baselineLabel,
          data: [
            { x: xMin ?? data[0].x, y: baselineValue },
            { x: xMax ?? data[data.length - 1].x, y: baselineValue },
          ],
          borderColor: "#8a93a6",
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          fill: false,
          tension: 0,
        });
      }

      // Moving averages: only meaningful with daily+ data, so skip 1D/5D.
      const showMA = !isIntraday && !isFiveDay && interval === "1d";
      const maLabels = new Set();
      if (showMA) {
        const sma50 = sma(data, 50);
        const sma200 = sma(data, 200);
        if (sma50.length) {
          datasets.push({
            label: "50-day MA",
            data: sma50,
            borderColor: "#f59e0b",
            borderWidth: 1.25,
            pointRadius: 0,
            fill: false,
            tension: 0,
          });
          maLabels.add("50-day MA");
        }
        if (sma200.length) {
          datasets.push({
            label: "200-day MA",
            data: sma200,
            borderColor: "#a78bfa",
            borderWidth: 1.25,
            pointRadius: 0,
            fill: false,
            tension: 0,
          });
          maLabels.add("200-day MA");
        }
      }

      chartObj = new Chart(ctx, {
        type: "line",
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: {
              display: showMA && maLabels.size > 0,
              position: "top",
              align: "end",
              labels: {
                color: "#8a93a6",
                filter: (item) => maLabels.has(item.text),
                boxWidth: 12,
                boxHeight: 2,
              },
            },
            tooltip: {
              filter: (item) => item.dataset.label !== baselineLabel,
              callbacks: { label: (c) => `${c.dataset.label}: ${fmtMoney(c.parsed.y)}` },
            },
          },
          scales: {
            x: {
              type: "time",
              time: { tooltipFormat: "PPpp" },
              min: xMin,
              max: xMax,
              ticks: { color: "#8a93a6" },
              grid: { color: "#1f2530" },
            },
            y: {
              ticks: { color: "#8a93a6", callback: (v) => v.toFixed(2) },
              grid: { color: "#1f2530" },
            },
          },
        },
      });
    } catch (e) {
      chartWrap.replaceChildren(h("div", { class: "error" }, `Chart error: ${e.message}`));
    }
  }

  async function loadQuote() {
    try {
      const q = await api.quote(symbol);
      const up = (q.change ?? 0) >= 0;
      prevClose = q.previousClose ?? null;
      setText("sname", q.name ?? "—");
      setText("sprice", fmtMoney(q.price, q.currency || "USD"));
      const c = document.getElementById("schange");
      if (c) {
        c.textContent = ` ${fmtMoney(q.change ?? null, q.currency || "USD")} (${fmtPct(q.changePercent)})`;
        c.className = up ? "up" : "down";
      }
      setText(
        "smeta",
        `Market ${q.marketState ?? "—"} · Vol ${fmtNum(q.volume ?? null, 0)} · Day ${fmtMoney(q.dayLow ?? null)} – ${fmtMoney(q.dayHigh ?? null)}`
      );
      return q.price;
    } catch (e) {
      setText("sprice", "err");
      setText("smeta", e.message);
      return null;
    }
  }

  // Placeholders for subsequent panels; filled as data arrives.
  const earningsBannerSlot = h("div");
  const analystSlot = h("div");
  const earningsHistorySlot = h("div");
  const aboutSlot = h("div");
  const metricsSlot = h("div");
  const optionsSlot = h("div", { class: "panel" }, h("div", { class: "muted" }, "Loading options…"));
  const newsSlot = h("div", { class: "panel" }, h("div", { class: "muted" }, "Loading news…"));
  const bottomGrid = h("div", { class: "grid cols-2" }, optionsSlot, newsSlot);
  // Order: banner -> chart (above) -> analyst -> earnings history -> about -> metrics -> bottom.
  root.insertBefore(earningsBannerSlot, chartPanel);
  root.append(analystSlot, earningsHistorySlot, aboutSlot, metricsSlot, bottomGrid);

  const currentPrice = await loadQuote();
  loadChart();
  stockTimers.push(setInterval(loadQuote, 30000));

  // Analyst
  try {
    const a = await api.analyst(symbol);
    analystSlot.replaceChildren(renderAnalyst(a, currentPrice));
  } catch (e) {
    analystSlot.replaceChildren(h("div", { class: "panel error" }, `Analyst data unavailable: ${e.message}`));
  }

  // Info (about + metrics)
  try {
    const info = await api.info(symbol);
    if (info && typeof info.longBusinessSummary === "string" && info.longBusinessSummary) {
      aboutSlot.replaceChildren(
        h(
          "div",
          { class: "panel" },
          h("h2", {}, `About ${info.shortName || symbol}`),
          h(
            "div",
            { class: "muted small" },
            [info.sector, info.industry, info.country].filter(Boolean).join(" · ") || "—"
          ),
          h("p", { class: "muted", style: { lineHeight: "1.6", marginTop: "8px" } }, info.longBusinessSummary)
        )
      );
    }
    metricsSlot.replaceChildren(renderMetrics(info || {}));
  } catch (e) {
    metricsSlot.replaceChildren(h("div", { class: "panel error" }, `Metrics unavailable: ${e.message}`));
  }

  // Earnings
  api
    .earnings(symbol)
    .then((e) => {
      const banner = renderEarningsBanner(e);
      if (banner) earningsBannerSlot.replaceChildren(banner);
      const hist = renderEarningsHistory(e);
      if (hist) earningsHistorySlot.replaceChildren(hist);
    })
    .catch(() => {});

  // Options
  loadOptions(symbol, optionsSlot);

  // News
  api
    .news(symbol)
    .then(({ items }) => newsSlot.replaceChildren(renderNews(items || [])))
    .catch((e) => newsSlot.replaceChildren(h("div", { class: "panel error" }, `News unavailable: ${e.message}`)));
}

function ratingLabel(mean) {
  if (mean == null) return { label: "—", cls: "muted" };
  if (mean <= 1.5) return { label: "Strong Buy", cls: "up" };
  if (mean <= 2.5) return { label: "Buy", cls: "up" };
  if (mean <= 3.5) return { label: "Hold", cls: "muted" };
  if (mean <= 4.5) return { label: "Underperform", cls: "down" };
  return { label: "Sell", cls: "down" };
}

function renderAnalyst(a, currentPrice) {
  const r = ratingLabel(a.recommendationMean);
  const upside =
    a.targetMean != null && currentPrice != null
      ? ((a.targetMean - currentPrice) / currentPrice) * 100
      : null;

  const head = h(
    "div",
    { class: "grid cols-4" },
    h(
      "div",
      {},
      h("div", { class: "muted small" }, "Consensus"),
      h("div", { class: r.cls, style: { fontSize: "18px", fontWeight: "600" } }, r.label),
      h(
        "div",
        { class: "muted small" },
        `${a.numberOfAnalystOpinions ?? "?"} analysts · mean ${fmtNum(a.recommendationMean)}`
      )
    ),
    h(
      "div",
      {},
      h("div", { class: "muted small" }, "Target (mean)"),
      h("div", { style: { fontSize: "18px", fontWeight: "600" } }, fmtMoney(a.targetMean ?? null)),
      upside != null
        ? h(
            "div",
            { class: upside >= 0 ? "up small" : "down small" },
            `${upside >= 0 ? "+" : ""}${upside.toFixed(2)}% vs current`
          )
        : null
    ),
    h(
      "div",
      {},
      h("div", { class: "muted small" }, "Target range"),
      h(
        "div",
        {},
        `${fmtMoney(a.targetLow ?? null)} – ${fmtMoney(a.targetHigh ?? null)}`
      ),
      h("div", { class: "muted small" }, `Median ${fmtMoney(a.targetMedian ?? null)}`)
    ),
    h(
      "div",
      {},
      h("div", { class: "muted small" }, "Rating"),
      h("div", {}, a.recommendationKey ?? "—")
    )
  );

  const panel = h("div", { class: "panel stack" }, h("h2", {}, "Analyst Ratings"), head);

  const ug = a.upgradesDowngrades || [];
  if (ug.length) {
    const tbl = h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Date"),
          h("th", {}, "Firm"),
          h("th", {}, "Action"),
          h("th", {}, "From"),
          h("th", {}, "To")
        )
      ),
      h(
        "tbody",
        {},
        ...ug.slice(0, 15).map((r) =>
          h(
            "tr",
            {},
            h("td", { class: "muted small" }, String(r.GradeDate ?? r.index ?? "")),
            h("td", {}, String(r.Firm ?? "")),
            h("td", {}, String(r.Action ?? "")),
            h("td", { class: "muted" }, String(r.FromGrade ?? "")),
            h("td", {}, String(r.ToGrade ?? ""))
          )
        )
      )
    );
    panel.append(
      h("div", { class: "muted small" }, "Recent Upgrades / Downgrades"),
      h("div", { class: "scroll-y" }, tbl)
    );
  }

  return panel;
}

function renderMetrics(info) {
  function pct(v) {
    return typeof v === "number" ? fmtPct(v * 100) : "—";
  }
  function rawPct(v) {
    return typeof v === "number" ? fmtPct(v) : "—";
  }
  function money(v) {
    return typeof v === "number" ? fmtMoney(v) : "—";
  }
  function num(v, d = 2) {
    return typeof v === "number" ? fmtNum(v, d) : "—";
  }
  const groups = [
    [
      "Valuation",
      [
        ["Market Cap", money(info.marketCap)],
        ["Enterprise Value", money(info.enterpriseValue)],
        ["Trailing P/E", num(info.trailingPE)],
        ["Forward P/E", num(info.forwardPE)],
        ["Price / Book", num(info.priceToBook)],
        ["Price / Sales", num(info.priceToSalesTrailing12Months)],
      ],
    ],
    [
      "Profitability",
      [
        ["Profit Margin", pct(info.profitMargins)],
        ["Operating Margin", pct(info.operatingMargins)],
        ["Gross Margin", pct(info.grossMargins)],
        ["Return on Equity", pct(info.returnOnEquity)],
        ["Return on Assets", pct(info.returnOnAssets)],
        ["EBITDA", money(info.ebitda)],
      ],
    ],
    [
      "Financials",
      [
        ["Revenue (TTM)", money(info.totalRevenue)],
        ["Revenue Growth", pct(info.revenueGrowth)],
        ["Net Income", money(info.netIncomeToCommon)],
        ["Free Cash Flow", money(info.freeCashflow)],
        ["Total Cash", money(info.totalCash)],
        ["Total Debt", money(info.totalDebt)],
      ],
    ],
    [
      "Trading",
      [
        ["52W High", money(info.fiftyTwoWeekHigh)],
        ["52W Low", money(info.fiftyTwoWeekLow)],
        ["52W Change", rawPct(info["52WeekChange"])],
        ["50D Avg", money(info.fiftyDayAverage)],
        ["200D Avg", money(info.twoHundredDayAverage)],
        ["Beta", num(info.beta)],
      ],
    ],
    [
      "Shares & Dividends",
      [
        ["Shares Outstanding", num(info.sharesOutstanding, 0)],
        ["Float", num(info.floatShares, 0)],
        ["Insider %", pct(info.heldPercentInsiders)],
        ["Institutional %", pct(info.heldPercentInstitutions)],
        ["Dividend Yield", pct(info.dividendYield)],
        ["Dividend Rate", num(info.dividendRate)],
      ],
    ],
    [
      "Per Share",
      [
        ["Trailing EPS", num(info.trailingEps)],
        ["Forward EPS", num(info.forwardEps)],
        ["Revenue / Share", num(info.revenuePerShare)],
        ["Debt / Equity", num(info.debtToEquity)],
        ["Current Ratio", num(info.currentRatio)],
        ["Quick Ratio", num(info.quickRatio)],
      ],
    ],
  ];

  return h(
    "div",
    { class: "grid cols-3" },
    ...groups.map(([title, rows]) =>
      h(
        "div",
        { class: "panel" },
        h("h2", {}, title),
        ...rows.map(([k, v]) =>
          h("div", { class: "kv" }, h("span", { class: "k" }, k), h("span", {}, v))
        )
      )
    )
  );
}

async function loadOptions(symbol, slot) {
  try {
    const first = await api.options(symbol);
    const expirations = first.expirations || [];
    let expiration = first.expiration || expirations[0] || null;
    let tab = "calls";

    async function render() {
      const data = await api.options(symbol, expiration || undefined);
      const rows = (tab === "calls" ? data.calls : data.puts) || [];

      const select = h(
        "select",
        {
          onchange: (e) => {
            expiration = e.target.value;
            render();
          },
        },
        ...expirations.map((x) =>
          h("option", { value: x, selected: x === expiration ? "selected" : null }, x)
        )
      );
      const callsBtn = h(
        "button",
        {
          class: "ghost" + (tab === "calls" ? " active" : ""),
          onclick: () => {
            tab = "calls";
            render();
          },
        },
        "Calls"
      );
      const putsBtn = h(
        "button",
        {
          class: "ghost" + (tab === "puts" ? " active" : ""),
          onclick: () => {
            tab = "puts";
            render();
          },
        },
        "Puts"
      );

      const body = h("tbody", {});
      if (!rows.length) {
        body.append(
          h(
            "tr",
            {},
            h(
              "td",
              { colspan: 9, class: "muted", style: { textAlign: "center", padding: "24px" } },
              "No options data."
            )
          )
        );
      } else {
        for (const r of rows) {
          const chg = r.percentChange;
          body.append(
            h(
              "tr",
              { style: r.inTheMoney ? { background: "#131a22" } : null },
              h("td", { style: { fontWeight: "500" } }, fmtMoney(r.strike ?? null)),
              h("td", {}, fmtMoney(r.lastPrice ?? null)),
              h("td", { class: "muted" }, fmtMoney(r.bid ?? null)),
              h("td", { class: "muted" }, fmtMoney(r.ask ?? null)),
              h("td", { class: (chg ?? 0) >= 0 ? "up" : "down" }, fmtPct(chg ?? null)),
              h("td", {}, fmtNum(r.volume ?? null, 0)),
              h("td", {}, fmtNum(r.openInterest ?? null, 0)),
              h("td", {}, r.impliedVolatility != null ? fmtPct(r.impliedVolatility * 100) : "—"),
              h("td", {}, r.inTheMoney ? "✓" : "")
            )
          );
        }
      }

      slot.className = "panel stack";
      slot.replaceChildren(
        h(
          "div",
          { class: "row between" },
          h("h2", { style: { margin: 0 } }, "Options Chain"),
          h("div", { class: "row" }, select, callsBtn, putsBtn)
        ),
        h(
          "div",
          { class: "scroll-y" },
          h(
            "table",
            {},
            h(
              "thead",
              {},
              h(
                "tr",
                {},
                h("th", {}, "Strike"),
                h("th", {}, "Last"),
                h("th", {}, "Bid"),
                h("th", {}, "Ask"),
                h("th", {}, "Chg %"),
                h("th", {}, "Vol"),
                h("th", {}, "OI"),
                h("th", {}, "IV"),
                h("th", {}, "ITM")
              )
            ),
            body
          )
        )
      );
    }
    await render();
  } catch (e) {
    slot.replaceChildren(h("div", { class: "panel error" }, `Options unavailable: ${e.message}`));
  }
}

function renderNews(items) {
  const panel = h("div", { class: "panel" }, h("h2", {}, "News"));
  if (!items.length) {
    panel.append(h("div", { class: "muted" }, "No recent news."));
    return panel;
  }
  const list = h("div", { class: "scroll-y stack" });
  for (const n of items) {
    const when =
      typeof n.publishedAt === "number"
        ? new Date(n.publishedAt * 1000).toLocaleString()
        : n.publishedAt
        ? new Date(n.publishedAt).toLocaleString()
        : "";
    const text = h(
      "div",
      { style: { flex: "1", minWidth: "0" } },
      h(
        "a",
        { href: n.link || "#", target: "_blank", rel: "noreferrer", style: { fontWeight: "500" } },
        n.title || "Untitled"
      ),
      h("div", { class: "muted small" }, `${n.publisher ?? "—"} · ${when}`),
      n.summary ? h("div", { class: "muted small", style: { marginTop: "4px" } }, n.summary) : null
    );
    list.append(
      h(
        "div",
        {
          class: "news-item",
          style: { paddingBottom: "12px", borderBottom: "1px solid #1f2530" },
        },
        n.thumbnail ? h("img", { class: "news-thumb", src: n.thumbnail, loading: "lazy", alt: "" }) : null,
        text
      )
    );
  }
  panel.append(list);
  return panel;
}

// ---------- earnings ----------

function daysUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.valueOf())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((target - today) / (1000 * 60 * 60 * 24));
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.valueOf())) return String(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function renderEarningsBanner(e) {
  if (!e || (!e.nextEarnings && !e.dividendDate && !e.exDividendDate)) return null;
  const days = daysUntil(e.nextEarnings);
  const soon = days != null && days >= 0 && days <= 7;
  const past = days != null && days < 0;
  const daysText =
    days == null
      ? ""
      : past
      ? `${-days} day${-days === 1 ? "" : "s"} ago`
      : days === 0
      ? "today"
      : `in ${days} day${days === 1 ? "" : "s"}`;

  const eps = e.epsEstimate || {};
  const rev = e.revenueEstimate || {};

  const cards = [];
  if (e.nextEarnings) {
    cards.push(
      h(
        "div",
        { class: "panel", style: soon ? { borderColor: "#4f8cff" } : null },
        h("div", { class: "muted small" }, "Next earnings"),
        h("div", { style: { fontSize: "18px", fontWeight: "600" } }, fmtDate(e.nextEarnings)),
        daysText
          ? h("div", { class: soon ? "small" : "muted small", style: soon ? { color: "#4f8cff" } : null }, daysText)
          : null
      )
    );
  }
  if (eps.average != null || eps.low != null || eps.high != null) {
    cards.push(
      h(
        "div",
        { class: "panel" },
        h("div", { class: "muted small" }, "EPS estimate"),
        h("div", { style: { fontSize: "18px", fontWeight: "600" } }, fmtNum(eps.average ?? null)),
        h(
          "div",
          { class: "muted small" },
          `Range ${fmtNum(eps.low ?? null)} – ${fmtNum(eps.high ?? null)}`
        )
      )
    );
  }
  if (rev.average != null || rev.low != null || rev.high != null) {
    cards.push(
      h(
        "div",
        { class: "panel" },
        h("div", { class: "muted small" }, "Revenue estimate"),
        h("div", { style: { fontSize: "18px", fontWeight: "600" } }, fmtMoney(rev.average ?? null)),
        h(
          "div",
          { class: "muted small" },
          `Range ${fmtMoney(rev.low ?? null)} – ${fmtMoney(rev.high ?? null)}`
        )
      )
    );
  }
  if (e.exDividendDate || e.dividendDate) {
    cards.push(
      h(
        "div",
        { class: "panel" },
        h("div", { class: "muted small" }, "Dividend"),
        h("div", { style: { fontSize: "14px" } }, `Ex-div ${fmtDate(e.exDividendDate)}`),
        h("div", { class: "muted small" }, `Pay ${fmtDate(e.dividendDate)}`)
      )
    );
  }
  if (!cards.length) return null;
  const cls = cards.length >= 4 ? "grid cols-4" : cards.length === 3 ? "grid cols-3" : "grid cols-2";
  return h("div", { class: cls }, ...cards);
}

function renderEarningsHistory(e) {
  const history = (e && e.history) || [];
  if (!history.length) return null;
  const rows = history.filter((r) => r.epsReported != null).slice(0, 8);
  if (!rows.length) return null;

  const body = h("tbody", {});
  for (const r of rows) {
    const beat = (r.surprisePercent ?? 0) >= 0;
    body.append(
      h(
        "tr",
        {},
        h("td", {}, fmtDate(r.date)),
        h("td", {}, fmtNum(r.epsEstimate ?? null)),
        h("td", { style: { fontWeight: "500" } }, fmtNum(r.epsReported ?? null)),
        h(
          "td",
          { class: beat ? "up" : "down" },
          r.surprisePercent != null ? fmtPct(r.surprisePercent) : "—"
        ),
        h(
          "td",
          {},
          r.surprisePercent != null
            ? h("span", { class: "pill " + (beat ? "up" : "down") }, beat ? "Beat" : "Miss")
            : ""
        )
      )
    );
  }

  return h(
    "div",
    { class: "panel stack" },
    h("h2", {}, "Earnings History"),
    h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Date"),
          h("th", {}, "EPS estimate"),
          h("th", {}, "EPS reported"),
          h("th", {}, "Surprise"),
          h("th", {}, "")
        )
      ),
      body
    )
  );
}

// ---------- holdings ----------

let holdingsTimer = null;

async function renderHoldings() {
  clearInterval(holdingsTimer);
  const root = h("div", { class: "stack" });
  app.replaceChildren(root);

  const head = h(
    "div",
    {},
    h("h1", {}, "Holdings"),
    h("div", { class: "muted small" }, "Track cost basis vs. live market value.")
  );
  const totalsSlot = h("div");
  const analyticsSlot = h("div");
  const chartSlot = h("div");
  const form = h("div");
  const tableSlot = h("div", { class: "panel", style: { padding: 0, overflow: "hidden" } });
  root.append(head, totalsSlot, analyticsSlot, chartSlot, form, tableSlot);
  let analyticsChart = null;

  function mountForm() {
    const symInput = h("input", {
      placeholder: "e.g. AAPL",
      autocomplete: "off",
      oninput: (e) => (e.target.value = e.target.value.toUpperCase()),
    });
    const symWrap = h("div", { class: "search-wrap" });
    const symResults = h("div", { class: "search-results", style: { display: "none" } });
    symWrap.append(symInput, symResults);

    let searchTimer = null;
    symInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const q = symInput.value.trim();
      if (!q) {
        symResults.style.display = "none";
        return;
      }
      searchTimer = setTimeout(async () => {
        try {
          const { results } = await api.search(q);
          symResults.replaceChildren();
          if (!results.length) {
            symResults.style.display = "none";
            return;
          }
          for (const r of results) {
            symResults.append(
              h(
                "button",
                {
                  type: "button",
                  onclick: () => {
                    symInput.value = r.symbol.toUpperCase();
                    symResults.style.display = "none";
                  },
                },
                h(
                  "div",
                  { class: "row between" },
                  h("span", { style: { fontWeight: "500" } }, r.symbol),
                  h("span", { class: "muted small" }, r.exchange ?? "")
                ),
                h("div", { class: "muted small" }, r.name ?? "")
              )
            );
          }
          symResults.style.display = "block";
        } catch {
          symResults.style.display = "none";
        }
      }, 200);
    });
    document.addEventListener("mousedown", (e) => {
      if (!symWrap.contains(e.target)) symResults.style.display = "none";
    });

    const sharesIn = h("input", { placeholder: "e.g. 10", inputmode: "decimal" });
    const costIn = h("input", { placeholder: "e.g. 150.25", inputmode: "decimal" });
    const submit = h("button", { type: "submit" }, "Add holding");
    const errMsg = h("div", { class: "small down", style: { display: "none", marginTop: "8px" } });

    const formEl = h(
      "form",
      {
        class: "panel",
        onsubmit: async (e) => {
          e.preventDefault();
          errMsg.style.display = "none";

          const sym = symInput.value.trim().toUpperCase();
          const sh = parseFloat(sharesIn.value);
          const cb = parseFloat(costIn.value);

          if (!sym) return showErr("Enter a ticker symbol.");
          if (!isFinite(sh) || sh <= 0) return showErr("Shares must be a positive number.");
          if (!isFinite(cb) || cb < 0) return showErr("Cost per share must be a non-negative number.");

          submit.disabled = true;
          try {
            await api.holdingAdd(sym, sh, cb);
            symInput.value = "";
            sharesIn.value = "";
            costIn.value = "";
            symResults.style.display = "none";
            await draw();
          } catch (err) {
            showErr(`Could not add holding: ${err.message}`);
          } finally {
            submit.disabled = false;
          }
        },
      },
      h(
        "div",
        { class: "row", style: { alignItems: "flex-end" } },
        h(
          "div",
          { style: { minWidth: "220px", flex: "1" } },
          h("div", { class: "muted small", style: { marginBottom: "4px" } }, "Symbol"),
          symWrap
        ),
        h(
          "div",
          {},
          h("div", { class: "muted small", style: { marginBottom: "4px" } }, "Shares"),
          sharesIn
        ),
        h(
          "div",
          {},
          h("div", { class: "muted small", style: { marginBottom: "4px" } }, "Cost / share"),
          costIn
        ),
        submit
      ),
      errMsg
    );

    function showErr(msg) {
      errMsg.textContent = msg;
      errMsg.style.display = "block";
    }

    form.replaceChildren(formEl);
  }
  mountForm();

  async function draw() {
    const data = await api.holdings();
    const t = data.totals || {};
    const tUp = (t.gain ?? 0) >= 0;
    totalsSlot.replaceChildren(
      h(
        "div",
        { class: "grid cols-4" },
        totalCard("Cost basis", fmtMoney(t.cost)),
        totalCard("Market value", fmtMoney(t.value)),
        totalCard("Unrealized P/L", fmtMoney(t.gain), tUp ? "up" : "down"),
        totalCard("Return", fmtPct(t.gainPercent), tUp ? "up" : "down")
      )
    );

    const tbl = h(
      "table",
      {},
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          h("th", {}, "Symbol"),
          h("th", {}, "Shares"),
          h("th", {}, "Cost / share"),
          h("th", {}, "Price"),
          h("th", {}, "Market value"),
          h("th", {}, "Cost value"),
          h("th", {}, "P/L"),
          h("th", {}, "Return"),
          h("th", {})
        )
      ),
      h("tbody", {})
    );
    const body = tbl.querySelector("tbody");
    if (!data.holdings.length) {
      body.append(
        h(
          "tr",
          {},
          h(
            "td",
            { colspan: 9, class: "muted", style: { textAlign: "center", padding: "24px" } },
            "No holdings yet."
          )
        )
      );
    } else {
      for (const holding of data.holdings) {
        const up = (holding.gain ?? 0) >= 0;
        body.append(
          h(
            "tr",
            {},
            h("td", {}, h("a", { href: `#/stock/${holding.symbol}` }, holding.symbol)),
            h("td", {}, fmtNum(holding.shares, 4)),
            h("td", {}, fmtMoney(holding.costBasis)),
            h("td", {}, fmtMoney(holding.price ?? null)),
            h("td", {}, fmtMoney(holding.marketValue ?? null)),
            h("td", { class: "muted" }, fmtMoney(holding.costValue ?? null)),
            h("td", { class: up ? "up" : "down" }, fmtMoney(holding.gain ?? null)),
            h("td", { class: up ? "up" : "down" }, fmtPct(holding.gainPercent ?? null)),
            h(
              "td",
              { class: "right" },
              h(
                "button",
                {
                  class: "danger",
                  onclick: async () => {
                    await api.holdingRemove(holding.id);
                    await draw();
                  },
                },
                "Remove"
              )
            )
          )
        );
      }
    }
    tableSlot.replaceChildren(tbl);
  }

  function totalCard(label, value, cls = "") {
    return h(
      "div",
      { class: "panel" },
      h("div", { class: "muted small" }, label),
      h("div", { class: cls, style: { fontSize: "20px", fontWeight: "600" } }, value)
    );
  }

  async function loadAnalytics() {
    try {
      const a = await api.portfolioAnalytics("1y");
      analyticsSlot.replaceChildren(renderPortfolioKpis(a));
      chartSlot.replaceChildren(renderPortfolioCurve(a, (chart) => (analyticsChart = chart)));
    } catch (e) {
      analyticsSlot.replaceChildren(
        h("div", { class: "panel error" }, `Analytics unavailable: ${e.message}`)
      );
      chartSlot.replaceChildren();
    }
  }

  await draw();
  loadAnalytics();
  holdingsTimer = setInterval(draw, 30000);
}

function renderPortfolioKpis(a) {
  const k = a.kpis || {};
  if (k.sharpe == null && k.volatility == null && k.beta == null) {
    return h(
      "div",
      { class: "panel muted small" },
      "Analytics unavailable — add holdings and enough history to compute."
    );
  }
  function num(v, d = 2) {
    return typeof v === "number" ? fmtNum(v, d) : "—";
  }
  function pct(v) {
    return typeof v === "number" ? fmtPct(v * 100) : "—";
  }
  const cumUp = (k.cumulativeReturn ?? 0) >= 0;
  const bench = a.benchmark || "SPY";
  return h(
    "div",
    { class: "grid cols-4" },
    kpiCard(`Return (${a.period || "1y"})`, pct(k.cumulativeReturn), cumUp ? "up" : "down"),
    kpiCard(`${bench} return`, pct(k.benchmarkReturn), (k.benchmarkReturn ?? 0) >= 0 ? "up" : "down"),
    kpiCard("Sharpe", num(k.sharpe)),
    kpiCard("Sortino", num(k.sortino)),
    kpiCard("Volatility", pct(k.volatility)),
    kpiCard("Max drawdown", pct(k.maxDrawdown), "down"),
    kpiCard(`Beta vs ${bench}`, num(k.beta)),
    kpiCard("Alpha (ann.)", pct(k.alpha), (k.alpha ?? 0) >= 0 ? "up" : "down")
  );
}

function kpiCard(label, value, cls = "") {
  return h(
    "div",
    { class: "panel" },
    h("div", { class: "muted small" }, label),
    h("div", { class: cls, style: { fontSize: "18px", fontWeight: "600" } }, value)
  );
}

function renderPortfolioCurve(a, onChart) {
  const points = a.curve || [];
  const bench = a.benchmark || "SPY";
  const panel = h(
    "div",
    { class: "panel stack" },
    h("h2", {}, `Cumulative return vs ${bench}`),
    h("div", { class: "muted small" }, `Assumes current shares held throughout ${a.period || "1y"}.`)
  );
  if (points.length < 2) {
    panel.append(h("div", { class: "muted" }, "Not enough history to plot."));
    return panel;
  }
  const wrap = h("div", { class: "chart-wrap" }, h("canvas"));
  panel.append(wrap);
  // Defer chart construction to next tick so the canvas is in the DOM.
  setTimeout(() => {
    const canvas = wrap.querySelector("canvas");
    if (!canvas) return;
    const port = points.map((p) => ({ x: p.t, y: (p.portfolio ?? 0) * 100 }));
    const spy = points.map((p) => ({ x: p.t, y: (p.benchmark ?? 0) * 100 }));
    const ctx = canvas.getContext("2d");
    const chart = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [
          {
            label: "Portfolio",
            data: port,
            borderColor: "#4f8cff",
            backgroundColor: "#4f8cff22",
            borderWidth: 2,
            pointRadius: 0,
            fill: false,
            tension: 0.1,
          },
          {
            label: bench,
            data: spy,
            borderColor: "#8a93a6",
            borderWidth: 1.25,
            pointRadius: 0,
            fill: false,
            tension: 0.1,
            borderDash: [4, 4],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: true,
            position: "top",
            align: "end",
            labels: { color: "#8a93a6", boxWidth: 12, boxHeight: 2 },
          },
          tooltip: {
            callbacks: { label: (c) => `${c.dataset.label}: ${fmtPct(c.parsed.y)}` },
          },
        },
        scales: {
          x: {
            type: "time",
            time: { tooltipFormat: "PP" },
            ticks: { color: "#8a93a6" },
            grid: { color: "#1f2530" },
          },
          y: {
            ticks: { color: "#8a93a6", callback: (v) => `${v.toFixed(0)}%` },
            grid: { color: "#1f2530" },
          },
        },
      },
    });
    if (typeof onChart === "function") onChart(chart);
  }, 0);
  return panel;
}

// ---------- router ----------

function router() {
  const hash = location.hash.replace(/^#/, "") || "/";
  document.querySelectorAll("nav a").forEach((a) => {
    const href = a.getAttribute("href").replace(/^#/, "");
    a.classList.toggle("active", href === hash);
  });
  if (hash === "/" || hash === "") return renderWatchlist();
  if (hash === "/holdings") return renderHoldings();
  const m = hash.match(/^\/stock\/(.+)$/);
  if (m) return renderStock(decodeURIComponent(m[1]));
  app.replaceChildren(h("div", { class: "panel" }, "Not found. ", h("a", { href: "#/" }, "Home")));
}

window.addEventListener("hashchange", router);
router();

// ---------- market strip ----------

const MARKET_INDICES = [
  { symbol: "^GSPC", label: "S&P 500" },
  { symbol: "^IXIC", label: "NASDAQ" },
  { symbol: "^DJI", label: "DOW" },
  { symbol: "^RUT", label: "RUSSELL" },
  { symbol: "^VIX", label: "VIX" },
  { symbol: "^TNX", label: "10Y" },
  { symbol: "BTC-USD", label: "BTC" },
  { symbol: "ETH-USD", label: "ETH" },
];

async function renderMarketStrip() {
  const strip = document.getElementById("market-strip");
  if (!strip) return;
  if (!strip.firstChild) {
    strip.replaceChildren(
      h(
        "div",
        { class: "market-strip-inner muted" },
        "Loading market data…"
      )
    );
  }
  try {
    const { quotes } = await api.quotes(MARKET_INDICES.map((m) => m.symbol));
    const bySym = new Map(quotes.map((q) => [q.symbol, q]));
    const inner = h("div", { class: "market-strip-inner" });
    for (const { symbol, label } of MARKET_INDICES) {
      const q = bySym.get(symbol);
      if (!q || q.error || q.price == null) continue;
      const up = (q.change ?? 0) >= 0;
      // Indices use different magnitudes than stocks; format as plain numbers.
      const price = q.price.toLocaleString("en-US", { maximumFractionDigits: 2 });
      const pct = fmtPct(q.changePercent);
      inner.append(
        h(
          "span",
          { class: "market-item" },
          h("span", { class: "sym" }, label),
          h("span", { class: "price" }, price),
          h("span", { class: up ? "up small" : "down small" }, pct)
        )
      );
    }
    if (!inner.childNodes.length) {
      inner.append(h("span", { class: "muted small" }, "Market data unavailable."));
    }
    strip.replaceChildren(inner);
  } catch (e) {
    strip.replaceChildren(
      h("div", { class: "market-strip-inner muted small" }, `Market data unavailable: ${e.message}`)
    );
  }
}

renderMarketStrip();
setInterval(renderMarketStrip, 30000);
