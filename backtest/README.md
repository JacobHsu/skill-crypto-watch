# backtest

回測幣安 BTC、ETH 每日漲跌題（Binance「1天內漲或跌」）的工具，給維護這個 repo 的人用。

**這個資料夾不會被下載。** 用 `npx skills add` 或 plugin 安裝的人只會拿到 `skills/tv-ta/`。這裡的工具會直接重用那個 skill 裡的 `run.py`、`updown.py`、`replay.py`，所以要在完整的 repo 裡執行。

需要 Python 3.11 以上，只用標準函式庫。**下面的指令都在 repo 根目錄執行。**

| 檔案 | 做什麼 |
|---|---|
| `updown_dataset.py` | 建立歷史題目資料集（每天一題：目標價、結算價、結果） |
| `updown_heatmap.py` | 把資料集畫成日曆熱圖，輸出根目錄的 `index.html` |
| `backtest_updown.py` | 在題目的多個時點（剩 24 / 16 / 8 / 2 小時）回測，看技術面有沒有幫助 |
| `tune_server.py` | 本機調參頁面：看流程圖、改數值、按鈕跑回測 |
| `tune_core.py` | 調參頁面背後的邏輯（檢查補丁、套用、回測），不用直接執行 |
| `skill_path.py` | 讓這裡的程式找得到 `skills/tv-ta/scripts/` |
| `assets/` | 調參頁面 `tune.html`、熱圖用的幣種圖示 |
| `tests/` | 這個資料夾的測試 |

## 快速開始

```
python backtest/updown_dataset.py BTC ETH     # 1. 建資料集，寫到 data/updown/
python backtest/updown_heatmap.py             # 2. 產生 index.html，雙擊就能看
python backtest/tune_server.py                # 3. 開調參頁面 http://127.0.0.1:8765/
```

- **調參頁面（`assets/tune.html`）的入口是網址 `http://127.0.0.1:8765/`**，要先執行第 3 步啟動伺服器，再用瀏覽器打開這個網址（預設會自動開啟）。
- **不能直接雙擊 `tune.html`，也不能用 VS Code 的 Live Server（`127.0.0.1:5500`）開**：它要向 `tune_server.py` 要節點設定、流程圖和回測結果，Live Server 這類只提供檔案的伺服器沒有這些 API，會看到空白頁和一條說明。
- 熱圖 `index.html` 相反，是獨立的單一檔案，可以直接雙擊開。
- 伺服器用 Ctrl+C 結束。

## 1. 資料集 `updown_dataset.py`

```
python backtest/updown_dataset.py BTC ETH --days 730
```

- 一題一列，存成 `data/updown/btc.csv`、`eth.csv`。欄位：`symbol, question_date, start_utc, settle_utc, start_tw, settle_tw, target, settle_price, result, return_pct`。
- `--days` 是往回推多少題（預設 730）。**重跑只會補缺的日子**，所以每天跑一次就能追加最新一題。
- 價格是 Binance 1 分鐘 K 線收盤價，和題目規定的一致。
- 時間：`question_date` 是題目名稱「on 9/25」的日期（美東結算日）。夏令時間是台灣 00:00 到隔天 00:00，冬令時間是 01:00 到隔天 01:00。結算價等於目標價算 Down。
- **冬天的題目是否仍然照美東 12:00 結算還沒核對**，目前只驗證過夏令時間段。

## 2. 熱圖 `updown_heatmap.py`

```
python backtest/updown_heatmap.py
```

- 讀 `data/updown/*.csv`，輸出 repo 根目錄的 `index.html`（單一檔案，本機直接開）。
- 一格一天，綠漲紅跌，顏色深淺依漲跌幅；右上角切換 BTC / ETH 和「近 12 個月 / 近 2 年」；滑鼠移到格子上看目標價、結算價和台灣時間。
- 要用網址看的話，到 GitHub 的 Settings → Pages 選 `main` 分支、`/ (root)`，網址是 `https://jacobhsu.github.io/skill-crypto-watch/`。
- 新增幣種：把 PNG 放進 `assets/coins/`，檔名用小寫幣名（例如 `sol.png`）。沒有圖檔會退回彩色字母徽章。

