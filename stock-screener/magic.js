/* =========================================================================
 * Magic Trader® Elite — signal engine
 *
 * A faithful *interpretation* of the indicators described in the MetaStock
 * "Magic Trader® Elite" add-on manual (Save Dollar Enterprises, 2019),
 * reimplemented over the fields available in this demo dataset.
 *
 * IMPORTANT: MetaStock's actual Magic Trader formulas are proprietary and not
 * published. This engine reproduces the manual's *concepts, color scheme, and
 * decision rules* from price, moving averages, RSI, momentum, volume and beta.
 * It is a teaching model, not a replica of the closed-source indicators.
 *
 * Concepts implemented (see manual chapters):
 *   - Magic Blue Line .... higher-timeframe territory line (bull/bear)
 *   - Magic Lines ........ green/red "line in the sand"; the confirmation gate
 *   - Magic Trading Zone . 7 adaptive lines -> 6 dynamic zones
 *   - Health Warning ..... green vs red dominance + black-line momentum
 *   - Volatility Combinator (VLL1 histogram vs VLL2 blue line)
 *   - Directional Lines .. green/red concentration of believers
 *   - Entry / Price Spikes
 *   - Magic Believer Pure Health
 *   - HTR Ribbon ......... green / red / black + elapsed-time risk number
 *   - Magic Candle color . Yellow/Indigo/Turquoise/Golden/Neutral/Green/Red
 *   - DSPR Price Preceptors (Types a..f -> codes 1..6)
 *   - Five Ingredients alignment + entry trigger
 *   - Bond Inversions .... yield-curve macro risk
 * ========================================================================= */

