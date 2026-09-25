# 節點設計與調參

tv-ta 的決策模型借用 TypeSafe Jev 的設計理念：

1. **一個節點只做一個判斷**。每個檢核項目各自獨立作答，不看其他項目的結果。組合項（↳）例外，它會讀取組合對象已經算好的答案。
2. **答案有型別**：
   - **Score**（-2 到 +2）：大多數項目用這個型別。
   - **Choice**（類別）：用於結構分類，例如鱷魚是睡覺、張口向上還是張口向下，分類後再對應成分數。
   - **Noul**（0 到 1 的機率）：用於 gate，表示「某個條件成立嗎」。
3. **權重和政策放在設定檔，不寫在判斷裡**（composite scoring）。調整權重或 gate 只會重新加權已經算好的答案，不必重新判斷；加上 `--cache` 連行情都不用重新下載。

## 設定放在哪裡

設定分成好幾層，後面的層會覆寫前面的層：

| 層 | 位置 | 誰來改 |
|---|---|---|
| 1. 預設定義 | skill 內的 `config/nodes.toml`、`config/profile.toml` | skill 維護者（在原始碼 repo 裡改） |
| 2. 內建幣種校準 | skill 內的 `config/symbols/<sym>.toml` | skill 維護者 |
| 3. 使用者 profile | `~/.tv-ta/config/profile.toml` | 使用者 |
| 4. 使用者節點覆寫 | `~/.tv-ta/config/nodes.toml`（寫法同 `[nodes.<id>]`，套用到所有幣種） | 使用者 |
| 5. 使用者幣種覆寫 | `~/.tv-ta/config/symbols/<sym>.toml` | 使用者 |

**用 plugin 安裝的使用者，只改第 3 到 5 層。** plugin 更新時會整份替換 skill 資料夾，改在 skill 內的設定會被蓋掉；放在 `~/.tv-ta/` 的設定不受影響。

- 使用者這幾層的檔案都是「補丁」：只寫想改的欄位就好，不用複製整份。
  - 表格（例如 `[decision]`、`params`）會逐鍵合併；陣列（例如 `[[gate]]`）則會整個替換。
- 使用者設定的位置可以用環境變數 `TV_TA_CONFIG` 改；`TV_TA_HOME` 會同時改變設定和 log 的根目錄。
- 報告開頭的「設定：」那一行，會列出這次實際套用了哪幾層。
- 加上 `--no-user-config` 可以只跑內建預設值，方便比較改動前後的差異。

例如，只想讓 BTC 更看重 Supertrend，建立 `~/.tv-ta/config/symbols/btc.toml`：

```toml
[nodes.supertrend]
weight = 2.0
```

## 常見的調整

下表的「修改的位置」是指預設定義所在的檔案。用 plugin 安裝的使用者，請把同樣的欄位寫進上面第 3 到 5 層對應的檔案。

| 想做的事 | 修改的位置 |
|---|---|
| 某個指標更重要或更不重要 | 在 `config/nodes.toml` 改該節點的 `weight`。設成 0 時，這一列照樣顯示，但不參與投票 |
| 調整判斷門檻 | 改該節點的 `params`，例如 `rsi` 的 `neutral_band`、`dmi` 的 `adx_min` |
| 只對某個幣種調整 | 在 `config/symbols/<sym>.toml` 加 `[nodes.<id>]`，可以覆寫 `weight`、`params` 或 `enabled` |
| 盤整時減少趨勢類指標的影響 | 在 `config/profile.toml` 的 `[[gate]]` 改 `floor`（越低影響越大）或 `groups` |
| 改變 BUY / SELL 的門檻 | 在 `profile.toml [decision]` 改 `buy_above`、`sell_below` 或 `checklist_majority` |
| 改用 check.html 的計數法當作主結論 | 在 `profile.toml [decision]` 設定 `primary = "checklist"` |
| 調整停損與目標 | 在 `profile.toml [plan]` 改 `stop_atr_min`、`stop_atr_max` 或 `target_rr` |
| 調整指標參數（例如 Zig Zag 的偏差） | 在 `profile.toml [indicators]` 修改。注意改了之後，和圖表上顯示的指標就不一致了 |

