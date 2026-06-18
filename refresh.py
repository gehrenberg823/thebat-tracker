#!/usr/bin/env python3
"""Build a local dashboard tracking The Bat manual-trade results on Kalshi.

Pulls every settled position tagged strategy_name='Manual Trade' on an MLB
ticker (= the The Bat test) from the market-maker ClickHouse, derives the
market from the Kalshi ticker's series prefix (because offer_name is not
logged on manual trades), and renders a self-contained index.html.

    python3 refresh.py            # query + rebuild index.html
    python3 refresh.py --open     # also open it in the browser

Read-only SELECTs against market_maker_settlement_log. Credentials come from
Repository/os.tools/.env.local (CLICKHOUSE_MARKET_MAKER_*). Settled trades only.
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = "/Users/gregehrenberg/Repository/os.tools/.env.local"
OUT = os.path.join(HERE, "index.html")

# Kalshi series prefix (ticker up to the first '-') -> readable market name.
# This is the mapping that recovers the market with offer_name missing.
MARKET_NAMES = {
    "KXMLBHR": "HR (Total Home Runs)",
    "KXMLBHRR": "Hits + Runs + RBIs",
    "KXMLBTB": "Total Bases",
    "KXMLBHIT": "Total Hits",
    "KXMLBKS": "Strikeouts (pitcher)",
    "KXMLBTEAMTOTAL": "Team Total Runs",
    "KXMLBTOTAL": "Game Total Runs",
    "KXMLBSPREAD": "Run Line",
    "KXMLBGAME": "Moneyline",
    "KXMLBRFI": "Run First Inning",
    "KXMLBF5SPREAD": "F5 Run Line",
    "KXMLBF5TOTAL": "F5 Total",
    "KXMLBF5GAME": "F5 Moneyline",
    "KXMLBF5": "F5 Moneyline",
}

# Trades we count as the The Bat test: Manual Trade strategy on an MLB ticker.
WHERE = "strategy_name='Manual Trade' AND startsWith(ticker,'KXMLB')"

# Entry-price buckets for the per-market drill-down.
BUCKET_ORDER = ["<30", "30-45", "45-60", "60-75", "75+"]
BUCKET_LABEL = {"<30": "< 30¢", "30-45": "30–45¢",
                "45-60": "45–60¢", "60-75": "60–75¢",
                "75+": "75¢ +"}


def load_creds() -> dict:
    keys = ("HOST", "USERNAME", "PASSWORD", "DATABASE")
    out = {}
    with open(ENV, encoding="utf-8") as f:
        for line in f:
            for k in keys:
                pfx = f"CLICKHOUSE_MARKET_MAKER_{k}="
                if line.startswith(pfx):
                    out[k] = line[len(pfx):].strip().strip("'\"")
    missing = [k for k in keys if k not in out]
    if missing:
        raise SystemExit(f"Missing creds in {ENV}: {missing}")
    return out


def ch(creds: dict, sql: str) -> list[dict]:
    """Run a query, return rows as dicts (expects FORMAT JSONEachRow)."""
    url = f"https://{creds['HOST']}:8443/?database={creds['DATABASE']}"
    r = requests.post(
        url, params={"default_format": "JSONEachRow"},
        auth=(creds["USERNAME"], creds["PASSWORD"]),
        data=sql.encode("utf-8"), timeout=120,
    )
    r.raise_for_status()
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def market_name(series: str) -> str:
    return MARKET_NAMES.get(series, series)


def fnum(cents) -> float:
    return round(float(cents) / 100.0, 2)


def fetch(creds: dict) -> dict:
    by_market = ch(creds, f"""
        WITH splitByChar('-', ticker)[1] AS series
        SELECT series,
               count() AS pos,
               sum(total_cost_cents) AS deployed_c,
               sum(profit_loss_cents) AS pnl_c,
               countIf(settlement_result='won') AS wins
        FROM market_maker_settlement_log
        WHERE {WHERE}
        GROUP BY series ORDER BY deployed_c DESC
    """)

    overall = ch(creds, f"""
        SELECT count() AS pos,
               sum(total_cost_cents) AS deployed_c,
               sum(profit_loss_cents) AS pnl_c,
               countIf(settlement_result='won') AS wins,
               toString(min(index_date)) AS first_day,
               toString(max(index_date)) AS last_day
        FROM market_maker_settlement_log WHERE {WHERE}
    """)[0]

    by_side = ch(creds, f"""
        SELECT exchange_side,
               count() AS pos,
               sum(total_cost_cents) AS deployed_c,
               sum(profit_loss_cents) AS pnl_c,
               countIf(settlement_result='won') AS wins
        FROM market_maker_settlement_log WHERE {WHERE}
        GROUP BY exchange_side ORDER BY exchange_side
    """)

    by_day = ch(creds, f"""
        SELECT toString(index_date) AS d,
               count() AS pos,
               sum(profit_loss_cents) AS pnl_c,
               sum(total_cost_cents) AS deployed_c
        FROM market_maker_settlement_log WHERE {WHERE}
        GROUP BY d ORDER BY d
    """)

    recent = ch(creds, f"""
        WITH splitByChar('-', ticker)[1] AS series
        SELECT toString(index_date) AS d, ticker, series, exchange_side,
               round(average_entry_price_cents,1) AS entry,
               quantity, profit_loss_cents AS pnl_c, settlement_result
        FROM market_maker_settlement_log WHERE {WHERE}
        ORDER BY timestamp DESC LIMIT 100
    """)

    by_market_side = ch(creds, f"""
        WITH splitByChar('-', ticker)[1] AS series
        SELECT series, exchange_side AS side, count() AS pos,
               sum(total_cost_cents) AS deployed_c, sum(profit_loss_cents) AS pnl_c,
               countIf(settlement_result='won') AS wins
        FROM market_maker_settlement_log WHERE {WHERE}
        GROUP BY series, side ORDER BY series, side
    """)

    by_market_bucket = ch(creds, f"""
        WITH splitByChar('-', ticker)[1] AS series
        SELECT series,
               multiIf(average_entry_price_cents<30,'<30',
                       average_entry_price_cents<45,'30-45',
                       average_entry_price_cents<60,'45-60',
                       average_entry_price_cents<75,'60-75','75+') AS bucket,
               count() AS pos, sum(total_cost_cents) AS deployed_c,
               sum(profit_loss_cents) AS pnl_c, countIf(settlement_result='won') AS wins
        FROM market_maker_settlement_log WHERE {WHERE}
        GROUP BY series, bucket
    """)

    return {"by_market": by_market, "overall": overall, "by_side": by_side,
            "by_day": by_day, "recent": recent,
            "by_market_side": by_market_side, "by_market_bucket": by_market_bucket}


def roi(pnl_c, deployed_c):
    d = float(deployed_c)
    return round(100.0 * float(pnl_c) / d, 1) if d else 0.0


def pct(n, d):
    return round(100.0 * float(n) / float(d), 0) if d else 0.0


# ---------- rendering ----------

def cls(v):
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def money(v):
    return f"+${v:,.0f}" if v >= 0 else f"-${abs(v):,.0f}"


def _subtable(title: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    body = ""
    for r in rows:
        body += (
            f"<tr><td>{r['label']}</td><td>{r['pos']}</td>"
            f"<td>${r['dep']:,.0f}</td>"
            f"<td class='{cls(r['pnl'])}'>{money(r['pnl'])}</td>"
            f"<td class='{cls(r['roi'])}'>{r['roi']:+.1f}%</td>"
            f"<td>{r['win']:.0f}%</td></tr>"
        )
    return (
        f"<div class='sub'><div class='subt'>{title}</div>"
        f"<table class='subtab'><thead><tr><th></th><th>Pos</th><th>Deployed</th>"
        f"<th>P&amp;L</th><th>ROI</th><th>Win%</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def render(data: dict) -> str:
    o = data["overall"]
    dep = fnum(o["deployed_c"]); pnl = fnum(o["pnl_c"])
    o_roi = roi(o["pnl_c"], o["deployed_c"]); o_win = pct(o["wins"], o["pos"])

    # per-market drill-down lookups (side + entry-price bucket)
    sides_by: dict[str, list] = {}
    for r in data["by_market_side"]:
        sides_by.setdefault(r["series"], []).append({
            "label": "YES / Over" if r["side"] == "yes" else "NO / Under",
            "pos": r["pos"], "dep": fnum(r["deployed_c"]), "pnl": fnum(r["pnl_c"]),
            "roi": roi(r["pnl_c"], r["deployed_c"]), "win": pct(r["wins"], r["pos"]),
        })
    buckets_raw: dict[str, dict] = {}
    for r in data["by_market_bucket"]:
        buckets_raw.setdefault(r["series"], {})[r["bucket"]] = r
    buckets_by: dict[str, list] = {}
    for s, bmap in buckets_raw.items():
        lst = []
        for b in BUCKET_ORDER:
            if b in bmap:
                r = bmap[b]
                lst.append({
                    "label": BUCKET_LABEL[b], "pos": r["pos"], "dep": fnum(r["deployed_c"]),
                    "pnl": fnum(r["pnl_c"]), "roi": roi(r["pnl_c"], r["deployed_c"]),
                    "win": pct(r["wins"], r["pos"]),
                })
        buckets_by[s] = lst

    # market rows (each followed by a hidden, expandable detail row)
    mrows = ""
    for m in data["by_market"]:
        s = m["series"]
        d_ = fnum(m["deployed_c"]); p_ = fnum(m["pnl_c"])
        r_ = roi(m["pnl_c"], m["deployed_c"]); w_ = pct(m["wins"], m["pos"])
        onclick = (
            f"var d=document.getElementById('d-{s}');"
            f"d.style.display=d.style.display==='none'?'table-row':'none';"
            f"this.classList.toggle('open')"
        )
        mrows += (
            f"<tr class='mrow' onclick=\"{onclick}\"><td class='mkt'>"
            f"<span class='car'>&#9656;</span>{market_name(s)}"
            f"<span class='pfx'>{s}</span></td>"
            f"<td>{m['pos']}</td><td>${d_:,.0f}</td>"
            f"<td class='{cls(p_)}'>{money(p_)}</td>"
            f"<td class='{cls(r_)}'>{r_:+.1f}%</td>"
            f"<td>{w_:.0f}%</td></tr>"
        )
        detail = (
            f"<div class='detail'>{_subtable('By side', sides_by.get(s, []))}"
            f"{_subtable('By entry price', buckets_by.get(s, []))}</div>"
        )
        mrows += (
            f"<tr class='detail-row' id='d-{s}' style='display:none'>"
            f"<td colspan='6'>{detail}</td></tr>"
        )

    # side rows
    srows = ""
    for s in data["by_side"]:
        d_ = fnum(s["deployed_c"]); p_ = fnum(s["pnl_c"])
        r_ = roi(s["pnl_c"], s["deployed_c"]); w_ = pct(s["wins"], s["pos"])
        label = "YES / Over" if s["exchange_side"] == "yes" else "NO / Under"
        srows += (
            f"<tr><td>{label}</td><td>{s['pos']}</td><td>${d_:,.0f}</td>"
            f"<td class='{cls(p_)}'>{money(p_)}</td>"
            f"<td class='{cls(r_)}'>{r_:+.1f}%</td><td>{w_:.0f}%</td></tr>"
        )

    # recent rows
    rrows = ""
    for t in data["recent"]:
        p_ = fnum(t["pnl_c"])
        won = t["settlement_result"] == "won"
        rrows += (
            f"<tr><td>{t['d']}</td><td class='mkt'>{market_name(t['series'])}</td>"
            f"<td>{'YES' if t['exchange_side']=='yes' else 'NO'}</td>"
            f"<td>{t['entry']:.0f}¢</td><td>{t['quantity']:.0f}</td>"
            f"<td class='{'pos' if won else 'neg'}'>{money(p_)}</td></tr>"
        )

    # cumulative pnl series for the chart
    cum = 0.0; labels = []; cumvals = []; dayvals = []
    for row in data["by_day"]:
        cum += fnum(row["pnl_c"])
        labels.append(row["d"]); cumvals.append(round(cum, 2))
        dayvals.append(fnum(row["pnl_c"]))

    stamp = datetime.now().astimezone().strftime("%b %-d, %Y · %-I:%M %p %Z")

    return TEMPLATE.format(
        first=o["first_day"], last=o["last_day"], stamp=stamp,
        pos=o["pos"], dep=f"${dep:,.0f}",
        pnl=money(pnl), pnl_cls=cls(pnl),
        roi=f"{o_roi:+.1f}%", roi_cls=cls(o_roi), win=f"{o_win:.0f}%",
        mrows=mrows, srows=srows, rrows=rrows,
        labels=json.dumps(labels), cumvals=json.dumps(cumvals),
        dayvals=json.dumps(dayvals),
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Bat — Manual Trade Tracker</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{{--bg:#0f1419;--card:#1a212b;--line:#2a3441;--mut:#8a97a8;--pos:#22c55e;--neg:#ef4444;--txt:#e6edf3}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:22px;margin:0 0 2px}}
.sub{{color:var(--mut);font-size:13px;margin-bottom:22px}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:26px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card .k{{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.card .v{{font-size:22px;font-weight:600;margin-top:4px}}
h2{{font-size:15px;margin:26px 0 10px;color:var(--txt)}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line)}}
th:first-child,td:first-child{{text-align:left}}
th{{color:var(--mut);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.03em;background:#161d26}}
tr:last-child td{{border-bottom:none}}
.pos{{color:var(--pos)}} .neg{{color:var(--neg)}}
.mkt{{font-weight:500}}
.pfx{{display:block;color:var(--mut);font-size:11px;font-weight:400;font-family:ui-monospace,monospace}}
.mrow{{cursor:pointer}}
.mrow:hover{{background:#1f2733}}
.car{{display:inline-block;width:14px;color:var(--mut);font-size:10px;transition:transform .15s}}
.mrow.open .car{{transform:rotate(90deg)}}
.detail-row td{{background:#11161d;padding:0}}
.detail{{display:flex;gap:18px;padding:14px 16px;flex-wrap:wrap}}
.sub{{flex:1;min-width:300px}}
.subt{{color:var(--mut);font-size:12px;margin-bottom:6px;text-transform:uppercase;letter-spacing:.03em}}
.subtab{{border:1px solid var(--line)}}
.subtab th,.subtab td{{padding:6px 10px;font-size:13px}}
.hint{{color:var(--mut);font-size:12px;font-weight:400;text-transform:none;letter-spacing:0}}
.chartbox{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:6px}}
.note{{color:var(--mut);font-size:12px;margin-top:8px}}
.small{{max-height:380px;overflow:auto}}
</style></head><body><div class="wrap">
<h1>The Bat — Manual Trade Tracker</h1>
<div class="sub">MLB <code>Manual Trade</code> positions (market derived from ticker) · settled {first} → {last} · refreshed {stamp}</div>

<div class="cards">
  <div class="card"><div class="k">Positions</div><div class="v">{pos}</div></div>
  <div class="card"><div class="k">Deployed</div><div class="v">{dep}</div></div>
  <div class="card"><div class="k">Net P&amp;L</div><div class="v {pnl_cls}">{pnl}</div></div>
  <div class="card"><div class="k">ROI</div><div class="v {roi_cls}">{roi}</div></div>
  <div class="card"><div class="k">Win rate</div><div class="v">{win}</div></div>
</div>

<div class="chartbox"><canvas id="cum" height="92"></canvas></div>
<div class="note">Cumulative net P&amp;L by settlement date (bars = daily P&amp;L). Settled trades only.</div>

<h2>ROI by market <span class="hint">— click a market to drill into side &amp; entry-price buckets</span></h2>
<table><thead><tr><th>Market</th><th>Pos</th><th>Deployed</th><th>P&amp;L</th><th>ROI</th><th>Win%</th></tr></thead>
<tbody>{mrows}</tbody></table>

<h2>By side</h2>
<table><thead><tr><th>Side</th><th>Pos</th><th>Deployed</th><th>P&amp;L</th><th>ROI</th><th>Win%</th></tr></thead>
<tbody>{srows}</tbody></table>

<h2>Recent settled trades</h2>
<div class="small"><table><thead><tr><th>Date</th><th>Market</th><th>Side</th><th>Entry</th><th>Qty</th><th>P&amp;L</th></tr></thead>
<tbody>{rrows}</tbody></table></div>

<p class="note">Scope: <code>strategy_name='Manual Trade'</code> on <code>KXMLB*</code> tickers = the The Bat baseball test. Market is recovered from the ticker's series prefix because <code>offer_name</code> is null on manual trades. Small per-market samples over a short window — read the signs as directional, not conclusive.</p>

<script>
const labels={labels}, cum={cumvals}, day={dayvals};
new Chart(document.getElementById('cum'),{{
  data:{{labels:labels,datasets:[
    {{type:'line',label:'Cumulative P&L ($)',data:cum,borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.12)',fill:true,tension:.25,pointRadius:2,yAxisID:'y'}},
    {{type:'bar',label:'Daily P&L ($)',data:day,backgroundColor:day.map(v=>v>=0?'rgba(34,197,94,.55)':'rgba(239,68,68,.55)'),yAxisID:'y'}}
  ]}},
  options:{{responsive:true,interaction:{{mode:'index',intersect:false}},
    plugins:{{legend:{{labels:{{color:'#8a97a8'}}}}}},
    scales:{{x:{{ticks:{{color:'#8a97a8'}},grid:{{color:'#202833'}}}},
            y:{{ticks:{{color:'#8a97a8',callback:v=>'$'+v}},grid:{{color:'#202833'}}}}}}}}
}});
</script>
</div></body></html>"""


def main():
    creds = load_creds()
    data = fetch(creds)
    html = render(data)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    o = data["overall"]
    print(f"Wrote {OUT}")
    print(f"  {o['pos']} positions, {fnum(o['deployed_c']):,.0f} deployed, "
          f"P&L {fnum(o['pnl_c']):+,.0f}, ROI {roi(o['pnl_c'], o['deployed_c']):+.1f}%")
    if "--open" in sys.argv:
        webbrowser.open("file://" + OUT)


if __name__ == "__main__":
    main()
