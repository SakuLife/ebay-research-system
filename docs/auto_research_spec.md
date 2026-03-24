# Auto Research パイプライン仕様書

最終更新: 2026-03-25

## 目標

| 指標 | 目標値 |
|---|---|
| SerpAPIクレジット/月 | 5,000 |
| 出力件数/月 | 500件 |
| 出品OK率 | 70% → 350出品/月 |
| **クレジット単価** | **10 credits/output** |

---

## パイプライン全体フロー

```
キーワード入力（スプレッドシート）
  ↓
[1] eBay Sold検索（SerpAPI × 1 credit）
    → 最大120件/ページ、最大4ページ = 480件
  ↓
[2] 各アイテムの仕入先検索（5ステップ）
    Step 1: Gemini翻訳（無料）
    Step 2: 楽天API検索（無料）
    Step 2.5: DuckDuckGo事前スクリーニング（無料）
    Step 2.8: Vision+楽天再検索（無料）
    Step 3: Web検索 EN（SerpAPI × 1 credit）
    Step 4: Google Lens（現在無効化）
  ↓
[3] 在庫チェック（Scrape、無料）
  ↓
[4] Gemini検証（同一商品か判定、無料）
  ↓
[5] 利益計算 → スプレッドシート出力
```

---

## SerpAPIクレジット消費

### メソッド別コスト（各1 credit/call）

| メソッド | 用途 | 現在の状態 |
|---|---|---|
| `search_sold_items()` | eBay過去売却品検索 | **有効** |
| `search_google_web_jp()` | Web(EN)仕入先検索 | **有効** |
| `search_by_image()` | Google Lens画像検索 | **無効化**（採用率0%） |
| `search_amazon_jp()` | Amazon検索 | 未使用 |
| `search_google_shopping_jp()` | Google Shopping | 未使用 |

### アイテムあたりのSerpAPI消費

| パス | 消費 | 発生条件 |
|---|---|---|
| 楽天API成功 | **0** | 楽天で類似度40%以上の候補発見 |
| Vision+楽天成功 | **0** | Gemini Visionで商品特定→楽天再検索成功 |
| DuckDuckGo SKIP判定 | **0** | 仕入困難と判定 |
| 低成功率カテゴリ | **0** | vintage, lolita等のカテゴリ |
| キャッシュヒット | **0** | 類似タイトル75%以上で既処理 |
| **Web(EN)成功** | **1** | Web検索で類似度30%以上発見 |
| **Web(EN)失敗** | **1** | Web検索で見つからず（Lensは無効化済み） |

**平均消費: 0.3〜0.5 credits/アイテム**（Lens無効化後）

### キーワードあたりのSerpAPI消費（試算）

```
eBay Sold検索:  1 credit × 1〜4ページ = 1〜4 credits
仕入先検索:     0.4 credit × 120件     = 48 credits（概算）
────────────────────────────────────────────────
合計:           約50 credits/keyword
```

---

## フィルタ一覧

### 1. Limitedフィルタ（緩和済み）

**除外対象（入手困難なもののみ）：**
- 一番くじ系: `ichiban kuji`, `lottery prize`, `last one prize`, `prize figure`
- 一番くじ日本語: `一番くじ`, `A賞`〜`H賞`, `ラストワン賞`
- プロモカード: `promo card`, `promotional card`

**除外しない（以前は除外していた）：**
- `limited`, `limited edition`, `exclusive`, `collector`, `premium`
- `special edition`, `deluxe edition`, `bonus`, `first edition`

**ホワイトリスト:** `tomica limited vintage`, `tomica limited`

### 2. Graded品フィルタ（New条件のみ）

`psa10`, `psa 10`, `bgs10`, `cgc10`, `graded`, `鑑定済`

### 3. Card/TCG自動除外（eBay検索クエリに付与）

```
-card -cards -tcg -ccg -kuji -lottery -prize
-PSA -BGS -CGC -graded -promo -promotional
-used -junk -set -bundle -lot -combo -complete
```

### 4. 低成功率カテゴリ自動スキップ

以下のキーワードを含むeBayタイトルはSerpAPI検索をスキップ（楽天のみ）：

- 日本アパレル: `liz lisa`, `angelic pretty`, `lolita`, `harajuku`
- ヴィンテージ: `vintage`, `retro`, `antique`, `deadstock`
- レコード: `vinyl`, `lp record`
- ジュエリー: `handmade jewelry`, `one of a kind`
- Zippo: `zippo lighter`
- 香水: `fragrance`, `parfum`
- 車パーツ: `genuine oem`, `jdm parts`

### 5. Used/中古品フィルタ（New条件のみ）

