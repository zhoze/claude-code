/* =========================================================================
 * Elite Magic Trader — screener UI
 *
 * Pipeline:  universe -> MagicEngine.computeMagic -> filters -> sort -> render
 *
 * Signals, colors and Explorations follow the Magic Trader® Elite manual
 * (see magic.js for the interpretation of each indicator).
 * ========================================================================= */

(function () {
  "use strict";

  let universe = STOCK_UNIVERSE.map((s) => MagicEngine.computeMagic(s));

  // ---- formatting helpers ------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const fmtMoney = (n) => (n == null ? "—" : "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const fmtCap = (n) => {
    if (n == null) return "—";
    if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
    if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
    return "$" + n.toLocaleString();
  };
  const fmtPct = (n, d = 1) => (n == null ? "—" : (n > 0 ? "+" : "") + n.toFixed(d) + "%");
  const fmtVol = (n) => (n == null ? "—" : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : (n / 1e3).toFixed(0) + "K");
  const signed = (n) => (n == null ? "" : n > 0 ? "pos" : n < 0 ? "neg" : "");

  const CM = MagicEngine.CANDLE_META;

  function candleChip(s) {
    const m = CM[s.candle];
    return `<span class="chip c-${s.candle}" title="${m.desc}">${m.glyph} ${m.label}</span>`;
  }
  function ribbonChip(s) {
    const risk = s.ribbonRisk ? ` ${s.ribbonRisk > 0 ? "+" : ""}${s.ribbonRisk}` : "";
    const label = s.ribbon === "green" ? "Bull" : s.ribbon === "red" ? "Bear" : "Neutral";
    return `<span class="chip r-${s.ribbon}" title="Horizontal Time Risk — ${label}; risk number grows with time since the ribbon flipped">${label}${risk}</span>`;
  }
  function ingCell(s) {
    const bull = s.ingBull, bear = s.ingBear;
    const dom = bull >= bear ? "bull" : "bear";
    const n = Math.max(bull, bear);
    const trig = s.entryTrigger !== "none" ? `<span class="trigger ${s.entryTrigger}" title="Entry Trigger fired">⚡</span>` : "";
    return `<span class="ing ${dom}">${n}/5${trig}</span>`;
  }
  function mlGateCell(s) {
    if (s.mlGate === 1) return `<span class="pos" title="Close above both Magic Lines — long confirmed">▲ above</span>`;
    if (s.mlGate === -1) return `<span class="neg" title="Close below both Magic Lines — short confirmed">▼ below</span>`;
    return `<span class="muted" title="Between the Magic Lines — no confirmation">— between</span>`;
  }

  // =======================================================================
  //  Columns
  // =======================================================================
  const COLUMNS = [
    { key: "ticker", label: "Ticker", txt: true, sortType: "str",
      render: (s) => `<span class="ticker">${s.ticker}</span><div class="coname">${s.name}</div><span class="sector-tag">${s.sector}</span>` },
    { key: "price", label: "Price", sortType: "num", render: (s) => fmtMoney(s.price) },
    { key: "perfMonth", label: "1M", sortType: "num", render: (s) => `<span class="${signed(s.perfMonth)}">${fmtPct(s.perfMonth)}</span>` },
    { key: "territory", label: "Territory", sortType: "str", sortKey: (s) => s.territory,
      render: (s) => `<span class="chip t-${s.territory}" title="Blue Line territory">${s.territory === "bull" ? "Bull" : "Bear"}</span>` },
    { key: "mlGate", label: "Magic Lines", sortType: "num", sortKey: (s) => s.mlGate, render: mlGateCell },
    { key: "zone", label: "Zone", sortType: "num", render: (s) => `<span class="zone-pill z${s.zone}">${s.zone}</span>` },
    { key: "candle", label: "Candle", sortType: "num", sortKey: (s) => candleSortVal(s), render: candleChip },
    { key: "dsprCode", label: "DSPR", sortType: "num",
      render: (s) => `<span class="dspr ${MagicEngine.DSPR_META[s.dspr].side}" title="${MagicEngine.DSPR_META[s.dspr].desc}">${s.dspr} (${s.dsprCode})</span>` },
    { key: "atrPct", label: "Vola", sortType: "num",
      render: (s) => `<span class="${s.exVola ? "neg" : s.lowRisk ? "pos" : ""}" title="${s.exVola ? "Excessive volatility" : s.lowRisk ? "Lowest-risk volatility" : "Pure Volatility %"}">${s.atrPct}%${s.exVola ? " ⚠" : ""}</span>` },
    { key: "ribbon", label: "Ribbon", sortType: "num", sortKey: (s) => ribbonSortVal(s), render: ribbonChip },
    { key: "ing", label: "5 Ing.", sortType: "num", sortKey: (s) => (s.ingBull - s.ingBear), render: ingCell },
    { key: "magicScore", label: "Magic", sortType: "num",
      render: (s) => `<span class="magic-cell"><span class="magic-bar"><span class="magic-fill ${s.magicScore >= 50 ? "bull" : "bear"}" style="width:${s.magicScore}%"></span></span><span class="magic-val ${s.magicScore >= 50 ? "pos" : "neg"}">${s.magicScore}</span></span>` },
  ];

  // sort helpers so colored chips order sensibly (bull -> bear)
  const candleSortVal = (s) => ({ turquoise: 6, green: 5, yellow: 4, neutral: 3, indigo: 2, golden: 1, red: 0 })[s.candle];
  const ribbonSortVal = (s) => (s.ribbon === "green" ? 2 + s.ribbonRisk / 10 : s.ribbon === "black" ? 1 : 0 + s.ribbonRisk / 10);

  // =======================================================================
  //  Explorations (presets) — mirror the manual's scan list
  // =======================================================================
  const EXPLORATIONS = [
    { id: "fiveBull", name: "5 Ingredients — Bull", desc: "Full bullish alignment", apply: (st) => { reset(st); st.ingSide = "bull"; st.ingMin = 4; st.volFilter = true; st.sort = { key: "ing", dir: -1 }; } },
    { id: "fiveBear", name: "5 Ingredients — Bear", desc: "Full bearish alignment", apply: (st) => { reset(st); st.ingSide = "bear"; st.ingMin = 4; st.volFilter = true; st.sort = { key: "ing", dir: 1 }; } },
    { id: "entry", name: "Entry Triggers", desc: "5/5 + volume + candle", apply: (st) => { reset(st); st.entryOnly = true; st.sort = { key: "magicScore", dir: -1 }; } },
    { id: "magicLong", name: "Magic Lines Long", desc: "Above both, bull ribbon", apply: (st) => { reset(st); st.mlGate = "1"; st.ribbon = "green"; st.sort = { key: "magicScore", dir: -1 }; } },
    { id: "turquoise", name: "Turquoise Reversal", desc: "Sudden bull reversal", apply: (st) => { reset(st); st.candle = "turquoise"; st.sort = { key: "perfMonth", dir: 1 }; } },
    { id: "yellow", name: "Yellow Warnings", desc: "Bull pre-entry setups", apply: (st) => { reset(st); st.candle = "yellow"; st.sort = { key: "magicScore", dir: -1 }; } },
    { id: "golden", name: "Golden Warnings", desc: "Sudden bear reversal", apply: (st) => { reset(st); st.candle = "golden"; st.sort = { key: "magicScore", dir: 1 }; } },
    { id: "volSurge", name: "Volume Surge", desc: "Unusually high volume", apply: (st) => { reset(st); st.volQual = true; st.sort = { key: "relVol", dir: -1 }; } },
    { id: "lowRisk", name: "Lowest Risk", desc: "Calm volatility, neutral health", apply: (st) => { reset(st); st.lowRiskOnly = true; st.sort = { key: "atrPct", dir: 1 }; } },
    { id: "bottomZone", name: "Deep Zones 5–6", desc: "Washed-out vs. trend", apply: (st) => { reset(st); st.zoneMin = 5; st.zoneMax = 6; st.sort = { key: "magicScore", dir: 1 }; } },
  ];

  // =======================================================================
  //  State
  // =======================================================================
  const defaultState = () => ({
    search: "", sector: "All", priceBand: "any", capMin: 0,
    volFilter: true, volQual: false,
    territory: "any", mlGate: "any", candle: "any", ribbon: "any", dspr: "any",
    zoneMin: null, zoneMax: null,
    ingMin: 0, ingSide: "bull", entryOnly: false, lowRiskOnly: false,
    sort: { key: "magicScore", dir: -1 },
  });
  let state = defaultState();
  function reset(st) { Object.assign(st, defaultState()); }

  const PRICE_BANDS = {
    any: () => true,
    u5: (p) => p < 5,
    "5-15": (p) => p >= 5 && p < 15,
    "15-100": (p) => p >= 15 && p < 100,
    "100-200": (p) => p >= 100 && p < 200,
    "200-500": (p) => p >= 200 && p < 500,
    o500: (p) => p >= 500,
  };

  // =======================================================================
  //  Filtering + sorting
  // =======================================================================
  function applyScreen() {
    const q = state.search.trim().toLowerCase();
    let rows = universe.filter((s) => {
      if (q && !(s.ticker.toLowerCase().includes(q) || s.name.toLowerCase().includes(q) || s.sector.toLowerCase().includes(q))) return false;
      if (state.sector !== "All" && s.sector !== state.sector) return false;
      if (!PRICE_BANDS[state.priceBand](s.price)) return false;
      if (s.marketCap < state.capMin) return false;
      if (state.volFilter && !s.volPass) return false;
      if (state.volQual && !s.volQual) return false;
      if (state.territory !== "any" && s.territory !== state.territory) return false;
      if (state.mlGate !== "any" && String(s.mlGate) !== state.mlGate) return false;
      if (state.candle !== "any" && s.candle !== state.candle) return false;
      if (state.ribbon !== "any" && s.ribbon !== state.ribbon) return false;
      if (state.dspr !== "any" && s.dspr !== state.dspr) return false;
      if (state.zoneMin != null && s.zone < state.zoneMin) return false;
      if (state.zoneMax != null && s.zone > state.zoneMax) return false;
      if (state.ingMin > 0) {
        const v = state.ingSide === "bull" ? s.ingBull : s.ingBear;
        if (v < state.ingMin) return false;
      }
      if (state.entryOnly && s.entryTrigger === "none") return false;
      if (state.lowRiskOnly && !s.lowRisk) return false;
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
  function renderHeader() {
    $("headerRow").innerHTML = COLUMNS.map((c) => {
      const active = state.sort.key === c.key;
      const arrow = active ? `<span class="arrow">${state.sort.dir < 0 ? "▾" : "▴"}</span>` : "";
      return `<th data-key="${c.key}" class="${c.txt ? "txt" : ""}">${c.label}${arrow}</th>`;
    }).join("");
    $("headerRow").querySelectorAll("th").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        if (state.sort.key === key) state.sort.dir *= -1;
        else state.sort = { key, dir: key === "ticker" ? 1 : -1 };
        render();
      }));
  }

  function render() {
    const rows = applyScreen();
    $("matchCount").textContent = rows.length;
    $("universeCount").textContent = `of ${universe.length} screened`;
    $("resultsBody").innerHTML = rows.map((s) =>
      "<tr>" + COLUMNS.map((c) => `<td class="${c.txt ? "txt" : ""}">${c.render(s)}</td>`).join("") + "</tr>"
    ).join("");
    $("emptyState").hidden = rows.length !== 0;
    renderHeader();
    $("resultsTable").lastRows = rows;
  }

  // =======================================================================
  //  Bond inversion banner
  // =======================================================================
  function renderBonds() {
    const inv = MagicEngine.computeInversions(BOND_YIELDS);
    const invMaturities = new Set(inv.active.flat());
    $("bondCurve").innerHTML = BOND_YIELDS.map((y) =>
      `<span class="yield-chip ${invMaturities.has(y.maturity) ? "inv" : ""}" title="${y.symbol}">${y.maturity}<b>${y.yield.toFixed(2)}%</b></span>`
    ).join("");
    $("bondGauge").textContent = `${inv.count}/${inv.total} inverted · ${inv.pct}%`;
    const banner = $("bondBanner");
    banner.classList.toggle("warn", inv.pct >= 30);
    banner.classList.toggle("danger", inv.pct >= 60);
    $("bondNote").textContent = inv.pct >= 60 ? "Deeply inverted — elevated macro risk"
      : inv.pct >= 30 ? "Partially inverted — watch the short end"
      : inv.pct > 0 ? "Mild inversion" : "Curve normal";
  }

  // =======================================================================
  //  Color legend
  // =======================================================================
  function renderLegend() {
    const items = [
      ["c-turquoise", "Turquoise — sudden bull reversal"],
      ["c-green", "Bull (confirmed)"],
      ["c-yellow", "Yellow — bull warning"],
      ["c-neutral", "Neutral"],
      ["c-indigo", "Indigo — bear warning"],
      ["c-golden", "Golden — sudden bear reversal"],
      ["c-red", "Bear (confirmed)"],
    ];
    $("legendColors").innerHTML = items.map(([cls, label]) =>
      `<span class="legend-item"><span class="swatch ${cls}"></span>${label}</span>`).join("");
  }

  // =======================================================================
  //  CSV export
  // =======================================================================
  function exportCSV() {
    const rows = $("resultsTable").lastRows || applyScreen();
    const cols = ["ticker", "name", "sector", "price", "perfMonth", "territory", "mlGate", "zone", "candle", "dspr", "dsprCode", "atrPct", "ribbon", "ribbonRisk", "ingBull", "ingBear", "entryTrigger", "magicScore"];
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
  //  Controls
  // =======================================================================
  function syncControlsFromState() {
    $("searchInput").value = state.search;
    $("sectorSelect").value = state.sector;
    $("priceBand").value = state.priceBand;
    $("capMin").value = String(state.capMin);
    $("volFilter").checked = state.volFilter;
    $("volQual").checked = state.volQual;
    $("territory").value = state.territory;
    $("mlGate").value = state.mlGate;
    $("candle").value = state.candle;
    $("ribbon").value = state.ribbon;
    $("dspr").value = state.dspr;
    $("zoneMin").value = state.zoneMin ?? "";
    $("zoneMax").value = state.zoneMax ?? "";
    $("ingMin").value = state.ingMin;
    $("ingMinOut").value = state.ingMin + " / 5";
    $("ingSide").value = state.ingSide;
    $("entryOnly").checked = state.entryOnly;
    $("lowRiskOnly").checked = state.lowRiskOnly;
    markActiveExploration();
  }

  const numOrNull = (v) => (v === "" || v == null ? null : Number(v));

  function bindControls() {
    $("searchInput").addEventListener("input", (e) => { state.search = e.target.value; clearExploration(); render(); });
    $("sectorSelect").addEventListener("change", (e) => { state.sector = e.target.value; clearExploration(); render(); });
    $("priceBand").addEventListener("change", (e) => { state.priceBand = e.target.value; clearExploration(); render(); });
    $("capMin").addEventListener("change", (e) => { state.capMin = Number(e.target.value); clearExploration(); render(); });
    $("volFilter").addEventListener("change", (e) => { state.volFilter = e.target.checked; clearExploration(); render(); });
    $("volQual").addEventListener("change", (e) => { state.volQual = e.target.checked; clearExploration(); render(); });
    $("territory").addEventListener("change", (e) => { state.territory = e.target.value; clearExploration(); render(); });
    $("mlGate").addEventListener("change", (e) => { state.mlGate = e.target.value; clearExploration(); render(); });
    $("candle").addEventListener("change", (e) => { state.candle = e.target.value; clearExploration(); render(); });
    $("ribbon").addEventListener("change", (e) => { state.ribbon = e.target.value; clearExploration(); render(); });
    $("dspr").addEventListener("change", (e) => { state.dspr = e.target.value; clearExploration(); render(); });
    $("zoneMin").addEventListener("input", (e) => { state.zoneMin = numOrNull(e.target.value); clearExploration(); render(); });
    $("zoneMax").addEventListener("input", (e) => { state.zoneMax = numOrNull(e.target.value); clearExploration(); render(); });
    $("ingMin").addEventListener("input", (e) => { state.ingMin = Number(e.target.value); $("ingMinOut").value = e.target.value + " / 5"; clearExploration(); render(); });
    $("ingSide").addEventListener("change", (e) => { state.ingSide = e.target.value; clearExploration(); render(); });
    $("entryOnly").addEventListener("change", (e) => { state.entryOnly = e.target.checked; clearExploration(); render(); });
    $("lowRiskOnly").addEventListener("change", (e) => { state.lowRiskOnly = e.target.checked; clearExploration(); render(); });

    $("resetBtn").addEventListener("click", () => { state = defaultState(); activeExploration = null; syncControlsFromState(); render(); });
    $("exportBtn").addEventListener("click", exportCSV);
    $("liveBtn").addEventListener("click", tryLiveData);
  }

  // ---- explorations ui ---------------------------------------------------
  let activeExploration = null;
  function clearExploration() { activeExploration = null; markActiveExploration(); }
  function markActiveExploration() {
    document.querySelectorAll(".preset-btn").forEach((b) => b.classList.toggle("active", b.dataset.id === activeExploration));
    const e = EXPLORATIONS.find((x) => x.id === activeExploration);
    $("activeExploration").textContent = e ? "▸ " + e.name : "";
  }
  function buildExplorations() {
    $("presetGrid").innerHTML = EXPLORATIONS.map((p) =>
      `<button class="preset-btn" data-id="${p.id}">${p.name}<small>${p.desc}</small></button>`).join("");
    document.querySelectorAll(".preset-btn").forEach((b) =>
      b.addEventListener("click", () => {
        EXPLORATIONS.find((x) => x.id === b.dataset.id).apply(state);
        activeExploration = b.dataset.id;
        syncControlsFromState();
        render();
      }));
  }

  function buildSectorOptions() {
    const sectors = ["All", ...Array.from(new Set(universe.map((s) => s.sector))).sort()];
    $("sectorSelect").innerHTML = sectors.map((s) => `<option value="${s}">${s}</option>`).join("");
  }

  // =======================================================================
  //  Optional live data hook (Financial Modeling Prep)
  // =======================================================================
  const FMP_API_KEY = window.FMP_API_KEY || "";
  async function tryLiveData() {
    const pill = $("dataStatus"), txt = $("dataStatusText");
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
      universe = STOCK_UNIVERSE.map((base) => {
        const q = byTicker.get(base.ticker);
        const merged = { ...base };
        if (q) {
          merged.price = q.price ?? base.price;
          merged.marketCap = q.marketCap ?? base.marketCap;
          merged.pe = q.pe ?? base.pe;
          merged.sma50 = q.priceAvg50 ?? base.sma50;
          merged.sma200 = q.priceAvg200 ?? base.sma200;
          merged.volume = q.avgVolume ?? base.volume;
          merged.perfMonth = q.changesPercentage ?? base.perfMonth;
        }
        return MagicEngine.computeMagic(merged);
      });
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
    buildSectorOptions();
    buildExplorations();
    bindControls();
    renderBonds();
    renderLegend();
    syncControlsFromState();
    render();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
