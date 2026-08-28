#!/usr/bin/env python3
"""Scan all ~507 stocks: how well does the screen CATCH the best performers,
and can an amendment catch more without breaking what works?

Phase 1 (diagnosis): 'golden stock-days' = (sym,date) in the held-out period
whose forward-60d return is in the top 2% of the whole panel. For each, was the
stock (a) selected, (b) eligible but ranked below the cut, (c) ineligible (no
golden cross)? Plus the top-25 buy-and-hold stocks of the period and the
screen's engagement with each.

Phase 2 (amendment, PRE-REGISTERED before results are seen):
  C1 early-entry eligibility: golden cross OR (close>SMA200 AND SMA50 rising
     over 20d); gc leg generalized to spread + freshness-if-crossed
  C2 loose eligibility: close>SMA200 only (same generalized gc leg)
  C3 current eligibility + idio momentum 63d as a 4th leg (faster momentum)
Gates identical to previous experiments: IS 2020-23 paired diff>0, NW t>=1.5 at
30d AND 60d vs the CURRENT screen; survivors (max 2) get ONE OOS shot needing
paired t>=2 at 60d, positive at 30d, survivorship-flat, no negative year.
"""
import math, os, statistics, sys
from collections import defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SP)
sys.path.insert(0, "/home/user/claude-code/value-screener")
import pandas as pd
import imom_screen as I
from amend_blend import load_outcomes, nw_t
from finetune import monthly, raw_stats, sharpe

DATA = os.path.join(SP, "data500")
ETF = {"SPY"}
OOS_LO = "2024-01-01"

def sma_arr(v, p):
    n=len(v); out=[None]*n; run=0.0
    for i,x in enumerate(v):
        run+=x
        if i>=p: run-=v[i-p]
        if i>=p-1: out[i]=run/p
    return out

def build_all():
    bench = {b[0]: b[4] for b in I.load_bars(os.path.join(DATA, "SPY.csv"))}
    cols = defaultdict(dict)   # leg -> {(sym): {date: v}}
    for fn in sorted(os.listdir(DATA)):
        sym = fn[:-4]
        if not fn.endswith(".csv") or sym in ETF: continue
        bars = I.load_bars(os.path.join(DATA, fn))
        if len(bars) < I.WARMUP + 5: continue
        dates=[b[0] for b in bars]; closes=[b[4] for b in bars]
        resid, beta = I.residual_returns(dates, closes, bench)
        n=len(bars)
        cres=[0.0]*(n+1)
        for i in range(n): cres[i+1]=cres[i]+resid[i]
        s50=sma_arr(closes,50); s200=sma_arr(closes,200)
        # golden-cross state + days since cross (matching I._golden_cross)
        last_cross=None; prev_bull=None
        for i in range(I.WARMUP, n):
            if beta[i] is None or i < I.LOOKBACK_LONG or s200[i] is None or s50[i] is None:
                continue
            d=dates[i]
            bull = s50[i] > s200[i]
            # recompute bull state history cheaply: track as we go
            cols["imom"][sym,d]=cres[i+1-21]-cres[i+1-252]
            cols["imom63"][sym,d]=cres[i+1]-cres[i+1-63]
            cols["ma"][sym,d]=closes[i]/s200[i]-1.0
            cols["spread"][sym,d]=s50[i]/s200[i]-1.0
            cols["above200"][sym,d]=1.0 if closes[i]>s200[i] else 0.0
            slope_ok = s50[i-20] is not None and s50[i] > s50[i-20]
            cols["s50rising"][sym,d]=1.0 if slope_ok else 0.0
        # second pass for cross freshness (needs sequential state over ALL i)
        prev_bull=None; last_cross=None
        for i in range(n):
            if s50[i] is None or s200[i] is None:
                prev_bull=None; continue
            bull=s50[i]>s200[i]
            if bull and prev_bull is False: last_cross=i
            prev_bull=bull
            d=dates[i]
            if (sym,d) in cols["ma"]:
                cols["crossed"][sym,d]=1.0 if bull else 0.0
                fresh = 1.0/(1.0+(i-last_cross)) if (bull and last_cross is not None) else 0.0
                cols["gc_ext"][sym,d]=(s50[i]/s200[i]-1.0)+fresh
                cols["gc"][sym,d]=(s50[i]/s200[i]-1.0)+ (1.0/(1.0+(i-last_cross)) if (bull and last_cross is not None) else float("nan"))
    frames={}
    for leg,dd in cols.items():
        m=defaultdict(dict)
        for (sym,d),v in dd.items(): m[sym][d]=v
        df=pd.DataFrame(m); df.index=pd.to_datetime(df.index); frames[leg]=df.sort_index()
    return frames