タイトル・コンディション欄に以下を含むとスキップ:
`used`, `pre-owned`, `refurbished`, `open box`, `like new`

---

## eBay検索パラメータ

| パラメータ | 値 | 説明 |
|---|---|---|
| `_ipg` | 最大240 | 1ページあたりの取得件数 |
| `search_buffer` | `items_per_keyword * 20`（上限120） | 実際のリクエスト件数 |
| ページ数 | 最大4ページ | 目標未達時に自動追加 |
| 次ページ取得条件 | 残り≤5件 AND 目標未達 AND 1ページ目≥10件 | |
| `LH_Sold=1` | - | 売却済みのみ |
| `LH_Complete=1` | - | 完了リスティングのみ |
| `LH_ItemCondition` | `1000`(New) / `1500|2500|3000`(Used) | 状態フィルタ |

---

## 設定値（スプレッドシートから取得）

| 設定 | デフォルト | 説明 |
|---|---|---|
| `items_per_keyword` | 5 | キーワードあたりの出力目標 |
| `min_price` | $100 | eBay最低価格フィルタ |
| `min_sold` | 2 | キーワード全体の最低販売実績 |
| `min_profit` | JPY 5,000 | 最低利益額 |
| `market` | UK | eBayマーケット |
| `condition` | New | 商品状態 |

---

## 節約メカニズム

| メカニズム | 節約効果 | 説明 |
|---|---|---|
| **楽天API** | SerpAPI 0 | 無料API、最優先で使用 |
| **Vision+楽天** | SerpAPI 0 | Gemini画像分析→楽天再検索（無料） |
| **DuckDuckGo prescreen** | 最大2 credits/item | 無料Web検索で仕入困難判定 |
| **低成功率カテゴリ除外** | 最大1 credit/item | Web検索をスキップ |
| **キャッシュ** | SerpAPI 0 | 類似タイトル・画像hash・仕入先結果を再利用 |
| **Google Lens無効化** | 1 credit/item | 採用率0%のため停止 |
| **Web(EN)成功→Lens不要** | 1 credit/item | ENで見つかればLens実行しない |

---

## クレジット試算（月間目標 500 output）

### 前提
- 出力採用率: 5%（100アイテム処理 → 5件出力）
- SerpAPI消費: 0.4 credits/item average（Lens無効化後）
- 1ページあたりeBayアイテム: 100件（実効）

### 計算

```
目標出力:        500件
必要処理数:      500 / 5% = 10,000件
必要キーワード:  10,000 / 100件 = 100キーワード

eBay Sold検索:   100 × 1.5ページ平均 = 150 credits
仕入先検索:      10,000 × 0.4         = 4,000 credits
────────────────────────────────────────────────
合計:            約4,150 credits → 5,000以内 ✓
```

### 感度分析

| 採用率 | 必要処理数 | SerpAPI消費 | 5000内? |
|---|---|---|---|
| 3% | 16,667件 | 6,817 | NG |
| **5%** | **10,000件** | **4,150** | **OK** |
| 7% | 7,143件 | 3,007 | OK |
| 10% | 5,000件 | 2,150 | OK |

**採用率5%以上が必須。** 現状1-2%なので改善が必要。

---

## 採用率改善のキーレバー

| 施策 | 期待効果 | 状態 |
|---|---|---|
| Limitedフィルタ緩和 | +30%の候補が処理対象に | **実装済み** |
| eBay取得件数拡大 | より多くの候補を処理 | **実装済み** |
| Google Lens無効化 | コスト43%削減 | **実装済み** |
| 在庫切れ候補の扱い改善 | 在庫あり店舗を優先的に検索 | 未着手 |
| Gemini検証の緩和 | 同一商品判定を少し甘くする | 未着手 |
| キーワード選定ガイド | 楽天で見つかりやすいキーワード推奨 | 未着手 |

---

## キーワード選定ガイド（推奨）

### 向いているキーワード
- 日本メーカーの量産品: `Bandai`, `Takara Tomy`, `Kaiyodo`
- 日本ECで扱いがある商品: フィギュア、文具、食品、工芸品
- 具体的なブランド×カテゴリ: `Sanrio figure`, `Studio Ghibli`

### 向いていないキーワード
- 形容詞・抽象語: `Exquisite`, `Beautiful`, `Rare`
- 一品物・ヴィンテージ: `Vintage Kimono`, `Antique Pottery`
- 海外ブランド: `Ferrari`, `Baccarat`（日本ECに在庫なし）

---

## 実行環境

| 項目 | 値 |
|---|---|
| 実行場所 | GitHub Actions |
| 最大実行時間 | 6時間 |
| Python | 3.12 |
| トリガー | GASからrepository_dispatch |
