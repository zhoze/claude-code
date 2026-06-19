#!/usr/bin/env node
"use strict";
/* Elite Magic Trader — command-line runner for magic.js.
 * Usage:
 *   magic <TICKER>                 run on bundled demo data (offline, may be stale)
 *   magic <TICKER> --price 204 --sma50 244.89 --sma200 298.34 --rsi 29.38 \
 *         --beta 1.4 --perf-month -18 --perf-ytd -30 --volume 6857428 --pe 11.67 --roic 59.7
 *   magic <TICKER> --live          fetch fresh data from FMP (needs FMP_API_KEY env)
 *   magic --screen                 score the whole demo universe (table)
 *   magic --list                   list demo tickers
 * Educational risk model — gives NO buy/sell signals. Not investment advice. */
const path = require("path");
const DIR = __dirname;
const { computeMagic, computeInversions, CANDLE_META, DSPR_META } = require(path.join(DIR, "magic.js"));
const { STOCK_UNIVERSE, BOND_YIELDS } = require(path.join(DIR, "data.js"));

const NUM = { price:"price", sma50:"sma50", sma200:"sma200", rsi:"rsi14", beta:"beta",
  "perf-month":"perfMonth", "perf-ytd":"perfYTD", volume:"volume", pe:"pe", roic:"roic",
  "earn-yield":"earnYield", pb:"pb", "div-yield":"divYield", "market-cap":"marketCap" };
const STR = { name:"name", sector:"sector" };

function parseArgs(argv) {
  const o = { _:[], flags:{} };
  for (let i=0;i<argv.length;i++){
    let a=argv[i];
    if (a.startsWith("--")){
      let key=a.slice(2), val=null;
      if (key.includes("=")) [key,val]=key.split(/=(.*)/s);
      if (["screen","live","list","json","help"].includes(key)){ o.flags[key]= val ?? true; continue; }
      if (val===null){ val=argv[i+1]; i++; }
      o.flags[key]=val;
    } else o._.push(a);
  }
  return o;
}

async function fetchLive(ticker){
  const key=process.env.FMP_API_KEY;
  if(!key) throw new Error("--live needs an FMP key: export FMP_API_KEY=... (or pass numbers via flags)");
  const base="https://financialmodelingprep.com/api/v3";
  const j=async u=>{const r=await fetch(u);if(!r.ok)throw new Error("HTTP "+r.status+" "+u);return r.json();};
  const [q]=await j(`${base}/quote/${ticker}?apikey=${key}`);
  if(!q) throw new Error("no quote for "+ticker);
  const [km]=await j(`${base}/key-metrics-ttm/${ticker}?apikey=${key}`).catch(()=>[{}]);
  const chg=await j(`${base}/stock-price-change/${ticker}?apikey=${key}`).catch(()=>[{}]);
  const rsiArr=await j(`${base}/technical_indicator/1day/${ticker}?type=rsi&period=14&apikey=${key}`).catch(()=>[]);
  const c=Array.isArray(chg)?chg[0]||{}:chg;
  return { ticker, name:q.name||ticker, sector:"", price:q.price, sma50:q.priceAvg50,
    sma200:q.priceAvg200, volume:q.volume, pe:q.pe??null, beta: km.betaTTM ?? 1.0,
    rsi14: rsiArr[0]?.rsi ?? 50, perfMonth: c["1M"] ?? c.oneMonth ?? 0, perfYTD: c.ytd ?? 0,
    roic: km.roicTTM!=null? +(km.roicTTM*100).toFixed(2):null,
    earnYield: km.earningsYieldTTM!=null? +(km.earningsYieldTTM*100).toFixed(2): (q.pe? +(100/q.pe).toFixed(2):null) };
}

function fmtRead(r){
  const c=CANDLE_META[r.candle];
  const L=[];
  L.push(`===== ELITE MAGIC TRADER — ${r.ticker}${r.name?` (${r.name})`:""} =====`);
  L.push(`Price $${r.price}  | SMA50 $${r.sma50}  SMA200 $${r.sma200}  | RSI14 ${r.rsi14}  beta ${r.beta}`);
  L.push("");
  L.push("DIRECTIONAL READ");
  L.push(`  Blue Line territory : ${r.territory.toUpperCase()}  (stair-step ${r.stairStep})`);
  L.push(`  Magic Lines gate    : ${r.mlGate===1?"LONG-OK (above both)":r.mlGate===-1?"SHORT-OK (below both)":"NO-TRADE (between)"}  [green $${r.magicGreen} / red $${r.magicRed}]`);
  L.push(`  Magic Trading Zone  : Zone ${r.zone}/6  ${r.zone>=5?"(washed-out / bottom)":r.zone<=2?"(rich / top)":"(mid)"}  | ${r.distMA200}% vs 200-MA`);
  L.push(`  Magic Candle        : ${c.glyph} ${c.label} — ${c.desc}`);
  L.push(`  HTR Ribbon          : ${r.ribbon}${r.ribbonRisk?` ${r.ribbonRisk>0?"+":""}${r.ribbonRisk}`:""}  (elapsed-time risk)`);
  L.push(`  Health              : ${r.healthDir} (black ${r.healthBlack})  | Dir-line ${r.dirLine} | Spike ${r.spike} | PureHealth ${r.pureHealth}`);
  L.push(`  Volatility Combinator: bias ${r.volaBias}  vol ${r.atrPct}% ${r.exVola?"(EXCESSIVE)":r.lowRisk?"(calm)":""}`);
  L.push(`  DSPR preceptor      : Type ${r.dspr} (${r.dsprCode}) — ${DSPR_META[r.dspr].desc}`);
  L.push("");
  L.push(`FIVE INGREDIENTS  Bull ${r.ingBull}/5  Bear ${r.ingBear}/5  vol>500k ${r.volPass}  | Entry trigger: ${r.entryTrigger.toUpperCase()}`);
  L.push(`Weekly trend ${r.weeklyTrend} → Wk→Daily alignment: ${r.mtfAlign.toUpperCase()}`);
  L.push("");
  L.push(`MAGIC SCORE (0 bear · 50 neutral · 100 bull): ${r.magicScore}`);
  L.push(`8D WEIGHTED RISK (higher = more risk): ${r.dimRisk}/100`);
  for(const d of r.dims) L.push(`   ${d.num}. ${d.name.padEnd(30)} ${d.level.toUpperCase().padEnd(9)} ${d.note}`);
  return L.join("\n");
}

