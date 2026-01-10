/**
 * eBayリサーチシステム - Google Apps Script
 *
 * GitHub Actionsをトリガーして、結果をポーリングで待つ
 */

// ========================================
// 初期設定（一度だけ実行）
// ========================================

function setupProperties() {
  const props = PropertiesService.getScriptProperties();

  // ⚠️ 以下の値を実際の値に置き換えてください
  props.setProperty('GITHUB_TOKEN', 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN');
  props.setProperty('GITHUB_REPO', 'YOUR_USERNAME/ebaySystem');

  Logger.log('✓ 設定完了');
  Logger.log('GITHUB_REPO: ' + props.getProperty('GITHUB_REPO'));
}

// ========================================
// メニュー追加
// ========================================

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('🔍 eBayリサーチ')
    .addItem('この行をリサーチ', 'onResearchButtonClick')
    .addSeparator()
    .addItem('⚙️ 初期設定', 'setupProperties')
    .addToUi();
}

// ========================================
// メイン処理
// ========================================

function onResearchButtonClick() {
  const sheet = SpreadsheetApp.getActiveSheet();
  const row = sheet.getActiveCell().getRow();

  // シート名チェック
  if (sheet.getName() !== '入力シート') {
    Browser.msgBox(
      '❌ エラー',
      '「入力シート」で実行してください。',
      Browser.Buttons.OK
    );
    return;
  }

  // ヘッダー行チェック
  if (row === 1) {
    Browser.msgBox(
      '❌ エラー',
      'ヘッダー行は処理できません。\nデータ行を選択してください。',
      Browser.Buttons.OK
    );
    return;
  }

  // B列（eBay URL）取得
  const ebayUrl = sheet.getRange(row, 2).getValue();

  if (!ebayUrl || ebayUrl.toString().trim() === '') {
    Browser.msgBox(
      '❌ エラー',
      'B列にeBay URLを入力してください。',
      Browser.Buttons.OK
    );
    return;
  }

  // ステータス確認
  const currentStatus = sheet.getRange(row, 32).getValue();
  if (currentStatus === '処理中...') {
    Browser.msgBox(
      '⚠️ 警告',
      'この行は既に処理中です。',
      Browser.Buttons.OK
    );
    return;
  }

  // 確認ダイアログ
  const response = Browser.msgBox(
    '確認',
    `行${row}をリサーチしますか？\n\neBay URL: ${ebayUrl}\n\n処理時間: 約1〜2分`,
    Browser.Buttons.OK_CANCEL
  );

  if (response !== Browser.Buttons.OK) {
    return;
  }

  // 処理開始
  try {
    // ステータス更新
    sheet.getRange(row, 32).setValue('処理中...');
    SpreadsheetApp.flush();

    // GitHub Actionsトリガー
    const triggered = triggerGitHubActions(ebayUrl, row);

    if (!triggered) {
      sheet.getRange(row, 32).setValue('エラー');
      Browser.msgBox(
        '❌ エラー',
        'GitHub Actionsの起動に失敗しました。\n設定を確認してください。',
        Browser.Buttons.OK
      );
      return;
    }

    // 結果を待つ（ポーリング）
    const completed = waitForCompletion(sheet, row);

    if (completed) {
      const finalStatus = sheet.getRange(row, 32).getValue();

      if (finalStatus === '要確認') {
        Browser.msgBox(
          '✅ 完了',
          `リサーチが完了しました！\n\n結果をご確認ください。`,
          Browser.Buttons.OK
        );
      } else if (finalStatus === 'エラー') {
        Browser.msgBox(
          '⚠️ エラー',
          `処理中にエラーが発生しました。\nAH列のログをご確認ください。`,
          Browser.Buttons.OK
        );
      } else {
        Browser.msgBox(
          '✅ 完了',
          `処理が完了しました。\nステータス: ${finalStatus}`,
          Browser.Buttons.OK
        );
      }
    } else {
      Browser.msgBox(
        '⏱️ タイムアウト',
        '処理が完了しませんでした。\n\nバックグラウンドで実行中です。\n1〜2分後に結果を確認してください。',
        Browser.Buttons.OK
      );
    }

  } catch (error) {
    sheet.getRange(row, 32).setValue('エラー');
    Browser.msgBox(
      '❌ エラー',
      `エラーが発生しました:\n${error.message}`,
      Browser.Buttons.OK
    );
    Logger.log('Error: ' + error);
  }
}

// ========================================
// GitHub Actions トリガー
// ========================================

