# tv-ta — TradingView Technical Analyst

tv-ta 是一個 agent skill，Claude Code、Hermes，以及其他支援 Agent Skills 標準的 agent 都能用。

它把 crypto-watch 的 28 項技術指標檢核表（`check.html`）改成用數值來跑：
- 從 Binance K 線自己算指標，使用 TradingView 內建指標的公式和預設參數；
- 每個檢核項目都是一個有型別的判斷節點（Score / Choice / Noul）；
- 用可調整的權重和 gate 合成最後結論，輸出 BUY / WAIT / SELL、逐項的數值證據，以及進場區、停損和目標價；
- 另外能回答預測市場（Polymarket、幣安錢包）的一日漲跌題。

以前是靠人或 AI 看圖判斷，但 TradingView 圖表是跨網域 iframe 裡的 canvas，讀不到數值，準確度有限。tv-ta 直接計算數值，同一根 K 棒每次跑出來的結果都一樣。

安裝和試用請看 [repo 首頁](../../README.md)。這份文件是進階說明：直接執行指令、看懂報告、調整權重，以及開發與發佈。

需要 Python 3.11 以上。核心程式只用標準函式庫，不需要 pip install。

選配功能：
- 安裝 `tvscreener` 後可以執行 `scripts/crosscheck.py`，和 TradingView 的數值直接比對。
- 設定 `TYPESAFE_API_KEY` 並在設定檔開啟後，指定的節點會改問 Jev。

## 直接執行指令

平常在對話裡問就好，agent 會替你執行。以下指令給想自己跑、或要除錯的人。指令都在 skill 資料夾內執行；Windows 上請把 `python` 換成 `py`。

```
python scripts/run.py BTC --tf 4h                     # 檢核表分析
python scripts/updown.py BTC                          # 下一個要結算的一日漲跌題
python scripts/updown.py BTC --as-of 2026-09-22T16:00 # 回放過去的題目
python scripts/replay.py BTC --tf 4h --bars 2000      # 回放歷史 K 棒，校準信心度路由門檻
python scripts/replay.py BTC --tf 4h --compare-node macd_cross  # 同一批 K 棒比較節點導入前後
python scripts/replay.py BTC --updown 700              # 回放最近 700 題一日漲跌題
python scripts/forward_log.py run                     # 每日前瞻紀錄：結算昨天的題、記錄今天的預測、印出成績表
python scripts/forward_log.py lean BTC                # 今天原本策略和學習版各偏哪邊（不寫入任何檔案）
python scripts/evaluate_node.py rsi_divergence        # 用固定標準評估一個節點能不能開啟
```

`run.py` 的參數：

| 參數 | 作用 |
|---|---|
| `--tf 15m\|1h\|4h\|1d` | 指定主要級別，預設是 4h |
| `--no-context` | 不跑其他級別的對照 |
| `--live` | 納入還沒收盤的 K 棒，結果和圖表當下顯示的一致 |
| `--cache` | 重複使用上次下載的 K 棒，調參數時用 |
| `--json` | 輸出 JSON 格式 |
| `--log` | 把各節點分數寫進 `~/.tv-ta/logs/` |
| `--no-user-config` | 忽略 `~/.tv-ta/config/`，只用內建預設值 |
| `--as-of 2026-09-22T16:00` | 回放過去某個時點（UTC），只用當時已經收盤的 K 棒 |

## 看懂檢核報告

| 區塊 | 內容 |
|---|---|
| 開頭 | 資料來源、最新 K 棒時間、收盤價、套用了哪些設定 |
| SECTION A / B | 對應 `btc.html` 的 19 項和 `o/btc.html` 的 17 項。每一列包含判定（▲ BUY / ─ WAIT / ▼ SELL）、分數（-2 到 +2）、權重、數值證據 |
| GATES | 盤整（CI）和趨勢強度（ADX）兩個開關。權重係數小於 1 時，趨勢類指標的影響力會被調降 |
| FINAL | **檢核表判斷**：和 check.html 相同，BUY 或 SELL 佔 60% 以上才成立。**加權綜合分數**：-2 到 +2，≥ +0.5 為 BUY，≤ -0.5 為 SELL。最終判斷預設採用後者 |
| SECTION E | 事件訊號節點（預設全部關閉）。事件沒發生時顯示「· 未觸發」，不影響分數 |
| 多級別對照 | 1h / 4h / 1d 的分數，看各級別方向是否一致 |
| 操作建議 | BUY / SELL 時給進場區、停損、目標和風報比；WAIT 時只給轉多和轉空的觸發價 |