## 3. 多時點回測 `backtest_updown.py`

```
python backtest/backtest_updown.py BTC --days 700
python backtest/backtest_updown.py BTC --days 300 --hours-left 24,16,8,2 --json
```

在每一題的幾個時點各作答一次，比較三種答案和結算結果：

| 答案 | 意思 |
|---|---|
| 只看價格距離 | 現價離目標價多遠、還剩多少時間（隨機漫步機率） |
| 只看技術面 | 三個級別綜合分數平均的正負號 |
| 價格 + 技術面 | 兩者相加，和 `updown.py` 實際回答的一樣 |

**怎麼看**：「價格 + 技術面」減「只看價格距離」就是技術面的貢獻。目前 BTC 699 題的結果是接近 0：越接近結算命中率越高，但提升都來自現價已經在目標價哪一邊，不是技術面。完整結果在 [docs/tv-ta/07-updown.md](../docs/tv-ta/07-updown.md)。

限制：起訖價用 1h 開盤價近似，不是 1 分鐘收盤價。700 題約要 3 分鐘。

## 4. 調參頁面 `tune_server.py`

```
python backtest/tune_server.py                 # 預設 http://127.0.0.1:8765/
python backtest/tune_server.py --port 9000 --no-open
```

啟動後用瀏覽器打開 `http://127.0.0.1:8765/`（加了 `--no-open` 就要自己開；換了 `--port` 網址裡的埠也要跟著換）。頁面本身是 `assets/tune.html`，由伺服器提供，**不要直接雙擊它**。第一次使用要先建好資料集（第 1 節），否則伺服器會提示找不到資料。

- **流程圖**：把 [docs/tv-ta/04、05](../docs/tv-ta/) 裡每個節點的決策流程圖畫出來。**圖裡黃色的數字可以直接改**，沒標黃的是固定值。同一個參數出現在好幾處會一起變。
- **上方可以改**：每個節點的啟用和權重、gate 的 `floor`。圖上沒出現的參數列在圖下方。
- **跑回測**：用資料集最近 N 題（100 / 200 / 365 / 730），只看開題當下、只用當時已收盤的 K 線，比較「三個級別綜合分數平均的正負號」和結算結果，並排顯示內建設定和調整後。200 題約 25 秒，第一次要下載行情。
- **複製 TOML 補丁**：產生可放進 `~/.tv-ta/config/symbols/<幣種>.toml` 的內容，用在真正的分析上。頁面本身不會寫任何設定檔。

**怎麼看結果**
- 看「方向翻轉 N 題」：小幅調整常常一題都不翻，命中率就完全不變。
- 看誤差範圍：200 題的 95% 信賴區間約 ±7 個百分點，差距比這小就不算有改善。
- **換一段題目再驗證**：只在同一批題目上調到變好，很可能只是配合這批資料。
- 比較的基準是內建設定（含內建幣種校準），不含你 `~/.tv-ta/` 裡的調整。

**限制**
- 流程圖用 mermaid 畫，頁面從 jsdelivr 載入；沒有網路時退回表單，功能還在。
- 8 個事件節點、2 個 gate、`aroon`、`roc` 沒有流程圖，只有表單。
- 只監聽 127.0.0.1，會擋掉不是本機的 Host 標頭；同一時間只能跑一個回測。
- `buy_above` / `sell_below`、指標長度、`[updown]` 的 `scale` 只能改 `profile.toml`，本頁沒有提供。

## 測試

```
python -m unittest discover -s backtest/tests
python -m unittest discover -s skills/tv-ta/tests     # skill 本身的測試
```

不需要網路，也不需要資料集。

更多設計說明：[docs/tv-ta/07-updown.md](../docs/tv-ta/07-updown.md)（一日漲跌題和回測結果）、[08-config-and-calibration.md](../docs/tv-ta/08-config-and-calibration.md)（設定分層和調參）。
