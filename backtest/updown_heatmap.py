"""Render the daily Up/Down dataset as a single-file HTML calendar heatmap.

  python backtest/updown_heatmap.py                       # data/updown/*.csv -> <repo>/index.html
  python backtest/updown_heatmap.py --data-dir data/updown --out heatmap.html

Layout follows https://crypto-calendar-heatmap.lovable.app/ : one card per month (oldest first),
one square per day (Sunday first), green = Up and red = Down in four shades by size of the move,
the month's cumulative return in the card header, and a summary bar with a legend. A BTC / ETH
switch and a 12-month / all switch are pure CSS (radio inputs). The page
makes no external requests. The hover card is a few lines of inline JavaScript; without it the
squares still show the colours and the switches still work.

A square sits on its question's settle day ("on <date>", US Eastern). The move is the Binance
1-minute close at 12:00 ET against the day before, not the UTC daily candle. Hover a square for the
target and settle prices and the start / settle times in Taiwan time.

In the source repo the page is written to index.html at the repo root: open it from disk, or serve it
with GitHub Pages (branch main, folder /root) at https://<user>.github.io/<repo>/.
"""

import argparse
import base64
import calendar
import datetime as dt
import glob
import html
import math
import os
import sys

import skill_path  # noqa: F401  (puts skills/tv-ta/scripts on sys.path)

import updown_dataset  # noqa: E402

WEEKDAYS = ("日", "一", "二", "三", "四", "五", "六")  # Sunday first, like the reference page
WEEKDAY_NAMES = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")  # date.weekday(): Monday = 0
LEVELS = (0.5, 1.5, 3.0)  # |move| % bounds between shade 1|2, 2|3, 3|4
RANGE_LABELS = {12: "近 12 個月", 24: "近 2 年"}
RANGES = (12, 24)  # months offered by the range switch; the longest one is the most that is drawn
HIGHLIGHT_PCT = 10.0  # a month's cumulative return this large is shown in colour
ICONS = {"BTC": ("#f7931a", "₿"), "ETH": ("#627eea", "Ξ")}  # fallback badge when there is no logo file
COIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "coins")

CSS = """
:root{--bg:#10141f;--card:#181d2a;--line:#262d3d;--ink:#e6e8ee;--muted:#8a93a8;
--g1:#1d5b34;--g2:#1e7d3f;--g3:#22a24d;--g4:#4ade80;--r1:#7a2f2f;--r2:#b03a3a;--r3:#e84a4a;--r4:#ff6767}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif}
input.sw{position:absolute;opacity:0;pointer-events:none}
main{max-width:1280px;margin:0 auto;padding:36px 20px 56px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:28px}
h1{display:flex;align-items:center;gap:10px;font-size:26px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin:0}
.ico{display:inline-flex;width:30px;height:30px;border-radius:50%;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:16px}
.ico.s{width:18px;height:18px;font-size:11px}
.ctl{display:flex;gap:10px;flex-wrap:wrap}
.pill{display:flex;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px}
.pill label{display:flex;align-items:center;gap:6px;padding:6px 12px;border-radius:7px;cursor:pointer;color:var(--muted);font-weight:600;font-size:13px}
.months{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 16px 12px;display:flex;flex-direction:column}
.ch{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;font-weight:600}
.tot{font-size:11px;font-weight:500;color:var(--muted)}.tot.up{color:var(--g4)}.tot.down{color:var(--r4)}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;min-height:152px;align-content:start;flex:1}
.dow{font-size:10px;color:var(--muted);text-align:center;margin-bottom:2px}
.c{aspect-ratio:1;border-radius:4px}.c.blank{background:transparent}
.u1{background:var(--g1)}.u2{background:var(--g2)}.u3{background:var(--g3)}.u4{background:var(--g4)}
.d1{background:var(--r1)}.d2{background:var(--r2)}.d3{background:var(--r3)}.d4{background:var(--r4)}
.c:not(.blank):hover{outline:2px solid var(--ink);outline-offset:1px}
.cf{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--line);margin-top:14px;padding-top:10px;font-size:11px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}
.sum{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-top:20px;color:var(--muted)}
.sum b{color:var(--ink);font-weight:600}.sum span{margin-right:24px}
.legend{display:flex;align-items:center;gap:4px;font-size:12px}.legend i{width:12px;height:12px;border-radius:3px;display:inline-block}
.legend em{font-style:normal;margin:0 4px}
.note{color:var(--muted);font-size:12px;margin-top:20px}
#tip{position:fixed;z-index:10;display:none;width:250px;background:#0a0d15;border:1px solid #38425a;border-radius:10px;padding:12px 14px;box-shadow:0 10px 30px rgba(0,0,0,.55);pointer-events:none;font-size:13px}
#tip .h{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:8px}
#tip .h b{font-size:15px}#tip .h span{color:var(--muted);font-size:12px}
#tip .res{display:flex;justify-content:space-between;align-items:center;border-radius:8px;padding:6px 10px;margin-bottom:10px;font-weight:700;font-size:15px}
#tip .res.up{background:rgb(34 162 77/.18);color:var(--g4)}#tip .res.down{background:rgb(232 74 74/.18);color:var(--r4)}
#tip dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0}
#tip dt{color:var(--muted)}#tip dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
#tip .tz{margin-top:8px;color:var(--muted);font-size:11px;text-align:right}
"""


