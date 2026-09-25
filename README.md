# skill-crypto-watch

這個 repo 收錄給 [crypto-watch](https://jacobhsu.github.io/crypto-watch/) 使用的 agent skills，Claude Code 和 Hermes 都能直接使用。

| Skill | 說明 |
|---|---|
| [tv-ta](skills/tv-ta/) | 加密貨幣技術分析：用數值計算 28 項技術指標，跑多時間級別檢核表，回答漲跌方向、進場時機和預測市場漲跌題 |

## 安裝

電腦需要有 Python 3.11 以上。

**Claude Code**：在終端機執行

```
claude plugin marketplace add JacobHsu/skills-crypto-watch
claude plugin install crypto-watch@crypto-watch
```

**Hermes 和其他 agent**：

```
npx skills add JacobHsu/skills-crypto-watch --skill tv-ta
```

裝好之後，**開一個新對話**就能使用。

## 試用

直接用平常說話的方式問，例如：

| 你可以問 | 你會得到 |
|---|---|
| 用 tv-ta 回答 BTC 明日漲跌題 | 題目、目標價、現價，以及 Up 的機率和偏向 |
| 分析 BTC 4h | 28 項技術指標的檢核結果、BUY / WAIT / SELL 結論，以及進場、停損、目標價 |
| ETH 現在可以進場嗎？ | 結論和操作建議。如果是 WAIT，會告訴你漲過或跌破哪個價位再看 |
| SOL 日線跑一次檢核表 | 日線的完整檢核結果 |
| 用 tv-ta 回答 BTC 9/23 漲跌題，並對答案 | 用當時看得到的資料作答，再和實際結果比對 |

範例：問「用 tv-ta 回答 BTC 明日漲跌題」，會得到類似這樣的回答：

> **BTC Up or Down on September 24, 2026**
> 目標價 84,018.1，現價 84,120.0（+0.12%），剩 9.6 小時
> **P(Up) 54.7% → 偏 Up**：現價略高於目標價；4h 中性、日線偏多

> 結果是依技術指標計算的參考，不是投資建議。

想了解報告怎麼看、怎麼調整權重，請看 [tv-ta 說明文件](skills/tv-ta/README.md)。想知道每個指標為什麼這樣判斷、決策模型怎麼設計，請看 [tv-ta 設計文件](docs/tv-ta/README.md)。

## 用了哪些技術指標

tv-ta 的檢核項目和 crypto-watch 兩個頁面的 widget 一一對應，位置也一樣。每個指標在 1H、4H、1D 各算一次，得到 -2 到 +2 的分數。`↳` 開頭的是兩個指標的組合判斷，不是獨立 widget。

### 主圖指標頁 [btc.html](https://jacobhsu.github.io/crypto-watch/btc.html)（四欄）

| 第 1 欄 | 第 2 欄 | 第 3 欄 | 第 4 欄 |
|---|---|---|---|
| Multi-Time Period | Bollinger Bands | SMA 20 / 50 | Zig Zag |
| Williams Fractals | Keltner Channel | EMA 20 / 50 | Supertrend |
| Williams Alligator | BB / KC Squeeze | Donchian Channels | Linear Regression |
| ↳ Alligator × Fractal | MA Cross | ↳ MA × EMA 共振 | ↳ SAR × Linear Reg |
| Parabolic SAR | Volatility Stop | | VWMA |

共 19 項：16 個指標 + 3 個組合判斷（↳）。

### 副圖指標頁 [o/btc.html](https://jacobhsu.github.io/crypto-watch/o/btc.html)（四組）

這一頁的 Supertrend、Hull MA、Bollinger Bands、VWMA 在主圖指標頁已經列過，這裡不重複列出。

| 第 1 組：趨勢指標 | 第 2 組：動量指標 | 第 3 組：波動指標 | 第 4 組：量能指標 |
|---|---|---|---|
| MACD | RSI | ATR | On Balance Volume |
| DMI / ADX | Stochastic RSI | Choppiness Index | MFI |
| CCI | Ultimate Oscillator | Historical Volatility | Chaikin Money Flow |
| ↳ Supertrend × MACD | | | |

共 13 項：12 個指標 + 1 個組合判斷（↳）。

### 指標數量

同一個指標出現在兩頁只算一次，組合判斷（↳）不算指標：主圖 16 個 + 副圖 12 個 = **28 個技術指標**。

另有 4 個組合判斷，共 32 項。原本的檢核表把 Supertrend、Hull MA、Bollinger Bands、VWMA 在兩頁各算一次，所以是 36 項。

每個項目的判斷規則和數值定義請看 [檢核表對照](skills/tv-ta/references/checklist.md)。

## 開發

給維護這個 repo 的人：clone 之後執行一次 `scripts/dev-link.ps1`（macOS / Linux 用 `scripts/dev-link.sh`），在這個 repo 開 Claude Code 時就會直接載入正在修改的版本。測試、發佈流程請看 [tv-ta 說明文件](skills/tv-ta/README.md#開發與發佈)。

## 參考

[skills/typesafe-ai](https://github.com/typesafe-ai/skills)  
[tvscreener](https://github.com/deepentropy/tvscreener)
