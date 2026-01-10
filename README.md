# eBay リサーチ支援システム

楽天・Amazonから国内仕入れ先を自動検索し、利益計算を行うリサーチ支援ツール

## 📋 概要

eBayの販売実績・出品情報を起点に、国内仕入れ先（楽天・Amazon）を検索し、利益が出る商品候補を効率的に抽出するシステムです。

**特徴：**
- ✅ **GitHub Actions完結** - Cloud不要、GitHubだけで動く
- ✅ **シンプル** - スプレッドシートにURL貼るだけ
- ✅ **無料** - 月100件処理まで無料
- ✅ **非同期＋ポーリング** - 結果を待って完了通知

---

## 🎯 システム構成

```
Googleスプレッドシート
  ↓ [ボタンクリック]
Google Apps Script
  ↓ GitHub API (repository_dispatch)
GitHub Actions (Python)
  ├─ eBay API: アイテム情報取得
  ├─ Gemini: 日本語クエリ生成
  ├─ 楽天/Amazon: 国内ソーシング
  ├─ 利益計算
  └─ スプレッドシート書き込み
  ↓
GAS (ポーリング)
  ↓ ステータス確認 (5秒×36回=3分)
完了通知
```

---

## 🚀 セットアップ

### 1. GitHubリポジトリ作成

```bash
cd ebaySystem
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USER/ebaySystem.git
git push -u origin main
```

### 2. GitHub Secrets設定

リポジトリ → Settings → Secrets and variables → Actions

以下のSecretsを追加：

```
EBAY_CLIENT_ID
EBAY_CLIENT_SECRET
EBAY_REFRESH_TOKEN
RAKUTEN_APPLICATION_ID
RAKUTEN_AFFILIATE_ID
AMAZON_ACCESS_KEY_ID
AMAZON_SECRET_ACCESS_KEY
AMAZON_PARTNER_TAG
GOOGLE_SERVICE_ACCOUNT_JSON (JSON内容をそのまま)
SHEETS_SPREADSHEET_ID
GEMINI_API_KEY
```

### 3. GitHub Personal Access Token作成

1. GitHub → Settings → Developer settings → Personal access tokens
2. Generate new token (classic)
3. `repo` スコープを選択
4. トークンをコピー

### 4. Google Apps Script設定

1. スプレッドシート → 拡張機能 → Apps Script
2. `docs/GAS_コード.js` の内容を貼り付け
3. `setupProperties()` 関数を編集してトークン設定
4. 実行して設定保存

### 5. 動作確認

1. 「入力シート」のB列にeBay URL貼り付け
2. メニュー「eBayリサーチ」→「この行をリサーチ」
3. 確認ダイアログで「OK」
4. 処理中...（1〜2分待つ）
5. 完了通知 → 結果確認

詳細: [docs/SETUP_GITHUB_ACTIONS.md](docs/SETUP_GITHUB_ACTIONS.md)

---

## 📊 使い方

### 基本的な流れ

1. **eBay URLを入力**
   - 「入力シート」のB列にeBay商品URLを貼り付け

2. **リサーチ実行**
   - その行を選択
   - メニュー「eBayリサーチ」→「この行をリサーチ」
   - 確認ダイアログで「OK」

3. **結果を待つ**
   - 「処理中...」と表示（AF列）
   - 1〜2分で自動完了
   - 完了ダイアログが表示される

4. **結果を確認**
   - O〜T列: 仕入れ先情報（最大3件）
   - V〜W列: eBay価格・送料
   - AB〜AE列: 利益額・利益率
   - AF列: ステータス「要確認」

5. **手動確認・調整**
   - 仕入れ先URLを確認
   - 必要に応じて価格等を修正
   - ステータスを「確定」に変更

---

## 📁 ファイル構成

