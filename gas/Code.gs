/**
 * eBay Research Automation - Google Apps Script
 * スプレッドシートのボタンからGitHub Actionsを起動
 */

// === 設定 ===
const GITHUB_TOKEN = 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN'; // GitHub Personal Access Token
const GITHUB_OWNER = 'SakuLife'; // GitHubユーザー名
const GITHUB_REPO = 'ebay-research-system'; // リポジトリ名
const INPUT_SHEET_NAME = '入力シート'; // 入力シートの名前

/**
 * スプレッドシートを開いたときに実行されるメニューを追加
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('リサーチツール')
    .addItem('選択行を実行', 'runResearchForSelectedRow')
    .addSeparator()
    .addItem('設定', 'showSettings')
    .addToUi();
}

/**
 * 選択されている行のリサーチを実行
 */
function runResearchForSelectedRow() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(INPUT_SHEET_NAME);
  const activeRow = sheet.getActiveCell().getRow();
  
  // ヘッダー行（1行目）は除外
  if (activeRow === 1) {
    SpreadsheetApp.getUi().alert('ヘッダー行は実行できません。データ行を選択してください。');
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
  
  if (result) {
    SpreadsheetApp.getUi().alert(
      'リサーチを開始しました。\n' +
      '完了まで2-3分かかります。\n\n' +
      '行番号: ' + activeRow + '\n' +
      'eBay URL: ' + ebayUrl
    );
  } else {
    sheet.getRange(activeRow, statusCol).setValue('エラー');
    SpreadsheetApp.getUi().alert('GitHub Actionsの起動に失敗しました。');
  }
}

/**
 * GitHub Actionsをトリガー
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
    const response = UrlFetchApp.fetch(url, options);
    const responseCode = response.getResponseCode();
    
    Logger.log('GitHub API Response: ' + responseCode);
    Logger.log('GitHub API Response Body: ' + response.getContentText());
    
    if (responseCode === 204) {
      return true; // 成功
    } else {
      Logger.log('GitHub Actions trigger failed: ' + response.getContentText());
      return false;
    }
  } catch (error) {
    Logger.log('Error: ' + error.toString());
    return false;
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
