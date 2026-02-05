/**
 * 入力シートに条件付き書式を設定する
 * ステータス列（V列）の値に応じて行全体の色を変更
 *
 * 使い方:
 * 1. Google スプレッドシートを開く
 * 2. 拡張機能 → Apps Script
 * 3. このコードを貼り付けて保存
 * 4. setupConditionalFormatting() を実行
 */

function setupConditionalFormatting() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("入力シート");

  if (!sheet) {
    Logger.log("入力シートが見つかりません");
    return;
  }

  // 既存の条件付き書式をクリア
  sheet.clearConditionalFormatRules();

  // データ範囲（2行目から1000行目、A列からX列まで）
  const range = sheet.getRange("A2:X1000");

  // ステータス列 = V列 (22列目)
  const statusColumn = 22; // V列

  const rules = [];

  // ルール1: ステータスが「OK」の場合 → 薄緑
  const ruleOK = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=$V2="OK"')
    .setBackground("#d9ead3")  // 薄緑
    .setRanges([range])
    .build();
  rules.push(ruleOK);

  // ルール2: ステータスが「除外」の場合 → 薄グレー
  const ruleExcluded = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=$V2="除外"')
    .setBackground("#d9d9d9")  // 薄グレー
    .setRanges([range])
    .build();
  rules.push(ruleExcluded);

  // ルール3: ステータスが「エラー」の場合 → 薄赤
  const ruleError = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=$V2="エラー"')
    .setBackground("#f4cccc")  // 薄赤
    .setRanges([range])
    .build();
  rules.push(ruleError);

  // ルール4: 出品フラグ（W列）が入力されている場合 → 薄青
  const ruleListed = SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=LEN($W2)>0')
    .setBackground("#cfe2f3")  // 薄青
    .setRanges([range])
    .build();
  rules.push(ruleListed);

  // ルールを適用
  sheet.setConditionalFormatRules(rules);

  Logger.log("条件付き書式を設定しました");
  Logger.log("- OK → 薄緑");
  Logger.log("- 除外 → 薄グレー");
  Logger.log("- エラー → 薄赤");
  Logger.log("- 出品フラグあり → 薄青");
}

/**
 * ステータス列にプルダウンを設定する
 * 古いT列のドロップダウンも削除する
 */
function setupStatusDropdown() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("入力シート");

  if (!sheet) {
    Logger.log("入力シートが見つかりません");
    return;
  }

  // 古いT列のドロップダウンを削除（列構造変更前の名残）
  const oldRange = sheet.getRange("T2:T1000");
  oldRange.clearDataValidations();
  Logger.log("T列の古いドロップダウンを削除しました");

  // ステータス列 = V列、2行目から1000行目
  const statusRange = sheet.getRange("V2:V1000");

  // プルダウンの選択肢
  const statusOptions = ["要確認", "OK", "除外", "エラー", "保留"];

  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(statusOptions, true)
    .setAllowInvalid(false)
    .build();

  statusRange.setDataValidation(rule);

  Logger.log("ステータス列(V列)にプルダウンを設定しました");
  Logger.log("選択肢: " + statusOptions.join(", "));
}

/**
 * 出品フラグ列にプルダウンを設定する
 */
function setupListingFlagDropdown() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("入力シート");

  if (!sheet) {
    Logger.log("入力シートが見つかりません");
    return;
  }

  // 出品フラグ列 = W列、2行目から1000行目
  const flagRange = sheet.getRange("W2:W1000");

  // プルダウンの選択肢
  const flagOptions = ["出品済", "出品中", "下書き"];

  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(flagOptions, true)
    .setAllowInvalid(true)  // 空欄も許可
    .build();

  flagRange.setDataValidation(rule);

  Logger.log("出品フラグ列にプルダウンを設定しました");
  Logger.log("選択肢: " + flagOptions.join(", "));
}

/**
 * すべての設定を一括で実行
 */
function setupAll() {
  setupConditionalFormatting();
  setupStatusDropdown();
  setupListingFlagDropdown();
  Logger.log("すべての設定が完了しました");
}