```
ebaySystem/
├── .github/
│   └── workflows/
│       └── research.yml          # GitHub Actions定義
├── src/
│   ├── ebay_client.py           # eBay API
│   ├── sourcing.py              # 楽天/Amazon検索
│   ├── profit.py                # 利益計算
│   ├── sheets_client.py         # スプレッドシート連携
│   ├── spreadsheet_mapping.py   # カラムマッピング
│   └── github_actions_runner.py # メイン処理
├── config/
│   ├── fee_rules.yaml           # 手数料設定
│   ├── categories.yaml          # カテゴリ設定
│   └── hotwords.yaml            # キーワード設定
├── docs/
│   ├── SETUP_GITHUB_ACTIONS.md  # セットアップ手順
│   ├── GAS_コード.js            # GASコード
│   └── 構成比較.md              # システム構成比較
├── tests/
│   └── ...                      # テストコード
├── requirements.txt
└── README.md
```

---

## 🔧 開発

### ローカルテスト

```bash
# 仮想環境作成
python -m venv .venv
.venv\Scripts\activate

# 依存関係インストール
pip install -r requirements.txt

# .envファイル作成
cp .env.example .env
# .envを編集して環境変数設定

# テスト実行
python -m pytest tests/ -v

# 手動実行
python -m src.github_actions_runner \
  --ebay-url "https://www.ebay.com/itm/123456789" \
  --row 2
```

### コード更新・デプロイ

```bash
# コード修正
vim src/sourcing.py

# コミット
git add .
git commit -m "Update sourcing logic"

# デプロイ（これだけ！）
git push

# 自動的に最新コードが使われる
```

---

## 📈 処理フロー詳細

### 1. eBay情報取得
- eBay APIでアイテム情報取得
- タイトル、価格、送料、カテゴリ等

### 2. 検索クエリ生成
- Gemini APIで英語→日本語翻訳
- 不要語除去（"Authentic", "New"等）

### 3. 国内ソーシング
- 楽天API検索
- Amazon PA-API検索
- 最安値3件を取得

### 4. 利益計算
- 為替レート適用（150円/USD）
- eBay手数料（12% + $0.30）
- 送料（800円）
- 利益額・利益率算出

### 5. スプレッドシート書き込み
- 入力シートの該当行に結果反映
- ステータス「要確認」に更新

---

## 💰 コスト

| サービス | 無料枠 | 月100件処理 | コスト |
|---------|--------|------------|--------|
| GitHub Actions | 2,000分 | 200分 | **無料** |
| 楽天API | 無制限 | - | **無料** |
| Amazon PA-API | 8,640req/日 | 100req | **無料** |
| Gemini API | 50req/日 | 100req | 月$7程度 |

**合計: 月$7程度**

---

## ⚠️ 制限事項

### 同時実行
- GitHub Actionsは同時20ジョブまで
- 連続クリックを避ける

### 処理時間
- 1件あたり1〜2分
- タイムアウト: 10分

### API制限
- 楽天: 特になし
- Amazon: 1秒1リクエスト
- eBay: 5,000リクエスト/日
- Gemini: 50リクエスト/日（無料枠）

---

## 🐛 トラブルシューティング

### 処理が完了しない

1. GitHub → Actions タブでログ確認
2. エラー内容を確認
3. AF列（ステータス）を確認

### GitHub Actions起動しない

1. Personal Access Tokenを確認
2. `testGitHubConnection()` で接続テスト
3. リポジトリ名が正しいか確認

### スプレッドシートに書き込まれない

1. Service Accountのメールアドレスを確認
2. スプレッドシートの共有設定を確認
3. GitHub ActionsのログでGoogleエラー確認

---

## 📚 ドキュメント

- [セットアップ手順](docs/SETUP_GITHUB_ACTIONS.md)
- [GASコード](docs/GAS_コード.js)
- [構成比較](docs/構成比較.md)
- [カラムマッピング](docs/スプレッドシート_カラムマッピング.txt)

---

## 🔮 今後の拡張予定

### フェーズ2
- [ ] 一括処理機能（複数行を一度に）
- [ ] Discord/Slack通知
- [ ] 定期実行（毎日自動リサーチ）

### フェーズ3
- [ ] 英語タイトル自動生成
- [ ] eBay自動出品機能
- [ ] 画像処理・最適化

---

## 📝 ライセンス

MIT License