SCRIPT = """
(function(){
var tip=document.getElementById('tip'),cur=null;
function row(k,v){return '<dt>'+k+'</dt><dd>'+v+'</dd>';}
function esc(x){return String(x).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function show(c){
  var d=c.dataset,up=d.r==='up';cur=c;
  tip.innerHTML='<div class="h"><b>'+esc(d.d)+'</b><span>'+esc(d.w)+'（美東結算日）</span></div>'
   +'<div class="res '+d.r+'"><span>'+(up?'▲ Up 漲':'▼ Down 跌')+'</span><span>'+esc(d.p)+'%</span></div>'
   +'<dl>'+row('目標價',esc(d.t))+row('結算價',esc(d.s))
   +row('開始',esc(d.a))+row('結束',esc(d.b))+'</dl><div class="tz">時間為台灣時間</div>';
  tip.style.display='block';
  var r=c.getBoundingClientRect(),w=tip.offsetWidth,h=tip.offsetHeight;
  var x=Math.max(8,Math.min(innerWidth-w-8,r.left+r.width/2-w/2));
  var y=r.top-h-10; if(y<8) y=r.bottom+10;
  tip.style.left=x+'px'; tip.style.top=y+'px';
}
function hide(){tip.style.display='none';cur=null;}
document.addEventListener('mouseover',function(e){var c=e.target.closest&&e.target.closest('.c[data-d]');if(c)show(c);else hide();});
document.addEventListener('click',function(e){var c=e.target.closest&&e.target.closest('.c[data-d]');if(c&&c!==cur)show(c);else hide();});
document.addEventListener('scroll',hide,true);
})();
"""