/**
 * T列のデータ入力規則を強制削除し、V列にプルダウンを設定
 * この関数を実行してください
 */
function fixDropdownColumns() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("入力シート");

  if (!sheet) {
    Logger.log("入力シートが見つかりません");
    return;
  }

  // ===== T列の入力規則を完全削除（複数の方法で試行） =====

  // 方法1: T列全体を文字列で指定
  try {
    sheet.getRange("T:T").clearDataValidations();
    Logger.log("方法1: T:T で削除試行");
  } catch(e) {
    Logger.log("方法1失敗: " + e);
  }

  // 方法2: T1:T10000 で指定
  try {
    sheet.getRange("T1:T10000").clearDataValidations();
    Logger.log("方法2: T1:T10000 で削除試行");
  } catch(e) {
    Logger.log("方法2失敗: " + e);
  }

  // 方法3: 列番号で指定（T=20列目）
  try {
    const lastRow = sheet.getLastRow() || 1000;
    sheet.getRange(1, 20, lastRow + 100, 1).clearDataValidations();
    Logger.log("方法3: 列番号20で削除試行");
  } catch(e) {
    Logger.log("方法3失敗: " + e);
  }

  // ===== V列にプルダウン設定（ステータス） =====
  const vRange = sheet.getRange("V2:V10000");
  const statusOptions = ["要確認", "OK", "除外", "エラー", "保留"];

  const statusRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(statusOptions, true)
    .setAllowInvalid(true)
    .build();

  vRange.setDataValidation(statusRule);
  Logger.log("V列にステータスプルダウンを設定");

  // ===== W列にプルダウン設定（出品フラグ） =====
  const wRange = sheet.getRange("W2:W10000");
  const flagOptions = ["出品済", "出品中", "下書き", "様子見"];

  const flagRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(flagOptions, true)
    .setAllowInvalid(true)
    .build();

  wRange.setDataValidation(flagRule);
  Logger.log("W列に出品フラグプルダウンを設定");

  SpreadsheetApp.getUi().alert(
    '完了',
    'V列（ステータス）とW列（出品フラグ）にプルダウンを設定しました。',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

/**
 * 全シートのデータ入力規則を確認（デバッグ用）
 */
function checkAllDataValidations() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName("入力シート");

  if (!sheet) return;

  // T列の各セルをチェック
  for (let row = 1; row <= 10; row++) {
    const cell = sheet.getRange(row, 20);
    const validation = cell.getDataValidation();
    if (validation) {
      Logger.log("T" + row + ": 入力規則あり - " + validation.getCriteriaType());
    }
  }

  Logger.log("チェック完了。ログを確認してください。");
}

// ============================================================
// 利益再計算機能
// ============================================================

/**
 * カスタムメニューを作成
 */
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('🔧 ツール')
    .addItem('📊 選択セルで利益再計算', 'recalculateProfitFromSelection')
    .addSeparator()
    .addItem('⚙️ 初期設定（プルダウン・書式）', 'setupAll')
    .addToUi();
}

/**
 * 選択セルの金額を使って、その行の利益を再計算する
 *
 * 使い方:
 * 1. 入力シートで、新しい仕入れ価格を入力したセルを選択
 * 2. メニュー「ツール」→「選択セルで利益再計算」を実行
 * 3. 検索ベースで計算し、結果が同じ行の利益列に書き込まれる
 */