function triggerGitHubActions(ebayUrl, rowNumber) {
  const props = PropertiesService.getScriptProperties();
  const GITHUB_TOKEN = props.getProperty('GITHUB_TOKEN');
  const GITHUB_REPO = props.getProperty('GITHUB_REPO');

  if (!GITHUB_TOKEN || !GITHUB_REPO) {
    Logger.log('GitHub設定が未完了です。setupProperties()を実行してください。');
    return false;
  }

  const url = `https://api.github.com/repos/${GITHUB_REPO}/dispatches`;

  const payload = {
    event_type: 'research_request',
    client_payload: {
      ebay_url: ebayUrl.toString(),
      row_number: rowNumber,
      timestamp: new Date().toISOString(),
      spreadsheet_id: SpreadsheetApp.getActiveSpreadsheet().getId()
    }
  };

  const options = {
    method: 'post',
    headers: {
      'Authorization': 'Bearer ' + GITHUB_TOKEN,
      'Accept': 'application/vnd.github.v3+json',
      'User-Agent': 'Google-Apps-Script'
    },
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();

    if (responseCode === 204) {
      Logger.log('✓ GitHub Actions起動成功');
      return true;
    } else {
      Logger.log(`✗ GitHub Actions起動失敗: ${responseCode}`);
      Logger.log(response.getContentText());
      return false;
    }
  } catch (error) {
    Logger.log('✗ エラー: ' + error.message);
    return false;
  }
}

// ========================================
// 結果待機（ポーリング）
// ========================================

function waitForCompletion(sheet, row) {
  const MAX_ATTEMPTS = 36;  // 36回 × 5秒 = 3分
  const INTERVAL = 5000;    // 5秒

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    // 5秒待機
    Utilities.sleep(INTERVAL);

    // ステータス確認
    const status = sheet.getRange(row, 32).getValue();

    Logger.log(`ポーリング ${attempt}/${MAX_ATTEMPTS}: ステータス = ${status}`);

    // 処理完了チェック
    if (status !== '処理中...') {
      Logger.log(`✓ 完了検知 (${attempt * 5}秒後)`);
      return true;
    }

    // 進捗表示（10秒ごと）
    if (attempt % 2 === 0) {
      const elapsed = attempt * 5;
      Logger.log(`処理中... (${elapsed}秒経過)`);
    }
  }

  Logger.log('✗ タイムアウト (3分経過)');
  return false;
}

// ========================================
// ユーティリティ
// ========================================

function testGitHubConnection() {
  const props = PropertiesService.getScriptProperties();
  const GITHUB_TOKEN = props.getProperty('GITHUB_TOKEN');
  const GITHUB_REPO = props.getProperty('GITHUB_REPO');

  if (!GITHUB_TOKEN || !GITHUB_REPO) {
    Logger.log('❌ GitHub設定が未完了です');
    return;
  }

  const url = `https://api.github.com/repos/${GITHUB_REPO}`;

  const options = {
    method: 'get',
    headers: {
      'Authorization': 'Bearer ' + GITHUB_TOKEN,
      'Accept': 'application/vnd.github.v3+json'
    },
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();

    if (responseCode === 200) {
      const data = JSON.parse(response.getContentText());
      Logger.log('✅ GitHub接続成功');
      Logger.log(`リポジトリ: ${data.full_name}`);
      Logger.log(`説明: ${data.description || '(なし)'}`);
      Browser.msgBox('✅ 接続成功', `GitHub接続に成功しました。\n\nリポジトリ: ${data.full_name}`, Browser.Buttons.OK);
    } else {
      Logger.log(`❌ エラー: ${responseCode}`);
      Logger.log(response.getContentText());
      Browser.msgBox('❌ 接続失敗', `GitHub接続に失敗しました。\nエラーコード: ${responseCode}`, Browser.Buttons.OK);
    }
  } catch (error) {
    Logger.log('❌ エラー: ' + error.message);
    Browser.msgBox('❌ エラー', error.message, Browser.Buttons.OK);
  }
}

// ========================================
// トリガー設定ヘルパー
// ========================================

function showSetupInstructions() {
  const message = `
【初期設定手順】

1. GitHub Personal Access Token取得
   - GitHub → Settings → Developer settings
   - Personal access tokens → Tokens (classic)
   - Generate new token
   - repo スコープを選択
   - トークンをコピー

2. スクリプトプロパティ設定
   - メニュー「eBayリサーチ」→「初期設定」
   - GITHUB_TOKEN: 取得したトークン
   - GITHUB_REPO: YOUR_USERNAME/ebaySystem

3. 接続テスト
   - Apps Scriptエディタで testGitHubConnection() を実行
   - 「接続成功」と表示されればOK

詳細: docs/SETUP_GITHUB_ACTIONS.md
  `;

  Browser.msgBox('初期設定手順', message, Browser.Buttons.OK);
}