def picks_from(score, lo, hi, frac=0.05, power=1.0):
    out={}
    for ts,row in score.iterrows():
        d=ts.strftime("%Y-%m-%d")
        if not (lo<=d<hi): continue
        vals=row.dropna()
        if len(vals)<50: continue
        k=max(1,int(len(vals)*frac))
        sel=list(vals.sort_values(ascending=False).index[:k])
        w=[float(k-j)**power for j in range(k)]; tot=sum(w)
        out[d]=[(s,wi/tot) for s,wi in zip(sel,w)]
    return out

if __name__=="__main__":
    print("building leg frames for the full universe...", flush=True)
    F=build_all()
    exc,fwd,prox=load_outcomes()
    r=lambda df: df.rank(axis=1,pct=True)

    cur_score=((r(F["imom"])+r(F["ma"])+r(F["gc"]))/3).where(F["crossed"]>0)
    cur=picks_from(cur_score,OOS_LO,"2099")

    # ---------- PHASE 1: golden stock-days ----------
    oos_fwd={(s,d):v for (s,d,hz),v in fwd.items() if hz==60 and d>=OOS_LO}
    thresh=sorted(oos_fwd.values())[int(0.98*len(oos_fwd))]
    golden=[(s,d) for (s,d),v in oos_fwd.items() if v>=thresh]
    caught=missed_near=missed_far=inelig=0; nodata=0
    miss_syms=defaultdict(int)
    for s,d in golden:
        if d not in cur: nodata+=1; continue
        sel={x for x,_ in cur[d]}
        if s in sel: caught+=1; continue
        try:
            ts=pd.Timestamp(d)
            row=cur_score.loc[ts].dropna().sort_values(ascending=False)
        except KeyError:
            nodata+=1; continue
        if s not in row.index:
            inelig+=1; miss_syms[s]+=1; continue
        pos=int((row.index==s).argmax())+1
        if pos<=int(len(row)*0.10): missed_near+=1
        else: missed_far+=1
        miss_syms[s]+=1
    tot=caught+missed_near+missed_far+inelig
    print(f"\nPHASE 1 — golden stock-days (fwd60 >= {thresh*100:.1f}%, top 2% of panel): n={tot}")
    print(f"  CAUGHT (in top-5% selection)        : {caught:5d}  ({caught/tot*100:.1f}%)")
    print(f"  eligible, rank in 5-10% band        : {missed_near:5d}  ({missed_near/tot*100:.1f}%)")
    print(f"  eligible, rank below 10%            : {missed_far:5d}  ({missed_far/tot*100:.1f}%)")
    print(f"  INELIGIBLE (no golden cross)        : {inelig:5d}  ({inelig/tot*100:.1f}%)")
    top_missed=sorted(miss_syms.items(),key=lambda x:-x[1])[:10]
    print("  most-missed names:", ", ".join(f"{s}({c})" for s,c in top_missed))

    # top-25 buy&hold stocks of the OOS window and screen engagement
    perf={}
    for fn in sorted(os.listdir(DATA)):
        sym=fn[:-4]
        if not fn.endswith(".csv") or sym in ETF: continue
        bars=I.load_bars(os.path.join(DATA,fn))
        c=[(b[0],b[4]) for b in bars if b[0]>=OOS_LO]
        if len(c)<400: continue
        perf[sym]=c[-1][1]/c[0][1]-1
    top25=sorted(perf.items(),key=lambda x:-x[1])[:25]
    print(f"\n  top-25 buy&hold stocks 2024-2026 vs screen engagement:")
    print(f"  {'sym':<6}{'B&H':>9}{'days selected':>15}{'days inelig%':>13}")
    for sym,p in top25:
        seld=sum(1 for d,items in cur.items() if any(x==sym for x,_ in items))
        try:
            col=cur_score[sym]; col=col[col.index>=OOS_LO]
            inel=col.isna().mean()*100
        except KeyError:
            inel=100.0
        print(f"  {sym:<6}{p*100:>+8.0f}%{seld:>15}{inel:>12.0f}%")

    # ---------- PHASE 2: pre-registered amendments ----------
    print("\nPHASE 2 — pre-registered amendments, IS gate then one OOS shot")
    e1=((F["crossed"]>0)|((F["above200"]>0)&(F["s50rising"]>0)))
    e2=(F["above200"]>0)
    CANDS={
      "C1 early-entry elig": ((r(F["imom"])+r(F["ma"])+r(F["gc_ext"]))/3).where(e1),
      "C2 above-200 elig":   ((r(F["imom"])+r(F["ma"])+r(F["gc_ext"]))/3).where(e2),
      "C3 + imom63 leg":     ((r(F["imom"])+r(F["ma"])+r(F["gc"])+r(F["imom63"]))/4).where(F["crossed"]>0),
    }
    IS=("2020-01-01","2024-01-01")
    base_is=picks_from(cur_score,*IS)
    bm={hz:monthly(base_is,exc,hz) for hz in (30,60)}
    print(f"  {'candidate':<22}{'60d own':>9}{'diff':>8}{'t':>6}{'30d own':>9}{'diff':>8}{'t':>6}  gate")
    b60=statistics.fmean(bm[60].values())*20/60*100; b30=statistics.fmean(bm[30].values())*20/30*100
    print(f"  {'CURRENT screen':<22}{b60:>+8.3f}%{'':>8}{'':>6}{b30:>+8.3f}%")
    surv=[]
    for name,sc in CANDS.items():
        pk=picks_from(sc,*IS)
        res={}
        for hz,lag in ((30,2),(60,3)):
            own=monthly(pk,exc,hz); com=sorted(set(own)&set(bm[hz]))
            om=statistics.fmean([own[m] for m in com])*20/hz*100
            dm,dt=nw_t([own[m]-bm[hz][m] for m in com],lag)
            res[hz]=(om,dm*20/hz*100,dt)
        ok=res[30][1]>0 and res[30][2]>=1.5 and res[60][1]>0 and res[60][2]>=1.5
        if ok: surv.append((res[60][1],name))
        print(f"  {name:<22}{res[60][0]:>+8.3f}%{res[60][1]:>+7.3f}%{res[60][2]:>6.2f}"
              f"{res[30][0]:>+8.3f}%{res[30][1]:>+7.3f}%{res[30][2]:>6.2f}  {'PASS' if ok else 'fail'}")
    surv.sort(reverse=True)
    finalists=[n for _,n in surv[:2]]
    print(f"\n  finalists: {finalists or 'NONE — the current screen stands'}")
    if finalists:
        boos={hz:monthly(cur,exc,hz) for hz in (30,60)}
        for name in finalists:
            pk=picks_from(CANDS[name],OOS_LO,"2099")
            print(f"\n  === OOS one shot: {name} ===")
            for hz,lag in ((30,2),(60,3)):
                own=monthly(pk,exc,hz); com=sorted(set(own)&set(boos[hz]))
                om=statistics.fmean([own[m] for m in com])*20/hz*100
                bo=statistics.fmean([boos[hz][m] for m in com])*20/hz*100
                dm,dt=nw_t([own[m]-boos[hz][m] for m in com],lag)
                rr,wn=raw_stats(pk,fwd,hz); sh,_=sharpe(pk,fwd,hz)
                print(f"    {hz}d own {om:+.3f}% (cur {bo:+.3f}%)  diff {dm*20/hz*100:+.3f}% t={dt:.2f}"
                      f"  raw {rr:+.2f}%  win {wn:.1f}%  Sharpe {sh:.2f}")
            line="    survivorship 60d: "
            for cut in (0,50,75):
                mm=monthly(pk,exc,60,prox,cut); m_,t_=nw_t(list(mm.values()),3)
                line+=f">={cut}% {m_*20/60*100:+6.3f}%  "
            print(line)
            yy=[]
            for y in ("2024","2025","2026"):
                sub={d:v for d,v in pk.items() if d[:4]==y}
                mv=list(monthly(sub,exc,60).values())
                yy.append(f"{y} {statistics.fmean(mv)*20/60*100:+6.2f}%" if len(mv)>=2 else f"{y} n/a")
            print("    by year 60d: "+"   ".join(yy))
            # recall improvement on golden days
            c2=0
            for s,d in golden:
                if d in pk and any(x==s for x,_ in pk[d]): c2+=1
            print(f"    golden-day capture: {c2/tot*100:.1f}% (current {caught/tot*100:.1f}%)")