function recalculateProfitFromSelection() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const inputSheet = ss.getSheetByName("入力シート");
  const calcSheet = ss.getSheetByName("検索ベース");
  const ui = SpreadsheetApp.getUi();

  if (!inputSheet || !calcSheet) {
    ui.alert('エラー', '入力シートまたは検索ベースシートが見つかりません。', ui.ButtonSet.OK);
    return;
  }

  // 選択セルを取得
  const selection = ss.getActiveRange();
  const selectedRow = selection.getRow();
  const selectedValue = selection.getValue();

  // 入力シートかチェック
  if (ss.getActiveSheet().getName() !== "入力シート") {
    ui.alert('エラー', '入力シートで実行してください。', ui.ButtonSet.OK);
    return;
  }

  // ヘッダー行は除外
  if (selectedRow < 2) {
    ui.alert('エラー', 'データ行（2行目以降）を選択してください。', ui.ButtonSet.OK);
    return;
  }

  // 選択セルが数値かチェック
  const sourcePrice = parseFloat(String(selectedValue).replace(/[¥,]/g, ''));
  if (isNaN(sourcePrice) || sourcePrice <= 0) {
    ui.alert('エラー', '選択セルに有効な金額が入っていません。\n選択値: ' + selectedValue, ui.ButtonSet.OK);
    return;
  }

  // 入力シートから必要なデータを読み取り（P列=販売価格, Q列=販売送料）
  const rowData = inputSheet.getRange(selectedRow, 1, 1, 24).getValues()[0];

  // 列インデックス (0-based)
  const COL_SELL_PRICE = 15;  // P列: 販売価格（米ドル）
  const COL_SHIPPING = 16;    // Q列: 販売送料（米ドル）
  const COL_PROFIT_NO_REBATE = 17;   // R列: 還付抜き利益額
  const COL_MARGIN_NO_REBATE = 18;   // S列: 利益率%（還付抜き）
  const COL_PROFIT_WITH_REBATE = 19; // T列: 還付あり利益額
  const COL_MARGIN_WITH_REBATE = 20; // U列: 利益率%（還付あり）

  const sellPrice = parseFloat(rowData[COL_SELL_PRICE]) || 0;
  const shipping = parseFloat(rowData[COL_SHIPPING]) || 0;

  if (sellPrice <= 0) {
    ui.alert('エラー', '販売価格（P列）が入っていません。', ui.ButtonSet.OK);
    return;
  }

  // 検索ベースシートに値を書き込み
  // B10=仕入値, C10=売値, D10=送料
  calcSheet.getRange("B10").setValue(sourcePrice);
  calcSheet.getRange("C10").setValue(sellPrice);
  calcSheet.getRange("D10").setValue(shipping);

  // スプレッドシートの計算を強制実行
  SpreadsheetApp.flush();

  // 少し待機（計算完了を待つ）
  Utilities.sleep(1000);

  // 計算結果を読み取り
  // N10=業者, O10=発送方法, P10=還付抜き利益, Q10=還付抜き利益率
  // P13=還付あり利益, Q13=還付あり利益率
  const profitNoRebate = calcSheet.getRange("P10").getValue();
  const marginNoRebate = calcSheet.getRange("Q10").getValue();
  const profitWithRebate = calcSheet.getRange("P13").getValue();
  const marginWithRebate = calcSheet.getRange("Q13").getValue();

  // 入力シートに結果を書き戻し（円・%は整数で出力）
  inputSheet.getRange(selectedRow, COL_PROFIT_NO_REBATE + 1).setValue(Math.round(profitNoRebate));
  inputSheet.getRange(selectedRow, COL_MARGIN_NO_REBATE + 1).setValue(Math.round(marginNoRebate));
  inputSheet.getRange(selectedRow, COL_PROFIT_WITH_REBATE + 1).setValue(Math.round(profitWithRebate));
  inputSheet.getRange(selectedRow, COL_MARGIN_WITH_REBATE + 1).setValue(Math.round(marginWithRebate));

  // 結果をユーザーに通知
  const resultMsg = [
    '利益再計算が完了しました！',
    '',
    '【入力】',
    '  仕入値: ¥' + sourcePrice.toLocaleString(),
    '  売値: $' + sellPrice,
    '  送料: $' + shipping,
    '',
    '【結果】',
    '  還付抜き利益: ¥' + Math.round(profitNoRebate).toLocaleString(),
    '  還付抜き利益率: ' + marginNoRebate + '%',
    '  還付あり利益: ¥' + Math.round(profitWithRebate).toLocaleString(),
    '  還付あり利益率: ' + marginWithRebate + '%',
  ].join('\n');

  ui.alert('完了', resultMsg, ui.ButtonSet.OK);
}
