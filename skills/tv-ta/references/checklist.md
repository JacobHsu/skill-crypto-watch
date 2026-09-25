# 檢核表 → 節點對照

這份文件列出 crypto-watch `docs/CHECK.md` 的 36 個檢核項目在 tv-ta 裡怎麼判斷。原本「看圖判斷」的描述，在這裡都換成可以計算的數值定義。
要修改判斷規則時，先改 `config/nodes.toml` 裡該節點的 `params`。只有規則邏輯本身要改，才需要動 `scripts/nodes.py`。

- **分數**：-2 到 +2。分數 ≥ +0.5 算 BUY，≤ -0.5 算 SELL，其餘算 WAIT。資料不足時標成「未核對」，不計入分母。
- **K 棒**：預設只使用已收盤的 K 棒。加上 `--live` 會把正在形成的 K 棒也算進去，結果會和圖表當下顯示的一致。
- **指標參數**：採用 TradingView 內建指標的預設值。已用 tvscreener 比對，數值誤差 < 0.1%（詳見下方「驗證」一節）。

## Section A — `{symbol}.html`（19 項）

| # | 節點 id | 項目 | 型別 | 數值定義（圖上描述 → 計算方式） |
|---|---|---|---|---|
| 1 | `mtp` | Multi-Time Period | score | 取較高級別最近 11 根 K（4h 圖對應 1W，對照表在 `profile.toml [timeframes.htf]`）。綠 K 佔 ≥ 60% → +1，≤ 40% → -1，其餘 0 |
| 2 | `fractals` | Williams Fractals | choice | 找最近一個 △ 和最近一個 ▽，檢查之後有沒有 K 棒收盤突破該價位。以較晚發生的突破為準：向上突破 +1、向下突破 -1、都沒有突破 0 |
| 3 | `alligator` | Williams Alligator | choice | 三線最大距離 < 0.3 ATR 視為「睡覺」（0）。綠 > 紅 > 藍且收盤在綠線上方 → 張口向上 +1；反之 -1。三線分開但價格不在外側 → 0 |
| 4 | `alligator_x_fractal` | ↳ Alligator × Fractal | score | 鱷魚張口向上且碎形向上突破 → **+2**（強力訊號）；向下的鏡像 → **-2**；其他組合 → 0 |
| 5 | `psar` | Parabolic SAR | score | SAR 在 K 線下方 → +1，在上方 → -1。3 根內剛翻轉的會在證據中註明 |
| 6 | `bb` | Bollinger Bands | score | 「觸碰上軌」＝最高價 ≥ 上軌，且收盤在中軌上方。「帶寬擴大」＝帶寬大於 3 根前。觸上軌＋擴大 → +1；觸上軌＋收窄 → 0；下軌為鏡像 |
| 7 | `kc` | Keltner Channel | score | 「站穩」＝連續 2 根（`hold_bars`）收在上軌外 → +1；收在下軌外 → -1 |
| 8 | `squeeze` | BB / KC Squeeze | choice | BB 完全落在 KC 內 → Squeeze ON（0）。Squeeze OFF 時，收盤高於 KC 上軌 → +1，低於 KC 下軌 → -1 |
| 9 | `ma_cross` | MA Cross | score | SMA9 與 SMA21 在 3 根內（`lookback`）金叉 → +1，死叉 → -1，沒有交叉 → 0 |
| 10 | `vol_stop` | Volatility Stop | score | 停損線在 K 線下方（綠）→ +1，在上方（紅）→ -1 |
| 11 | `ma_20_50` | MA 20 / 50 | choice | 收盤 > MA20 > MA50 → +1；MA50 > MA20 > 收盤 → -1；其他排列 → 0 |
| 12 | `ema_20_50` | EMA 20 / 50 | choice | 同上，改用 EMA |
| 13 | `donchian` | Donchian Channels | score | 收盤高於**前一根**的上軌（創 20 根新高）→ +1；低於前一根的下軌 → -1；在通道內 → 0 |
| 14 | `ma_x_ema` | ↳ MA × EMA 共振 | score | MA 和 EMA 兩個節點同為多方 → +1，同為空方 → -1，否則 0 |
| 15 | `zigzag` | Zig Zag | choice | 用偏差 5%、深度 10 的轉折點，比較最近兩個高點和兩個低點。高低點都遞升 → +1；都遞降 → -1；其他 → 0。轉折點不足時標為未核對 |
| 16 | `supertrend` | Supertrend | score | 收盤在線上方 → +1，在下方 → -1（只看位置，不看顏色） |
| 17 | `linreg` | Linear Regression | score | 以 100 根做迴歸。斜率在整段累計不到 ±2% 視為「近乎水平」。收盤距迴歸線 < 0.5 個殘差標準差視為「在線附近」。在線上方＋向上 → +1，在線下方＋向下 → -1，其他 → 0 |
| 18 | `sar_x_linreg` | ↳ SAR × Linear Reg | score | SAR 在下方且迴歸線向上（非水平）→ +1；鏡像 → -1；其他 → 0 |
| 19 | `vwma` | VWMA | score | 收盤在 VWMA 上方，且 VWMA 高於 3 根前 → +1；鏡像 → -1。收盤距 VWMA < 0.25 ATR 視為「貼近」→ 0 |

## Section B — `o/{symbol}.html`（17 項）