(function (root) {
  "use strict";

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const round = (v, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

  // Deterministic 0..1 value from a string — used only to synthesize the
  // volatility / relative-volume fields the demo dataset doesn't carry.
  function hashUnit(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 100000) / 100000;
  }

  // -- master color -> meaning map (from the manual's color scheme) ----------
  const CANDLE_META = {
    turquoise: { label: "Turquoise", glyph: "▲", side: "bull", desc: "Sudden bullish reversal — institutions take the long side" },
    green:     { label: "Bull",      glyph: "▲", side: "bull", desc: "Confirmed bullish (Close above both Magic Lines)" },
    yellow:    { label: "Yellow",    glyph: "△", side: "bull", desc: "Bull warning / pre-entry — wait for Magic Lines confirmation" },
    neutral:   { label: "Neutral",   glyph: "◇", side: "neutral", desc: "Equilibrium — believers exhausted, trend may flip or resume" },
    indigo:    { label: "Indigo",    glyph: "▽", side: "bear", desc: "Bear warning / pre-entry — wait for Magic Lines confirmation" },
    golden:    { label: "Golden",    glyph: "▼", side: "bear", desc: "Sudden bearish reversal — institutions take the short side" },
    red:       { label: "Bear",      glyph: "▼", side: "bear", desc: "Confirmed bearish (Close below both Magic Lines)" },
  };

  const DSPR_META = {
    a: { code: 1, side: "bull", desc: "Type a — bullish believers, prices dropping (shakeout / pullback)" },
    b: { code: 2, side: "bull", desc: "Type b — bullish believers, prices rising (trend fully charged)" },
    c: { code: 3, side: "bull", desc: "Type c — bullish believers, prices rising (public piling in / extended)" },
    d: { code: 4, side: "bull", desc: "Type d — bullish believers, prices dropping after a rise (profit-taking near top)" },
    e: { code: 5, side: "bear", desc: "Type e — bearish believers, prices dropping (downtrend in control)" },
    f: { code: 6, side: "bear", desc: "Type f — bearish believers, prices rising (counter-bounce / cover)" },
  };

  // =======================================================================
  //  Per-stock signal computation
  // =======================================================================
  function computeMagic(s) {
    // --- synthesized demo fields (documented as illustrative) ------------
    const h1 = hashUnit(s.ticker);
    const h2 = hashUnit(s.ticker + "vol");
    const atrPct = round(1.0 + s.beta * 1.2 + h1 * 1.6, 2);        // Pure Volatility %
    const relVol = round(0.7 + h2 * 1.8 + (Math.abs(s.perfMonth) > 5 ? 0.6 : 0), 2); // vs 20d avg

    // --- geometry vs moving averages -------------------------------------
    const distMA50 = ((s.price - s.sma50) / s.sma50) * 100;
    const distMA200 = ((s.price - s.sma200) / s.sma200) * 100;
    const golden = s.sma50 > s.sma200;
    const aboveBoth = s.price > s.sma50 && s.price > s.sma200;
    const belowBoth = s.price < s.sma50 && s.price < s.sma200;

    // --- 1. Magic Blue Line (territory) ----------------------------------
    const territory = s.price >= s.sma200 ? "bull" : "bear";
    const stairStep = golden && territory === "bull" ? "up"
      : !golden && territory === "bear" ? "down" : "mixed";

    // --- 2. Magic Lines (green = higher line, red = lower) ----------------
    const magicGreen = Math.max(s.sma50, s.sma200);
    const magicRed = Math.min(s.sma50, s.sma200);
    const mlGate = aboveBoth ? 1 : belowBoth ? -1 : 0; // confirmation gate

    // --- 3. Magic Trading Zone (7 lines -> 6 zones) ----------------------
    const step = (atrPct / 100) * s.price * 1.5;
    const zlines = [3, 2, 1, 0, -1, -2, -3].map((k) => round(s.sma200 + k * step, 2)); // MZL-1..7
    let zone = 6;
    for (let i = 0; i < zlines.length - 1; i++) {
      if (s.price >= zlines[i + 1]) { zone = i + 1; break; }
    }
    if (s.price >= zlines[0]) zone = 1; // above the top line

    // --- 4. Health Warning Indicator -------------------------------------
    const healthBlack = clamp(distMA50 * 0.15 + s.perfMonth * 0.4 + (golden ? 1.5 : -1.5), -8, 8);
    const healthGreen = clamp(2 + s.perfMonth * 0.2 + (aboveBoth ? 1.5 : 0), -5, 5);
    const healthRed = clamp(2 - s.perfMonth * 0.2 + (belowBoth ? 1.5 : 0), -5, 5);
    const healthDir = healthGreen >= healthRed ? "bull" : "bear";

    // --- 5. Volatility Combinator (VLL1 hist vs VLL2 blue line) -----------
    const vll2 = atrPct;
    const vll1 = round(atrPct * (1 + (s.perfMonth / 100) * 3), 2);
    const volaBias = vll1 >= vll2 ? "call" : "put"; // call bias = bullish
    const exVola = atrPct > 4.2;
    const lowRisk = atrPct < 2.6 && Math.abs(healthBlack) < 2;

    // --- 6. Directional Line ---------------------------------------------
    const dirLine = s.price > s.sma50 && s.perfMonth >= 0 ? "green"
      : s.price < s.sma50 && s.perfMonth < 0 ? "red" : "none";

    // --- 7. Entry / Price Spike ------------------------------------------
    const spike = s.perfMonth >= 4 && aboveBoth ? "green"
      : s.perfMonth <= -4 && belowBoth ? "red" : "none";

    // --- 8. Pure Health --------------------------------------------------
    const pureHealth = aboveBoth && s.perfYTD > 0 && s.rsi14 >= 45 && s.rsi14 <= 72 ? 1
      : belowBoth && s.perfYTD < 0 && s.rsi14 <= 55 ? -1 : 0;

    // --- 9. HTR Ribbon + elapsed-time risk number ------------------------
    let ribbon = "black";
    if (healthDir === "bull" && aboveBoth) ribbon = "green";
    else if (healthDir === "bear" && belowBoth) ribbon = "red";
    let ribbonRisk = 0; // green: +1..+7 (3 in green circle); red: -1..-7 (4 in red circle)
    if (ribbon === "green") ribbonRisk = clamp(Math.round(1 + distMA200 / 3 + Math.max(0, (s.rsi14 - 50) / 6)), 1, 7);
    else if (ribbon === "red") ribbonRisk = -clamp(Math.round(1 + -distMA200 / 3 + Math.max(0, (50 - s.rsi14) / 6)), 1, 7);

    // --- 10. Magic Candle color ------------------------------------------
    // Reversal/warning candles take precedence at extremes (they are the
    // manual's pre-entry signals at tops and bottoms); confirmed bull/bear
    // candles apply once price is cleanly inside Magic Lines territory.
    let candle;
    if (aboveBoth && s.rsi14 >= 68 && s.perfMonth < 2) candle = "golden";      // overbought uptrend stalling -> sudden bear-reversal risk
    else if (belowBoth && s.rsi14 <= 36) candle = "turquoise";                 // washed-out downtrend -> sudden bull-reversal setup
    else if (aboveBoth && ribbon === "green") candle = "green";                // confirmed bull
    else if (belowBoth && ribbon === "red") candle = "red";                    // confirmed bear
    else if (Math.abs(healthBlack) < 1.4 && !aboveBoth && !belowBoth) candle = "neutral"; // equilibrium between the lines
    else if (healthDir === "bull" && !aboveBoth) candle = "yellow";            // bull pre-entry warning
    else if (healthDir === "bear" && !belowBoth) candle = "indigo";            // bear pre-entry warning
    else candle = "neutral";

    // --- 11. DSPR Price Preceptor ----------------------------------------
    let dspr;
    if (territory === "bull") {
      if (s.perfMonth >= 0) dspr = s.rsi14 >= 70 ? "c" : "b";
      else dspr = s.rsi14 >= 60 ? "d" : "a";
    } else {
      dspr = s.perfMonth < 0 ? "e" : "f";
    }

    // --- 12. Five Ingredients alignment ----------------------------------
    const ingBullArr = [dirLine === "green", healthDir === "bull", spike === "green", pureHealth === 1, ribbon === "green"];
    const ingBearArr = [dirLine === "red", healthDir === "bear", spike === "red", pureHealth === -1, ribbon === "red"];
    const ingBull = ingBullArr.filter(Boolean).length;
    const ingBear = ingBearArr.filter(Boolean).length;
    const volQual = relVol >= 1.5;          // VQua — unusually high volume
    const volPass = s.volume > 500_000;     // base volume filter from the explorations
    const bullCandle = ["yellow", "turquoise", "green", "neutral"].includes(candle);
    const bearCandle = ["indigo", "golden", "red", "neutral"].includes(candle);
    const entryTrigger = ingBull === 5 && volPass && bullCandle ? "bull"
      : ingBear === 5 && volPass && bearCandle ? "bear" : "none";

    // --- Magic Bull Score: 0..100 directional conviction (50 = neutral) ---
    const magicScore = clamp(
      Math.round(50 + (ingBull - ingBear) * 8 + healthBlack * 2 + (territory === "bull" ? 4 : -4)),
      0, 100
    );

    return {
      ...s,
      atrPct, relVol, distMA50: round(distMA50), distMA200: round(distMA200),
      golden, aboveBoth, belowBoth,
      territory, stairStep,
      magicGreen: round(magicGreen, 2), magicRed: round(magicRed, 2), mlGate,
      zone, zlines,
      healthBlack: round(healthBlack), healthGreen: round(healthGreen), healthRed: round(healthRed), healthDir,
      vll1, vll2, volaBias, exVola, lowRisk,
      dirLine, spike, pureHealth,
      ribbon, ribbonRisk,
      candle,
      dspr, dsprCode: DSPR_META[dspr].code,
      ingBull, ingBear, volQual, volPass, entryTrigger,
      magicScore,
    };
  }

  // =======================================================================
  //  Bond Inversions — yield-curve macro risk
  // =======================================================================
  // Pairs are (shorter maturity, longer maturity); inverted when the longer
  // yield is below the shorter yield, per the manual's inversion definition.
  const BOND_INVERSION_PAIRS = [
    ["3M", "1Y"], ["6M", "1Y"], ["6M", "2Y"], ["6M", "3Y"], ["6M", "5Y"],
    ["1Y", "3Y"], ["1Y", "5Y"], ["1Y", "7Y"], ["1Y", "10Y"],
    ["2Y", "5Y"], ["2Y", "10Y"], ["2Y", "30Y"],
    ["3Y", "5Y"], ["5Y", "10Y"], ["10Y", "30Y"],
  ];

  function computeInversions(yields) {
    const byKey = new Map(yields.map((y) => [y.maturity, y.yield]));
    const active = BOND_INVERSION_PAIRS.filter(([sh, lo]) => {
      const a = byKey.get(sh), b = byKey.get(lo);
      return a != null && b != null && b < a; // longer yield below shorter -> inverted
    });
    return {
      pairs: BOND_INVERSION_PAIRS,
      active,
      count: active.length,
      total: BOND_INVERSION_PAIRS.length,
      pct: round((active.length / BOND_INVERSION_PAIRS.length) * 100, 1),
    };
  }

  const MagicEngine = { computeMagic, computeInversions, CANDLE_META, DSPR_META, BOND_INVERSION_PAIRS };

  if (typeof module !== "undefined" && module.exports) module.exports = MagicEngine;
  else root.MagicEngine = MagicEngine;
})(typeof window !== "undefined" ? window : this);
