/**
 * eBay Research Automation - Google Apps Script
 * スプレッドシートのボタンからGitHub Actionsを起動
 */

// === 設定 ===
const GITHUB_TOKEN = 'REDACTED_TOKEN'; // GitHub Personal Access Token
const GITHUB_OWNER = 'SakuLife'; // GitHubユーザー名
const GITHUB_REPO = 'ebay-research-system'; // リポジトリ名
const INPUT_SHEET_NAME = '入力シート'; // 入力シートの名前

/**
 * スプレッドシートを開いたときに実行されるメニューを追加
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('リサーチツール')
    .addItem('【Pattern②】自動リサーチ実行', 'runAutoResearch')
    .addSeparator()
    .addItem('【Pattern①】選択行を実行', 'runResearchForSelectedRow')
    .addSeparator()
    .addItem('設定', 'showSettings')
    .addToUi();
}

/**
 * 【Pattern②】自動リサーチを実行
 * 設定シートのキーワードを使って自動リサーチ
 */
function runAutoResearch() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // GitHub Tokenの設定チェック
  if (GITHUB_TOKEN === 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN') {
    SpreadsheetApp.getUi().alert(
      'エラー: GitHub Personal Access Tokenが設定されていません。\n\n' +
      'スクリプトエディタを開き、GITHUB_TOKENを設定してください。'
    );
    return;
  }

  // 設定シートの存在確認
  let settingsSheet = ss.getSheetByName('設定＆キーワード');
  if (!settingsSheet) {
    SpreadsheetApp.getUi().alert(
      'エラー: 「設定＆キーワード」シートが見つかりません。\n\n' +
      'シートを作成してから再度実行してください。'
    );
    return;
  }

  // A列のキーワードを取得（9行目以降がキーワード）
  const keywords = settingsSheet.getRange('A10:A').getValues()
    .map(row => row[0])
    .filter(keyword => keyword && keyword.toString().trim() !== '' && !keyword.toString().startsWith('【'));

  if (keywords.length === 0) {
    SpreadsheetApp.getUi().alert(
      'キーワードが設定されていません。\n\n' +
      '「設定」シートのA列にキーワードを入力してください。'
    );
    return;
  }

  // 確認ダイアログ
  const ui = SpreadsheetApp.getUi();
  const response = ui.alert(
    '自動リサーチを実行しますか？',
    `${keywords.length}個のキーワードで自動リサーチを実行します。\n\n` +
    `キーワード:\n${keywords.slice(0, 5).join('\n')}${keywords.length > 5 ? '\n...' : ''}\n\n` +
    '処理には数分かかる場合があります。',
    ui.ButtonSet.OK_CANCEL
  );

  if (response !== ui.Button.OK) {
    return;
  }

  // GitHub Actionsを実行
  const result = triggerAutoResearchGitHubActions();

  if (result.success) {
    SpreadsheetApp.getUi().alert(
      '自動リサーチを開始しました。\n\n' +
      `キーワード数: ${keywords.length}個\n` +
      '完了まで数分かかります。\n\n' +
      '結果は「入力シート」に追加されます。'
    );
  } else {
    SpreadsheetApp.getUi().alert(
      'GitHub Actionsの起動に失敗しました。\n\n' +
      'エラー詳細:\n' + result.error + '\n\n' +
      '確認事項:\n' +
      '1. GitHub Personal Access Tokenが正しく設定されているか\n' +
      '2. トークンに「repo」権限があるか\n' +
      '3. リポジトリ名が正しいか: ' + GITHUB_OWNER + '/' + GITHUB_REPO
    );
  }
}

/**
 * 【Pattern①】選択されている行のリサーチを実行
 */
function runResearchForSelectedRow() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(INPUT_SHEET_NAME);
  const activeRow = sheet.getActiveCell().getRow();

  // ヘッダー行（1行目）は除外
  if (activeRow === 1) {
    SpreadsheetApp.getUi().alert('ヘッダー行は実行できません。データ行を選択してください。');
    return;
  }

  // GitHub Tokenの設定チェック
  if (GITHUB_TOKEN === 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN') {
    SpreadsheetApp.getUi().alert(
      'エラー: GitHub Personal Access Tokenが設定されていません。\n\n' +
      'スクリプトエディタを開き、GITHUB_TOKENを設定してください。'
    );
    return;
  }

  // B列のeBay URLを取得
  const ebayUrl = sheet.getRange(activeRow, 2).getValue(); // B列 = 2

  if (!ebayUrl) {
    SpreadsheetApp.getUi().alert('B列にeBay URLが入力されていません。');
    return;
  }

  // ステータスを「処理中」に更新
  const statusCol = 19; // S列 = 19
  sheet.getRange(activeRow, statusCol).setValue('処理中');

  // GitHub Actionsを実行
  const result = triggerGitHubActions(ebayUrl, activeRow);

  if (result.success) {
    SpreadsheetApp.getUi().alert(
      'リサーチを開始しました。\n' +
      '完了まで2-3分かかります。\n\n' +
      '行番号: ' + activeRow + '\n' +
      'eBay URL: ' + ebayUrl
    );
  } else {
    sheet.getRange(activeRow, statusCol).setValue('エラー');
    const memoCol = 20; // T列 = 20
    sheet.getRange(activeRow, memoCol).setValue('GitHub Actions起動失敗: ' + result.error);

    SpreadsheetApp.getUi().alert(
      'GitHub Actionsの起動に失敗しました。\n\n' +
      'エラー詳細:\n' + result.error + '\n\n' +
      '確認事項:\n' +
      '1. GitHub Personal Access Tokenが正しく設定されているか\n' +
      '2. トークンに「repo」権限があるか\n' +
      '3. リポジトリ名が正しいか: ' + GITHUB_OWNER + '/' + GITHUB_REPO
    );
  }
}