o/ 頁面的第一組第四格已從 Aroon 換成 CCI，第二組第四格已從 ROC 換成 Ultimate Oscillator，但 check.html 還是舊版。
所以 `aroon`、`roc` 仍保留在設定檔裡，只是 `enabled = false`。**CCI 和 UO 的規則 CHECK.md 還沒有定義，目前是初版，請審核。**

| # | 節點 id | 項目 | 型別 | 數值定義 |
|---|---|---|---|---|
| 1 | `supertrend_b` | Supertrend | score | 同 A-16 |
| 2 | `macd` | MACD | score | 柱狀為正值，且連續 2 根增高 → +1；為負值且連續 2 根加深 → -1；其他 → 0 |
| 3 | `dmi` | DMI / ADX | score | ADX ≤ 25 → 0。ADX > 25 時，+DI > −DI → `bull_score`（預設 +1），反之 → `bear_score`（預設 -1） |
| 4 | `cci` | CCI（初版） | score | CCI > 100 且走高 → `above_score`（預設 +1）；< -100 且走低 → `below_score`（預設 -1）；其他 → 0 |
| 5 | `supertrend_x_macd` | ↳ Supertrend × MACD | score | K 線在 Supertrend 上方且 MACD 柱為正值 → +1；鏡像 → -1；其他 → 0（只看柱的正負，不看是否持續增高） |
| 6 | `hma` | Hull MA | score | 收盤在 HMA 上方，且 HMA 高於 2 根前 → +1；鏡像 → -1 |
| 7 | `rsi` | RSI | score | 55–70 → +1；45–55 → 0；30–45 → -1。> 70 時：價格與 RSI 在近 3 根內同步創 20 根新高 → +1；只有價格創新高 → -1（頂背離）；兩者都沒創新高 → `overbought_score`。< 30 為鏡像 |
| 8 | `stoch_rsi` | Stochastic RSI | score | 2 根內在 20 以下 %K 上穿 %D → +1；在 80 以上 %K 下穿 %D → -1；其他 → 0 |
| 9 | `uo` | Ultimate Oscillator（初版） | score | > 70 → `overbought_score`（預設 -1）；< 30 → `oversold_score`（預設 +1）；> 50 且走高 → +1；< 50 且走低 → -1 |
| 10 | `bb_b` | Bollinger Bands | score | 同 A-6 |
| 11 | `atr` | ATR | score | ATR 比 3 根前增加 3% 以上時，依同期間價格方向給 ±1；否則 → 0 |
| 12 | `choppiness` | Choppiness Index | score | CI < 38.2 時：收盤在布林帶上半部的上半段 → +1，在下半部的下半段 → -1。CI ≥ 38.2 → 0 |
| 13 | `hv` | Historical Volatility | score | 以近 100 根的百分位判斷高低：< 30 為低檔，> 70 為高檔。低檔且翻揚時，若收盤突破布林上軌或 Donchian 上軌 → +1，下方鏡像 → -1。其他情況 → 0 |
| 14 | `vwma_b` | VWMA | score | 同 A-19 |
| 15 | `obv` | OBV | score | 比較 10 根內 OBV 和價格的方向：同步上升 → +1，同步下降 → -1，方向相反 → 0（背離） |
| 16 | `mfi` | MFI | score | > 80 且價格創新高但 MFI 沒有 → -1（頂背離）；45–55 → 0；> 50 且走高 → +1；< 50 且走低 → -1 |
| 17 | `cmf` | CMF | score | 絕對值 < 0.05 視為貼近零軸 → 0；正值且高於 2 根前 → +1；負值且低於 2 根前 → -1 |

## Gate（Noul 節點，不參與投票）

| 節點 id | 定義 | 作用（`profile.toml [[gate]]`） |
|---|---|---|
| `trend_established` | ADX 從 20 到 30 線性對應 0 到 1（門檻 25 ± 5） | 趨勢不成立時，trend 和 ma 群組的權重最低降到 ×0.75 |
| `choppy` | CI 從 58.8 到 64.8 線性對應 0 到 1（門檻 61.8 ± 3） | 盤整時，trend、ma、structure 群組的權重最低降到 ×0.5。這條對應 CHECK.md「CI > 61.8 時趨勢型指標要調降權重」 |

## 最終判斷

- **檢核表判斷**：和 check.html 相同。在已核對的項目中，BUY 佔 ≥ 60% → BUY，SELL 佔 ≥ 60% → SELL，其餘 → WAIT。
- **加權綜合分數**：Σ(分數 × 權重 × gate 係數) ÷ Σ(權重 × gate 係數)，範圍 -2 到 +2。≥ +0.5 → BUY，≤ -0.5 → SELL。
- 最終判斷預設採用加權綜合分數，可以用 `profile.toml` 的 `primary` 切換。兩種判斷都會顯示，結論不同時報告會標出來。

## 驗證

`scripts/crosscheck.py` 會把下列指標和 TradingView 的數值直接比對：RSI、MACD、BB、KC、Donchian、ATR、ADX/±DI、SMA、EMA、HMA、VWMA、PSAR、StochRSI、CCI、UO、MFI、CMF、Aroon、ROC。
在 BTC 4h 和 ETH 1d 上，所有欄位的誤差都 < 0.1%。
Supertrend、Alligator、Fractals、Zig Zag、Volatility Stop、Linear Regression、CI、OBV、HV 無法從 tvscreener 取得，這幾項用 `tests/` 的合成行情驗證方向是否正確，再請對照圖表人工抽查。
