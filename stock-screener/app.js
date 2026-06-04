/* =========================================================================
 * Elite Magic Trader — stock screener logic
 *
 * Pipeline:  raw universe  ->  Magic Score (ranked)  ->  filters  ->  sort  ->  render
 *
 * The "Magic Score" is a 0-100 blend of Joel Greenblatt's Magic Formula:
 * rank every stock by earnings yield (cheapness) and by return on capital
 * (quality), sum the two ranks, then rescale so the best combined rank = 100.
 * ========================================================================= */

(function () {
  "use strict";

  // ---- working dataset (swapped out if live data is fetched) -------------
  let universe = STOCK_UNIVERSE.map((s) => ({ ...s }));

  // ---- formatting helpers ------------------------------------------------
  const fmtMoney = (n) =>
    n == null ? "—" : "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const fmtCap = (n) => {
    if (n == null) return "—";
    if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
    return "$" + n.toLocaleString();
  };

  const fmtNum = (n, d = 1) => (n == null ? "—" : n.toFixed(d));
  const fmtPct = (n, d = 1) => (n == null ? "—" : n.toFixed(d) + "%");
  const fmtVol = (n) => (n == null ? "—" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : (n / 1e3).toFixed(0) + "K");
  const signed = (n) => (n == null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : "");

  // ---- Magic Formula scoring --------------------------------------------
  // Higher earnings yield and higher ROIC are both better -> rank descending.
  function computeMagicScores(list) {
    const rankDesc = (key) => {
      const order = [...list].sort((a, b) => (b[key] ?? -1e9) - (a[key] ?? -1e9));
      const rankOf = new Map();
      order.forEach((s, i) => rankOf.set(s.ticker, i + 1)); // 1 = best
      return rankOf;
    };
    const eyRank = rankDesc("earnYield");
    const roicRank = rankDesc("roic");

    // Combined rank: lower is better. Map to 0-100 where best = 100.
    const combined = list.map((s) => ({
      ticker: s.ticker,
      sum: eyRank.get(s.ticker) + roicRank.get(s.ticker),
    }));
    const sums = combined.map((c) => c.sum);
    const minSum = Math.min(...sums);
    const maxSum = Math.max(...sums);
    const span = maxSum - minSum || 1;
    const scoreOf = new Map();
    combined.forEach((c) => scoreOf.set(c.ticker, Math.round(((maxSum - c.sum) / span) * 100)));

    list.forEach((s) => (s.magicScore = scoreOf.get(s.ticker)));
    return list;
  }

  // ---- technical signal glyphs ------------------------------------------
  function signalFor(s) {
    const parts = [];
    if (s.price > s.sma50 && s.price > s.sma200) parts.push({ g: "▲", cls: "pos" });
    else if (s.price < s.sma50 && s.price < s.sma200) parts.push({ g: "▽", cls: "neg" });
    else parts.push({ g: "•", cls: "" });
    if (s.rsi14 != null && s.rsi14 < 35) parts.push({ g: "⚑", cls: "pos" }); // oversold = potential buy
    else if (s.rsi14 != null && s.rsi14 > 70) parts.push({ g: "⚑", cls: "neg" }); // overbought
    return parts;
  }

  // =======================================================================
  //  Column definitions (drive both header and body rendering)
  // =======================================================================
  const COLUMNS = [
    { key: "ticker", label: "Ticker", txt: true, sortType: "str",
      render: (s) => `<span class="ticker">${s.ticker}</span><div class="coname">${s.name}</div><span class="sector-tag">${s.sector}</span>` },
    { key: "price", label: "Price", sortType: "num", render: (s) => fmtMoney(s.price) },
    { key: "perfYTD", label: "YTD", sortType: "num",
      render: (s) => `<span class="${signed(s.perfYTD)}">${fmtPct(s.perfYTD)}</span>` },
    { key: "perfMonth", label: "1M", sortType: "num",
      render: (s) => `<span class="${signed(s.perfMonth)}">${fmtPct(s.perfMonth)}</span>` },
    { key: "marketCap", label: "Mkt Cap", sortType: "num", render: (s) => fmtCap(s.marketCap) },
    { key: "pe", label: "P/E", sortType: "num", render: (s) => fmtNum(s.pe) },
    { key: "divYield", label: "Div %", sortType: "num", render: (s) => fmtPct(s.divYield, 2) },
    { key: "roic", label: "ROIC", sortType: "num", render: (s) => fmtPct(s.roic) },
    { key: "earnYield", label: "Earn Yld", sortType: "num", render: (s) => fmtPct(s.earnYield) },
    { key: "rsi14", label: "RSI", sortType: "num",
      render: (s) => `<span class="${s.rsi14 < 35 ? "pos" : s.rsi14 > 70 ? "neg" : ""}">${fmtNum(s.rsi14, 0)}</span>` },
    { key: "signal", label: "Signal", sortType: "num", sortKey: (s) => (s.price > s.sma200 ? 1 : 0),
      render: (s) => `<span class="signal">${signalFor(s).map((p) => `<span class="${p.cls}">${p.g}</span>`).join("")}</span>` },
    { key: "magicScore", label: "Magic", sortType: "num",
      render: (s) => `<span class="magic-cell"><span class="magic-bar"><span class="magic-fill" style="width:${s.magicScore}%"></span></span><span class="magic-val">${s.magicScore}</span></span>` },
  ];

  // =======================================================================
  //  Presets — one-click screens
  // =======================================================================
  const PRESETS = [
    { id: "magic", name: "Magic Formula", desc: "Cheap + high quality", apply: (st) => { reset(st); st.magicMin = 70; st.sort = { key: "magicScore", dir: -1 }; } },
    { id: "value", name: "Deep Value", desc: "Low P/E, pays a dividend", apply: (st) => { reset(st); st.peMax = 15; st.divMin = 2; st.profitableOnly = true; st.sort = { key: "pe", dir: 1 }; } },
    { id: "momentum", name: "Momentum", desc: "Above MAs, strong YTD", apply: (st) => { reset(st); st.aboveSMA50 = true; st.aboveSMA200 = true; st.rsiNotOverbought = true; st.sort = { key: "perfYTD", dir: -1 }; } },
    { id: "dividend", name: "Dividend", desc: "Yield ≥ 3%", apply: (st) => { reset(st); st.divMin = 3; st.sort = { key: "divYield", dir: -1 }; } },
    { id: "oversold", name: "Oversold Dip", desc: "RSI < 35", apply: (st) => { reset(st); st.rsiOversold = true; st.sort = { key: "rsi14", dir: 1 }; } },
    { id: "mega", name: "Mega Caps", desc: "$200B+ leaders", apply: (st) => { reset(st); st.capMin = 200e9; st.sort = { key: "marketCap", dir: -1 }; } },
  ];

  // =======================================================================
  //  State
  // =======================================================================
  const defaultState = () => ({
    search: "", sector: "All", capMin: 0, priceMin: null, priceMax: null,
    peMax: null, profitableOnly: false, divMin: null, magicMin: 0,
    aboveSMA50: false, aboveSMA200: false, goldenCross: false,
    rsiOversold: false, rsiNotOverbought: false,
    sort: { key: "magicScore", dir: -1 },
  });
  let state = defaultState();
  function reset(st) { Object.assign(st, defaultState()); }

  // =======================================================================
  //  Filtering + sorting
  // =======================================================================
  function applyScreen() {
    const q = state.search.trim().toLowerCase();
    let rows = universe.filter((s) => {
      if (q && !(s.ticker.toLowerCase().includes(q) || s.name.toLowerCase().includes(q) || s.sector.toLowerCase().includes(q))) return false;
      if (state.sector !== "All" && s.sector !== state.sector) return false;
      if (s.marketCap < state.capMin) return false;
      if (state.priceMin != null && s.price < state.priceMin) return false;
      if (state.priceMax != null && s.price > state.priceMax) return false;
      if (state.profitableOnly && !(s.pe > 0)) return false;
      if (state.peMax != null && !(s.pe != null && s.pe <= state.peMax)) return false;
      if (state.divMin != null && s.divYield < state.divMin) return false;
      if (state.magicMin > 0 && s.magicScore < state.magicMin) return false;
      if (state.aboveSMA50 && !(s.price > s.sma50)) return false;
      if (state.aboveSMA200 && !(s.price > s.sma200)) return false;
      if (state.goldenCross && !(s.sma50 > s.sma200)) return false;
      if (state.rsiOversold && !(s.rsi14 < 35)) return false;
      if (state.rsiNotOverbought && !(s.rsi14 < 70)) return false;
      return true;
    });

    const col = COLUMNS.find((c) => c.key === state.sort.key) || COLUMNS[COLUMNS.length - 1];
    const keyFn = col.sortKey || ((s) => s[col.key]);
    const dir = state.sort.dir;
    rows.sort((a, b) => {
      let av = keyFn(a), bv = keyFn(b);
      if (col.sortType === "str") return dir * String(av).localeCompare(String(bv));
      av = av == null ? -Infinity : av;
      bv = bv == null ? -Infinity : bv;
      return dir * (av - bv);
    });
    return rows;
  }

  // =======================================================================
  //  Rendering
  // =======================================================================
  const $ = (id) => document.getElementById(id);

  function renderHeader() {
    $("headerRow").innerHTML = COLUMNS.map((c) => {
      const active = state.sort.key === c.key;
      const arrow = active ? `<span class="arrow">${state.sort.dir < 0 ? "▾" : "▴"}</span>` : "";
      return `<th data-key="${c.key}" class="${c.txt ? "txt" : ""}">${c.label}${arrow}</th>`;
    }).join("");
    $("headerRow").querySelectorAll("th").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (state.sort.key === key) state.sort.dir *= -1;
        else state.sort = { key, dir: key === "ticker" ? 1 : -1 };
        render();
      });
    });
  }

  function render() {
    const rows = applyScreen();
    $("matchCount").textContent = rows.length;
    $("universeCount").textContent = `of ${universe.length} screened`;

    const body = $("resultsBody");
    body.innerHTML = rows.map((s) =>
      "<tr>" + COLUMNS.map((c) => `<td class="${c.txt ? "txt" : ""}">${c.render(s)}</td>`).join("") + "</tr>"
    ).join("");

    $("emptyState").hidden = rows.length !== 0;
    renderHeader();
    $("resultsTable").lastRows = rows; // stash for CSV export
  }

  // =======================================================================
  //  CSV export
  // =======================================================================
  function exportCSV() {
    const rows = $("resultsTable").lastRows || applyScreen();
    const cols = ["ticker", "name", "sector", "price", "perfYTD", "perfMonth", "marketCap", "pe", "divYield", "roic", "earnYield", "rsi14", "magicScore"];
    const head = cols.join(",");
    const lines = rows.map((s) => cols.map((k) => {
      const v = s[k];
      if (v == null) return "";
      return typeof v === "string" && v.includes(",") ? `"${v}"` : v;
    }).join(","));
    const blob = new Blob([head + "\n" + lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "elite-magic-screen.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // =======================================================================
  //  Wiring up controls
  // =======================================================================
  function syncControlsFromState() {
    $("searchInput").value = state.search;
    $("sectorSelect").value = state.sector;
    $("capMin").value = String(state.capMin);
    $("priceMin").value = state.priceMin ?? "";
    $("priceMax").value = state.priceMax ?? "";
    $("peMax").value = state.peMax ?? "";
    $("profitableOnly").checked = state.profitableOnly;
    $("divMin").value = state.divMin ?? "";
    $("magicMin").value = state.magicMin;
    $("magicMinOut").value = state.magicMin;
    $("aboveSMA50").checked = state.aboveSMA50;
    $("aboveSMA200").checked = state.aboveSMA200;
    $("goldenCross").checked = state.goldenCross;
    $("rsiOversold").checked = state.rsiOversold;
    $("rsiNotOverbought").checked = state.rsiNotOverbought;
    markActivePreset();
  }

  const numOrNull = (v) => (v === "" || v == null ? null : Number(v));

  function bindControls() {
    $("searchInput").addEventListener("input", (e) => { state.search = e.target.value; clearPreset(); render(); });
    $("sectorSelect").addEventListener("change", (e) => { state.sector = e.target.value; clearPreset(); render(); });
    $("capMin").addEventListener("change", (e) => { state.capMin = Number(e.target.value); clearPreset(); render(); });
    $("priceMin").addEventListener("input", (e) => { state.priceMin = numOrNull(e.target.value); clearPreset(); render(); });
    $("priceMax").addEventListener("input", (e) => { state.priceMax = numOrNull(e.target.value); clearPreset(); render(); });
    $("peMax").addEventListener("input", (e) => { state.peMax = numOrNull(e.target.value); clearPreset(); render(); });
    $("profitableOnly").addEventListener("change", (e) => { state.profitableOnly = e.target.checked; clearPreset(); render(); });
    $("divMin").addEventListener("input", (e) => { state.divMin = numOrNull(e.target.value); clearPreset(); render(); });
    $("magicMin").addEventListener("input", (e) => { state.magicMin = Number(e.target.value); $("magicMinOut").value = e.target.value; clearPreset(); render(); });
    ["aboveSMA50", "aboveSMA200", "goldenCross", "rsiOversold", "rsiNotOverbought"].forEach((id) =>
      $(id).addEventListener("change", (e) => { state[id] = e.target.checked; clearPreset(); render(); }));

    $("resetBtn").addEventListener("click", () => { state = defaultState(); activePreset = null; syncControlsFromState(); render(); });
    $("exportBtn").addEventListener("click", exportCSV);
    $("liveBtn").addEventListener("click", tryLiveData);
  }

  // ---- presets ui --------------------------------------------------------
  let activePreset = null;
  function clearPreset() { activePreset = null; markActivePreset(); }
  function markActivePreset() {
    document.querySelectorAll(".preset-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.id === activePreset));
  }
  function buildPresets() {
    $("presetGrid").innerHTML = PRESETS.map((p) =>
      `<button class="preset-btn" data-id="${p.id}">${p.name}<small>${p.desc}</small></button>`).join("");
    document.querySelectorAll(".preset-btn").forEach((b) =>
      b.addEventListener("click", () => {
        const p = PRESETS.find((x) => x.id === b.dataset.id);
        p.apply(state);
        activePreset = p.id;
        syncControlsFromState();
        render();
      }));
  }

  function buildSectorOptions() {
    const sectors = ["All", ...Array.from(new Set(universe.map((s) => s.sector))).sort()];
    $("sectorSelect").innerHTML = sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
  }

  // =======================================================================
  //  Optional live data hook
  //
  //  Drop a free Financial Modeling Prep key into FMP_API_KEY below (or set
  //  window.FMP_API_KEY before this script loads) to pull live quotes for the
  //  bundled tickers. Without a key, the screener runs entirely on demo data.
  // =======================================================================
  const FMP_API_KEY = window.FMP_API_KEY || "";

  async function tryLiveData() {
    const pill = $("dataStatus");
    const txt = $("dataStatusText");
    if (!FMP_API_KEY) {
      pill.classList.add("error");
      txt.textContent = "No API key — using demo data";
      setTimeout(() => { pill.classList.remove("error"); txt.textContent = "Demo dataset"; }, 3500);
      return;
    }
    txt.textContent = "Fetching live quotes…";
    try {
      const symbols = universe.map((s) => s.ticker.replace(".", "-")).join(",");
      const res = await fetch(`https://financialmodelingprep.com/api/v3/quote/${symbols}?apikey=${FMP_API_KEY}`);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const quotes = await res.json();
      const byTicker = new Map(quotes.map((q) => [q.symbol.replace("-", "."), q]));
      universe.forEach((s) => {
        const q = byTicker.get(s.ticker);
        if (!q) return;
        s.price = q.price ?? s.price;
        s.marketCap = q.marketCap ?? s.marketCap;
        s.pe = q.pe ?? s.pe;
        s.sma50 = q.priceAvg50 ?? s.sma50;
        s.sma200 = q.priceAvg200 ?? s.sma200;
        s.volume = q.avgVolume ?? s.volume;
        if (q.yearHigh && q.yearLow) {
          // approximate YTD-ish positioning from the 52-week range
          s.perfYTD = q.changesPercentage ?? s.perfYTD;
        }
      });
      computeMagicScores(universe);
      pill.classList.add("live");
      txt.textContent = "Live quotes";
      render();
    } catch (err) {
      pill.classList.add("error");
      txt.textContent = "Live fetch failed — demo data";
      setTimeout(() => { pill.classList.remove("error"); txt.textContent = "Demo dataset"; }, 3500);
    }
  }

  // =======================================================================
  //  Init
  // =======================================================================
  function init() {
    computeMagicScores(universe);
    buildSectorOptions();
    buildPresets();
    bindControls();
    syncControlsFromState();
    render();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
