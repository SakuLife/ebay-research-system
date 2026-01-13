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
    .addItem('自動リサーチ実行', 'runAutoResearch')
    .addSeparator()
    .addItem('設定確認', 'showSettings')
    .addToUi();
}

/**
 * 自動リサーチを実行
 * 設定シートのキーワード×修飾語で自動リサーチ
 */
function runAutoResearch() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  // GitHub Tokenの設定チェック
  if (GITHUB_TOKEN === 'YOUR_GITHUB_PERSONAL_ACCESS_TOKEN') {
    ui.alert(
      'エラー: GitHub Personal Access Tokenが設定されていません。\n\n' +
      'スクリプトエディタを開き、GITHUB_TOKENを設定してください。'
    );
    return;
  }

  // 設定シートの存在確認
  let settingsSheet = ss.getSheetByName('設定＆キーワード');
  if (!settingsSheet) {
    ui.alert(
      'エラー: 「設定＆キーワード」シートが見つかりません。\n\n' +
      'シートを作成してから再度実行してください。'
    );
    return;
  }

  // === 設定値を読み取り ===
  const market = settingsSheet.getRange('B4').getValue() || 'UK';
  const period = settingsSheet.getRange('B5').getValue() || '90日';
  const minPrice = settingsSheet.getRange('B6').getValue() || '100';
  const minProfit = settingsSheet.getRange('B7').getValue() || 'フィルターなし';
  const itemsPerKeyword = settingsSheet.getRange('B8').getValue() || '5';
  const minSold = settingsSheet.getRange('B9').getValue() || '0';

  // === キーワードと修飾語を読み取り ===
  const keywordData = settingsSheet.getRange('E4:F100').getValues();

  const keywords = [];
  const modifiers = [];

  for (let i = 0; i < keywordData.length; i++) {
    const kw = keywordData[i][0] ? keywordData[i][0].toString().trim() : '';
    const mod = keywordData[i][1] ? keywordData[i][1].toString().trim() : '';

    if (kw && !kw.startsWith('【') && keywords.indexOf(kw) === -1) {
      keywords.push(kw);
    }
    if (mod && modifiers.indexOf(mod) === -1) {
      modifiers.push(mod);
    }
  }

  if (keywords.length === 0) {
    ui.alert(
      'キーワードが設定されていません。\n\n' +
      '「設定＆キーワード」シートのE列にキーワードを入力してください。'
    );
    return;
  }

  // === 検索パターン数を計算 ===
  const patternCount = modifiers.length > 0 ? keywords.length * modifiers.length : keywords.length;
  const totalItems = patternCount * parseInt(itemsPerKeyword);

  // === 確認メッセージを作成 ===
  let confirmMessage = '';
  confirmMessage += '【検索設定】\n';
  confirmMessage += `  マーケット: ${market}\n`;
  confirmMessage += `  最低価格: $${minPrice}\n`;
  confirmMessage += `  最低利益: ${minProfit}\n`;
  confirmMessage += `  最小販売数: ${minSold}\n`;
  confirmMessage += '\n';

  confirmMessage += '【キーワード】 ' + keywords.length + '個\n';
  confirmMessage += '  ' + keywords.slice(0, 3).join(', ');
  if (keywords.length > 3) confirmMessage += ' ...';
  confirmMessage += '\n\n';

  if (modifiers.length > 0) {
    confirmMessage += '【修飾語】 ' + modifiers.length + '個\n';
    confirmMessage += '  ' + modifiers.slice(0, 5).join(', ');
    if (modifiers.length > 5) confirmMessage += ' ...';
    confirmMessage += '\n\n';
  }

  confirmMessage += '【検索規模】\n';
  confirmMessage += `  検索パターン: ${patternCount}パターン\n`;
  confirmMessage += `  （${keywords.length}キーワード × ${modifiers.length || 1}修飾語）\n`;
  confirmMessage += `  各パターン商品数: ${itemsPerKeyword}個\n`;
  confirmMessage += `  最大出力行数: ${totalItems}行\n`;
  confirmMessage += '\n';
  confirmMessage += '処理には数分かかる場合があります。';

  const response = ui.alert('自動リサーチを実行しますか？', confirmMessage, ui.ButtonSet.OK_CANCEL);

  if (response !== ui.Button.OK) {
    return;
  }

  // GitHub Actionsを実行
  const result = triggerAutoResearchGitHubActions();

  if (result.success) {
    ui.alert(
      '自動リサーチを開始しました。\n\n' +
      `検索パターン: ${patternCount}パターン\n` +
      `最大出力行数: ${totalItems}行\n\n` +
      '結果は「入力シート」に追加されます。'
    );
  } else {
    ui.alert(
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
 * GitHub Actionsをトリガー（自動リサーチ用）
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