/**
 * 【Pattern②】GitHub Actionsをトリガー（自動リサーチ用）
 */
function triggerAutoResearchGitHubActions() {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/dispatches`;

  const payload = {
    'event_type': 'auto_research_request',
    'client_payload': {}
  };

  const options = {
    'method': 'post',
    'headers': {
      'Authorization': 'token ' + GITHUB_TOKEN,
      'Accept': 'application/vnd.github.v3+json',
      'Content-Type': 'application/json'
    },
    'payload': JSON.stringify(payload),
    'muteHttpExceptions': true
  };

  try {
    Logger.log('Triggering Auto Research GitHub Actions...');
    Logger.log('URL: ' + url);
    Logger.log('Payload: ' + JSON.stringify(payload));

    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();
    const responseBody = response.getContentText();

    Logger.log('GitHub API Response Code: ' + responseCode);
    Logger.log('GitHub API Response Body: ' + responseBody);

    if (responseCode === 204) {
      return { success: true }; // 成功
    } else {
      // エラーレスポンスをパース
      let errorMessage = 'HTTPステータス: ' + responseCode;
      try {
        const errorData = JSON.parse(responseBody);
        if (errorData.message) {
          errorMessage += '\nメッセージ: ' + errorData.message;
        }
        if (errorData.documentation_url) {
          errorMessage += '\nドキュメント: ' + errorData.documentation_url;
        }
      } catch (e) {
        errorMessage += '\nレスポンス: ' + responseBody;
      }

      Logger.log('Auto Research GitHub Actions trigger failed: ' + errorMessage);
      return { success: false, error: errorMessage };
    }
  } catch (error) {
    const errorMessage = 'ネットワークエラー: ' + error.toString();
    Logger.log('Error: ' + errorMessage);
    return { success: false, error: errorMessage };
  }
}

/**
 * 【Pattern①】GitHub Actionsをトリガー
 */
function triggerGitHubActions(ebayUrl, rowNumber) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/dispatches`;

  const payload = {
    'event_type': 'research_request',
    'client_payload': {
      'ebay_url': ebayUrl,
      'row_number': rowNumber
    }
  };

  const options = {
    'method': 'post',
    'headers': {
      'Authorization': 'token ' + GITHUB_TOKEN,
      'Accept': 'application/vnd.github.v3+json',
      'Content-Type': 'application/json'
    },
    'payload': JSON.stringify(payload),
    'muteHttpExceptions': true
  };

  try {
    Logger.log('Triggering GitHub Actions...');
    Logger.log('URL: ' + url);
    Logger.log('Payload: ' + JSON.stringify(payload));

    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();
    const responseBody = response.getContentText();

    Logger.log('GitHub API Response Code: ' + responseCode);
    Logger.log('GitHub API Response Body: ' + responseBody);

    if (responseCode === 204) {
      return { success: true }; // 成功
    } else {
      // エラーレスポンスをパース
      let errorMessage = 'HTTPステータス: ' + responseCode;
      try {
        const errorData = JSON.parse(responseBody);
        if (errorData.message) {
          errorMessage += '\nメッセージ: ' + errorData.message;
        }
        if (errorData.documentation_url) {
          errorMessage += '\nドキュメント: ' + errorData.documentation_url;
        }
      } catch (e) {
        errorMessage += '\nレスポンス: ' + responseBody;
      }

      Logger.log('GitHub Actions trigger failed: ' + errorMessage);
      return { success: false, error: errorMessage };
    }
  } catch (error) {
    const errorMessage = 'ネットワークエラー: ' + error.toString();
    Logger.log('Error: ' + errorMessage);
    return { success: false, error: errorMessage };
  }
}

/**
 * 設定画面を表示
 */
function showSettings() {
  const html = `
    <h3>設定情報</h3>
    <p><strong>GitHubリポジトリ:</strong> ${GITHUB_OWNER}/${GITHUB_REPO}</p>
    <p><strong>入力シート名:</strong> ${INPUT_SHEET_NAME}</p>
    <br>
    <p><a href="https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}/actions" target="_blank">GitHub Actionsを確認</a></p>
  `;

  const htmlOutput = HtmlService.createHtmlOutput(html)
    .setWidth(400)
    .setHeight(200);

  SpreadsheetApp.getUi().showModalDialog(htmlOutput, 'リサーチツール設定');
}