def coin_logo_css(symbols: list[str]) -> str:
    """Rules that paint each coin's logo (backtest/assets/coins/<sym>.png, CoinMarketCap 64x64) as a data URI."""
    rules = []
    for sym in symbols:
        path = os.path.join(COIN_DIR, f"{sym.lower()}.png")
        if os.path.exists(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            rules.append(f".logo-{sym}{{background:url(data:image/png;base64,{b64}) center/cover no-repeat}}")
    return "".join(rules)


def load(path: str) -> list[dict]:
    rows = updown_dataset.read_rows(path)
    return [{**r, "date": dt.date.fromisoformat(r["question_date"]),
             "ret": float(r["return_pct"]), "up": r["result"] == "Up"} for r in rows]


def level(ret: float) -> int:
    """Shade 1 (small move) to 4 (large move) for a daily return in percent."""
    return 1 + sum(abs(ret) >= bound for bound in LEVELS)


def month_total(rows: list[dict]) -> float:
    """Compounded return of a month's daily questions, in percent."""
    total = 1.0
    for r in rows:
        total *= 1 + r["ret"] / 100
    return (total - 1) * 100


def month_keys(rows: list[dict]) -> list[tuple[int, int]]:
    return sorted({(r["date"].year, r["date"].month) for r in rows})


def cell(r: dict | None) -> str:
    if r is None:
        return '<div class="c blank"></div>'
    attrs = {"d": r["question_date"], "w": WEEKDAY_NAMES[r["date"].weekday()], "r": "up" if r["up"] else "down",
             "p": f'{r["ret"]:+.2f}', "t": f'{float(r["target"]):,.2f}', "s": f'{float(r["settle_price"]):,.2f}',
             "a": r["start_tw"], "b": r["settle_tw"]}
    data = " ".join(f'data-{k}="{html.escape(v)}"' for k, v in attrs.items())
    return f'<div class="c {"u" if r["up"] else "d"}{level(r["ret"])}" {data}></div>'


def month_card(year: int, month: int, rows: list[dict], hidden_from: str = "") -> str:
    by_date = {r["date"]: r for r in rows}
    first_wd, days = calendar.monthrange(year, month)  # Monday = 0
    lead = (first_wd + 1) % 7  # Sunday-first offset
    cells = [f'<div class="dow">{w}</div>' for w in WEEKDAYS]
    cells += ['<div class="c blank"></div>'] * lead
    cells += [cell(by_date.get(dt.date(year, month, d))) for d in range(1, days + 1)]
    mine = [r for r in rows if (r["date"].year, r["date"].month) == (year, month)]
    total = month_total(mine)
    tone = "up" if total >= HIGHLIGHT_PCT else "down" if total <= -HIGHLIGHT_PCT else ""
    ups = sum(r["up"] for r in mine)
    return (f'<div class="card{hidden_from}"><div class="ch"><span>{year} / {month:02d}</span>'
            f'<span class="tot {tone}">累計 {total:+.1f}%</span></div><div class="grid">{"".join(cells)}</div>'
            f'<div class="cf"><span><span class="dot" style="background:var(--g3)"></span>{ups} 漲　'
            f'<span class="dot" style="background:var(--r3)"></span>{len(mine) - ups} 跌</span><span>共 {len(mine)} 天</span></div></div>')


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def monthly_average(rows: list[dict], months: int) -> tuple[float, float]:
    """Average Up and Down days per month over `months` calendar months."""
    up = sum(r["up"] for r in rows)
    return up / months, (len(rows) - up) / months


def summary(rows: list[dict], cls: str, months: int) -> str:
    up = sum(r["up"] for r in rows)
    n = len(rows)
    avg_up, avg_down = monthly_average(rows, months)
    legend = ("".join(f'<i style="background:var(--r{i})"></i>' for i in (4, 3, 2, 1))
              + "".join(f'<i style="background:var(--g{i})"></i>' for i in (1, 2, 3, 4)))
    return (f'<div class="sum {cls}"><div><span>總天數 <b>{n}</b></span><span>上漲 <b>{up}</b> 天（{pct(up / n)}）</span>'
            f'<span>下跌 <b>{n - up}</b> 天（{pct((n - up) / n)}）</span>'
            f'<span>平均每月 <b>{math.floor(avg_up + 0.5)}</b> 漲　<b>{math.floor(avg_down + 0.5)}</b> 跌</span></div>'
            f'<div class="legend">跌<em></em>{legend}<em></em>漲</div></div>')


def symbol_section(symbol: str, rows: list[dict]) -> str:
    if not rows:
        return f'<section class="sec" id="{symbol}"><p class="sub">{symbol}：沒有資料。</p></section>'
    keys = month_keys(rows)
    recent = {n: set(keys[-n:]) for n in RANGES}
    cards = ""
    for key in keys:
        # a class per range that does not include this month; the CSS decides which one applies
        hidden = "".join(f" o{n}" for n in RANGES if key not in recent[n])
        cards += month_card(key[0], key[1], rows, hidden)
    sums = "".join(summary([r for r in rows if (r["date"].year, r["date"].month) in recent[n]], f"s{n}",
                           len(recent[n])) for n in RANGES)
    return f'<section class="sec" id="{symbol}"><div class="months">{cards}</div>{sums}</section>'


def switch_css(symbols: list[str]) -> str:
    """CSS-only switches: which symbol is shown and how many months."""
    longest = max(RANGES)
    css = [f".o{longest}{{display:none}}"]
    for n in RANGES:
        others = "".join(f",#r-{n}:checked~main .s{m}" for m in RANGES if m != n)
        css.append(f"#r-{n}:checked~main .o{n}{others}{{display:none}}")
        css.append(f"#r-{n}:checked~main label[for=r-{n}]{{background:#262d3d;color:var(--ink)}}")
    for s in symbols:
        css.append(f"#s-{s}:checked~main .sec:not(#{s}){{display:none}}")
        css.append(f"#s-{s}:checked~main label[for=s-{s}]{{background:#262d3d;color:var(--ink)}}")
    return "".join(css)


def render(datasets: dict[str, list[dict]]) -> str:
    symbols = list(datasets)
    first = symbols[0]
    inputs = "".join(f'<input class="sw" type="radio" name="sym" id="s-{s}"{" checked" if s == first else ""}>'
                     for s in symbols)
    inputs += "".join(f'<input class="sw" type="radio" name="rng" id="r-{n}"{" checked" if n == RANGES[0] else ""}>'
                      for n in RANGES)

    logos = coin_logo_css(symbols)
    has_logo = {s for s in symbols if f".logo-{s}{{" in logos}

    def icon(s, small=False):
        size = " s" if small else ""
        if s in has_logo:
            return f'<span class="ico logo-{s}{size}" role="img" aria-label="{s}"></span>'
        color, glyph = ICONS.get(s, ("#666", s[:1]))
        return f'<span class="ico{size}" style="background:{color}">{glyph}</span>'

    titles = "".join(f'<h1 class="ttl" id="h-{s}">{icon(s)}{s} 每日漲跌熱力圖</h1>' for s in symbols)
    title_css = "".join(f"#s-{s}:checked~main #h-{s}{{display:flex}}" for s in symbols)
    range_switch = "".join(f'<label for="r-{n}">{RANGE_LABELS[n]}</label>' for n in RANGES)
    sym_switch = "".join(f'<label for="s-{s}">{icon(s, True)}{s}</label>' for s in symbols)
    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Binance 每日漲跌題 熱力圖</title>
<style>{CSS}{logos}h1.ttl{{display:none}}{title_css}{switch_css(symbols)}</style></head><body>
{inputs}<main>
<header><div>{titles}<p class="sub">月曆式排版 · 綠漲紅跌 · 幣安每日漲跌題（前一日美東 12:00 到當日美東 12:00，1 分鐘收盤價）</p></div>
<div class="ctl"><div class="pill">{sym_switch}</div>
<div class="pill">{range_switch}</div></div></header>
{"".join(symbol_section(s, rows) for s, rows in datasets.items())}
<p class="note">每格一題，放在結算日（美東）。台灣時間為當日 00:00 或 01:00 到隔天同時間；滑鼠移到格子上看目標價、結算價和起訖時間。
顏色深淺依漲跌幅：&lt;0.5%、&lt;1.5%、&lt;3%、≥3%。資料由 backtest/updown_dataset.py 產生；非投資建議。</p>
</main><div id="tip" role="tooltip"></div><script>{SCRIPT}</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render the Up/Down dataset as an HTML calendar heatmap.")
    ap.add_argument("--data-dir", default=None, help="folder with <symbol>.csv files (default: <repo>/data/updown)")
    ap.add_argument("--out", default=None, help="default: <repo>/index.html")
    args = ap.parse_args(argv)
    data_dir = args.data_dir or updown_dataset.default_out_dir()
    paths = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not paths:
        print(f"no CSV files in {data_dir}; run backtest/updown_dataset.py first", file=sys.stderr)
        return 2
    datasets = {os.path.splitext(os.path.basename(p))[0].upper(): load(p) for p in paths}
    out = args.out or os.path.join(updown_dataset.repo_root(), "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(datasets))
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"寫入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