任何一項有疑問，就看它的證據欄，再對照 [references/checklist.md](references/checklist.md) 裡的規則。

## 一日漲跌題怎麼算

**題目規則**：「Up or Down on 9/23」比較的是 **9/22 12:00 ET** 和 **9/23 12:00 ET** 的 Binance 1 分鐘收盤價。夏令時間的 12:00 ET 是 16:00 UTC，冬令時間是 17:00 UTC，`updown.py` 會自動換算。

**P(Up) = 基準機率 + 技術面修正**
- **基準機率**：依現價和目標價的距離、剩餘時間、近期 1h 波動度計算。題目剛開始時約 50%；越接近結算，現價在目標價哪一邊的影響就越大。
- **技術面修正**：取 4h 和 1d 加權綜合分數的平均，乘以每分 5pp，最多修正 ±10pp。這些數字都在 `profile.toml [updown]`，可以調整。

> 每分 5pp 這個係數**還沒有經過校準**，結果只能當作偏向的參考。

實際回放過的兩題：

| 題目 | 4h 分數 | 1d 分數 | P(Up) | 實際 |
|---|---|---|---|---|
| on Sep 22 | +0.72 | +0.40 | 53.1%，偏 Up | Up ✅ |
| on Sep 23 | +0.43 | +0.79 | 52.9%，偏 Up | Down ❌ |

## 調整自己的權重

**不要改 skill 資料夾裡的 `config/`。** 用 plugin 安裝的 skill，每次更新都會整份替換，改在裡面的設定會被蓋掉。

請把調整寫在 `~/.tv-ta/config/`，只寫想改的欄位就好：

```
~/.tv-ta/
├── config/
│   ├── profile.toml          # 例如 [decision] primary = "checklist"，或 [updown] scale = 3
│   ├── nodes.toml            # [nodes.<id>] 覆寫，套用到所有幣種
│   └── symbols/btc.toml      # [nodes.<id>] 覆寫，只套用到 BTC
└── logs/                     # --log 寫入的紀錄，之後可以拿來校準權重
```

報告開頭的「設定：」那一行，會列出這次實際套用了哪幾層設定。詳細說明請看 [references/node_design.md](references/node_design.md)。

## 結構

```
tv-ta/
├── SKILL.md                  # 給 agent 的執行說明（兩個平台共用）
├── config/                   # 內建預設值（使用者的覆寫放在 ~/.tv-ta/config/）
│   ├── nodes.toml            # 36 個檢核節點加 2 個 gate：規則、門檻、權重
│   ├── profile.toml          # 結論門檻、gate、停損/目標、漲跌題、指標參數、Jev 設定
│   ├── forward_model_v1.json # 學習版的固定係數（有雜湊檢查，被改過就拒絕執行）
│   └── symbols/              # 各幣種的覆寫設定（BTC、ETH 已依回測結果校準）
├── scripts/
│   ├── run.py                # 檢核表分析，也負責載入分層設定
│   ├── updown.py             # 預測市場一日漲跌題
│   ├── forward_log.py        # 每日前瞻紀錄：預測、結算、成績表（給排程的 agent 每天跑一次）
│   ├── forward_model.py      # 固定的「學習版」模型，和原本策略用同一份資料算出兩個答案
│   ├── replay.py             # 回放歷史 K 棒：信心度路由、節點 A/B、一日漲跌題
│   ├── evaluate_node.py      # 用固定通過標準評估節點（事件節點上線前必跑）
│   ├── fetch_ohlcv.py        # 抓 Binance K 線（不需要 API key）
│   ├── indicators.py         # 指標計算，採用 TradingView 公式
│   ├── nodes.py              # 判斷節點的規則
│   ├── decide.py             # 加權合成、gate、交易計畫
│   ├── jev_client.py         # 選配：TypeSafe Jev
│   └── crosscheck.py         # 選配：和 TradingView 數值比對
├── references/
│   ├── checklist.md          # 36 項對照表：CHECK.md 的描述 → 數值定義
│   └── node_design.md        # 分層設定、調參數、用紀錄校準權重、改成問 Jev
├── tests/test_nodes.py       # 合成行情測試
└── tests/test_forward.py     # 前瞻紀錄的離線測試（模型檔、結算、成績表）
```