function bondBanner(){
  const inv=computeInversions(BOND_YIELDS);
  return `BOND INVERSIONS (demo curve): ${inv.count}/${inv.total} pairs inverted (${inv.pct}%)  — macro risk gauge`;
}

function screenTable(rows){
  const scored=rows.map(computeMagic).sort((a,b)=>b.magicScore-a.magicScore);
  const out=[`${"TICK".padEnd(6)}${"PRICE".padStart(9)}  ${"TERR".padEnd(5)}${"ZN".padStart(3)}  ${"CANDLE".padEnd(10)}${"RIBBON".padEnd(10)}${"B/B".padEnd(5)}${"MAGIC".padStart(6)}${"8D".padStart(5)}  ENTRY`];
  for(const r of scored){
    const c=CANDLE_META[r.candle];
    out.push(`${r.ticker.padEnd(6)}${("$"+r.price).padStart(9)}  ${r.territory.padEnd(5)}${String(r.zone).padStart(3)}  ${(c.glyph+" "+c.label).padEnd(10)}${(r.ribbon+(r.ribbonRisk?` ${r.ribbonRisk>0?"+":""}${r.ribbonRisk}`:"")).padEnd(10)}${(r.ingBull+"/"+r.ingBear).padEnd(5)}${String(r.magicScore).padStart(6)}${String(r.dimRisk).padStart(5)}  ${r.entryTrigger}`);
  }
  return out.join("\n");
}

(async function main(){
  const {_, flags}=parseArgs(process.argv.slice(2));
  if(flags.help){ console.log(require("fs").readFileSync(__filename,"utf8").split("*/")[0].split("/* ")[1]); return; }
  if(flags.list){ console.log(STOCK_UNIVERSE.map(s=>s.ticker).join("  ")); return; }
  if(flags.screen){ console.log(bondBanner()); console.log(""); console.log(screenTable(STOCK_UNIVERSE));
    console.log("\nEducational risk model — no buy/sell signals. Not investment advice."); return; }

  const ticker=(_[0]||"").toUpperCase();
  if(!ticker){ console.error("Usage: magic <TICKER> [--live | --price .. --sma50 .. ...] | magic --screen | magic --list"); process.exit(1); }

  let stock=null, srcNote="";
  if(flags.json){ stock=JSON.parse(require("fs").readFileSync(flags.json,"utf8")); srcNote="(from "+flags.json+")"; }
  else if(flags.live){ stock=await fetchLive(ticker); srcNote="(LIVE via FMP)"; }
  else {
    const demo=STOCK_UNIVERSE.find(s=>s.ticker.toUpperCase()===ticker);
    if(demo){ stock={...demo}; srcNote="(bundled DEMO snapshot — may be stale; pass --live or --price/... for fresh data)"; }
    else stock={ ticker, name:ticker };
  }
  // apply numeric/string overrides
  for(const [k,prop] of Object.entries(NUM)) if(flags[k]!=null) stock[prop]=Number(flags[k]);
  for(const [k,prop] of Object.entries(STR)) if(flags[k]!=null) stock[prop]=String(flags[k]);
  stock.ticker=ticker;
  if(flags["earn-yield"]==null && stock.pe) stock.earnYield=+(100/stock.pe).toFixed(2);

  const required=["price","sma50","sma200","rsi14","beta","perfMonth","perfYTD","volume","pe"];
  const missing=required.filter(k=>stock[k]==null||Number.isNaN(stock[k]));
  if(missing.length){ console.error(`Missing data for ${ticker}: ${missing.join(", ")}\nPass them as flags, e.g. --price 204.02 --sma50 244.89 ... (or --live with FMP_API_KEY).`); process.exit(1); }

  console.log(fmtRead(computeMagic(stock)));
  if(srcNote) console.log("\n"+srcNote);
  console.log("Educational risk model — no buy/sell signals. Not investment advice.");
})().catch(e=>{ console.error("Error:",e.message); process.exit(1); });
