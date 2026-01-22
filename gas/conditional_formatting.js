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