改完後重新執行：

```
python scripts/run.py BTC --tf 4h --cache
```

比對改動前後的「加權綜合分數」和最終判斷。一次只改一個地方。

## 用紀錄校準權重

每次執行時加上 `--log`，會在 `~/.tv-ta/logs/YYYY-MM.jsonl` 寫入一行紀錄（可用 `TV_TA_LOG_DIR` 改位置），內容包含時間、價格、各節點分數和 gate 的值。
累積一段時間後，把每筆紀錄和之後的實際漲跌對照，就能算出每個節點的命中率和 edge（勝率減去基準勝率）。
`config/symbols/btc.toml` 和 `eth.toml` 的初始值，就是用這種方式從 py-tvscreener 的回測結果得來的：
- edge 明顯為正的節點，提高權重；
- edge 接近 0 的節點，權重減半；
- 只有單一方向有效的節點（例如 BTC 的超賣反彈沒有 edge），用 `*_score` 參數關掉無效的那一邊。

## 把節點改成詢問 Jev（選配）

有些節點用數值規則很難判斷，例如 Zig Zag 結構是否明確、背離算不算成立。這類節點可以改成詢問 TypeSafe Jev：

1. 在 `nodes.toml` 為該節點加上 `jev` 設定：

   ```toml
   jev = { instructions = "Given the swing pivots in `zigzag`, how clearly do they form a trending structure?",
           criteria = ["Clear lower highs and lower lows", "Mostly descending", "No clear order",
                       "Mostly ascending", "Clear higher highs and higher lows"] }
   ```

   - `criteria` 是陣列時，問題型別為 **Score**。等級必須從強烈看空排到強烈看多，回傳值會線性對應到 -2 到 +2。
   - `criteria` 是物件（`{選項 = 說明}`）時，問題型別為 **Choice**。這時要另外加 `choice_scores = { 選項 = 分數 }`，指定每個選項對應的分數。
   - 不寫 `criteria` 時，問題型別為 **Noul**，只適合 gate 節點。
   - 問題型別不必和節點原本的型別相同。例如 Zig Zag 在 code 規則裡是 choice，改問 Jev 時可以用 score。
2. 在 `profile.toml` 設定 `[jev] enabled = true`，並設定環境變數 `TYPESAFE_API_KEY`。

所有 Jev 節點會合併成一次請求送出。送出的 state 是所有節點的證據和數值，以節點 id 當鍵，
所以 `instructions` 裡可以用反引號引用，例如 `` `zigzag.pivots` ``、`` `rsi.rsi_prev` ``。

請求失敗、沒有 API key，或回答格式不符時，會自動改用原本的 code 規則，並在報告中顯示警告。
報告裡由 Jev 判斷的列會標上「（Jev）」，證據欄同時列出 Jev 的判斷和規則原本的判斷，方便比較。

TypeSafe 官方的建議是：數值門檻這類能直接計算的規則，留在 code 裡判斷；Jev 用來處理難以用數值定義的語意判斷。

## 新增節點

1. 在 `scripts/nodes.py` 寫一個規則函式 `r_xxx(ctx, params, results)`，回傳 `Result(score, evidence, ...)`，並登記到 `RULES`。
   `ctx` 裡已經算好所有指標序列；如果需要新的指標，在 `Context.__init__` 裡加上計算。
2. 在 `config/nodes.toml` 加一個 `[[node]]`。設定 `section = "A"` 或 `"B"`，這個節點就會出現在表格中並參與投票；設定 `section = "gate"` 則不投票，只當 gate 使用。
3. 在 `tests/test_nodes.py` 加一個合成行情測試，確認明顯的多頭會判成多方、明顯的空頭會判成空方。