## 前瞻測試（實驗性）

回測顯示目前的策略在一日漲跌題上略低於 50%（七個幣種各約 47–50%，五年資料）。另一組固定權重的「學習版」（`config/forward_model_v1.json`，部分節點權重為負）在回測中略高，但差距在誤差範圍內。要確認它不是事後挑出來的，必須看還沒發生的日子：`scripts/forward_log.py run` 每天記錄原本策略、學習版和永遠猜 Up 三個答案，結算後計分。詳細規則和給 agent 的執行步驟見 `SKILL.md`。

- 紀錄存在 `~/.tv-ta/forward/forward.csv`；題目開始 3 小時後才記的算「補記」，不計分。
- 累積 180 天前，成績表的數字只是進度，不是結論。
- 這個模型不影響檢核表、BUY/WAIT/SELL 和交易計畫，只在一日漲跌題多顯示一行實驗性的偏向。

## 驗證狀態

- **與 TradingView 比對**：RSI、MACD、BB、KC、Donchian、ATR、ADX/±DI、SMA、EMA、HMA、VWMA、PSAR、Stoch RSI、CCI、UO、MFI、CMF、Aroon、ROC，在 BTC 4h 和 ETH 1d 上誤差都 < 0.1%。
- **tvscreener 沒有提供的指標**：Supertrend、Alligator、Fractals、Zig Zag、Volatility Stop、Linear Regression、MA Cross、MTP、CI、OBV、HV。這幾項用合成的多頭和空頭行情測試過方向，還需要對照圖表人工抽查。
- **初版規則**：CCI 和 UO 的規則 CHECK.md 還沒有定義，目前是初版，請審核。

## 開發與發佈

repo 結構：

```
skills-crypto-watch/
├── .claude-plugin/        # plugin 與 marketplace 設定
├── skills/tv-ta/          # skill 原始碼（只有這一份）
├── .claude/skills/tv-ta   # 本機開發用的連結，不 commit
└── scripts/dev-link.*     # 建立上面那個連結
```

- **本機開發**：clone 之後執行一次 `scripts/dev-link.ps1`（macOS / Linux 用 `scripts/dev-link.sh`）。之後在這個 repo 開 Claude Code，就會直接載入 `skills/` 裡正在修改的版本，改完開新對話就會生效。開發期間，這台電腦不要再用 marketplace 安裝同一個 plugin，否則會有兩份。
- **測試**：`python -m unittest discover -s skills/tv-ta/tests`
- **模擬別人安裝後的樣子**：在 repo 外的目錄執行 `claude --plugin-dir <這個 repo 的路徑>`，skill 名稱會是 `crypto-watch:tv-ta`。
- **發佈**：
  1. 跑測試，並確認 `claude plugin validate --strict .claude-plugin/plugin.json` 通過；
  2. 調高 `.claude-plugin/plugin.json` 的 `version`；
  3. commit 並 push。

  已經安裝的人執行 `claude plugin update crypto-watch@crypto-watch` 就會拿到新版，他們在 `~/.tv-ta/config/` 的調整不受影響。

> 本工具的結果是依規則計算的技術面參考，非投資建議。
