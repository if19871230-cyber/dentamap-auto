/**
 * デンタマップ予約データ 自動取得（Google Apps Script 版）
 *
 * 毎晩、Googleドライブの「デンタマップ予約データ」フォルダに次を保存する。
 *   月次/YYYYMMデータ.csv          … 先月・今月（バックアップ画面「過去分」csv形式と同じ）
 *   未来分/yoyaku_dataYYYYMMDD.csv … 180日分（「未来分」HTMLの「CSVダウンロード」と同じ）
 *   最終取得.txt                    … 最後の取得日時と成功／失敗
 *
 * 初回の設定
 *   1. プロジェクトの設定 → スクリプト プロパティ に
 *        DENTAMAP_USER（ユーザー名）と DENTAMAP_PASS（パスワード）を登録
 *   2. 関数 testLogin を実行（初回は権限の承認が出る）→ 実行ログに「ログイン成功」
 *   3. 関数 runNow を実行 → ドライブにCSVができることを確認
 *   4. 関数 setupTrigger を実行 → 月〜土の17:30に自動実行
 *   5. （任意）「デプロイ → 新しいデプロイ → ウェブアプリ」で公開すると、
 *      そのURLを開くだけで「今すぐ取得」できる（実行ユーザー：自分、アクセス：自分のみ）
 */

var BASE = 'https://plus.dentamap.jp/apl/';
var ROOT_FOLDER_NAME = 'デンタマップ予約データ';
var FUTURE_DAYS = 180;
var PRINT_FLG = '1,1,1,1,1,1,0,0,0';
var KEEP_FUTURE_FILES = 14;
var RUN_HOUR = 17;
var RUN_MINUTE = 30;
var UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';

// ------------------------------------------------------------ 実行用の関数

/** ログインだけ確かめる（データは取らない） */
function testLogin() {
  var c = new DentamapClient_();
  var info = c.login();
  Logger.log('ログイン成功（デンタマップのバージョン ' + info.version + '）');
}

/** 今すぐ1回取得する（テスト用。日曜でも取る） */
function runNow() {
  fetchAll_();
}

/** 決まった時刻（月〜土 17:30）に呼ばれる */
function dailyJob() {
  cleanupOneTimeTriggers_();
  try {
    fetchAll_();
  } catch (e) {
    writeStatus_('失敗：' + e.message);
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
      '【デンタマップ自動取得】取得に失敗しました',
      '取得に失敗しました。\n\n' + e.message + '\n\nGoogle Apps Script の「実行数」で詳細を確認できます。');
    throw e;
  }
}

/**
 * 毎日16時台に呼ばれ、その日の17:30ちょうどに dailyJob を予約する。
 * （GASの「毎日○時」は1時間の幅でしか指定できないため、この2段構えで時刻を合わせる）
 * 日曜日は予約しない。
 */
function scheduleToday() {
  cleanupOneTimeTriggers_();
  var now = new Date();
  if (now.getDay() === 0) {
    Logger.log('日曜日なので予約しません');
    return;
  }
  var at = new Date(now.getFullYear(), now.getMonth(), now.getDate(), RUN_HOUR, RUN_MINUTE, 0);
  if (at.getTime() <= now.getTime()) at = new Date(now.getTime() + 60 * 1000);
  ScriptApp.newTrigger('dailyJob').timeBased().at(at).create();
  Logger.log('本日 ' + Utilities.formatDate(at, 'Asia/Tokyo', 'HH:mm') + ' に取得を予約しました');
}

/** 自動実行を登録する（何度実行しても1組だけになる） */
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var f = t.getHandlerFunction();
    if (f === 'dailyJob' || f === 'scheduleToday') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('scheduleToday').timeBased().atHour(RUN_HOUR - 1).everyDays(1)
    .inTimezone('Asia/Tokyo').create();
  Logger.log('月〜土の ' + RUN_HOUR + ':' + ('0' + RUN_MINUTE).slice(-2) + ' に自動で取得します');
  var now = new Date();
  if (now.getHours() === RUN_HOUR - 1 || (now.getHours() === RUN_HOUR && now.getMinutes() < RUN_MINUTE)) scheduleToday();
}

function cleanupOneTimeTriggers_() {
  // 実行済みの「その日だけ」の予約（dailyJob）を片付ける
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'dailyJob') ScriptApp.deleteTrigger(t);
  });
}

/** ウェブアプリのURLを開いたときに呼ばれる：今すぐ取得して結果を表示 */
function doGet() {
  var msg, ok = true;
  try {
    fetchAll_();
    msg = '取得しました。Googleドライブの「' + ROOT_FOLDER_NAME + '」フォルダを確認してください。';
  } catch (e) {
    ok = false;
    writeStatus_('失敗：' + e.message);
    msg = '取得に失敗しました：' + e.message;
  }
  var stamp = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy/MM/dd HH:mm');
  return HtmlService.createHtmlOutput(
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<div style="font-family:sans-serif;padding:24px;font-size:18px">' +
    '<h2 style="color:' + (ok ? '#1a7f37' : '#c0392b') + '">' + (ok ? '✓ 取得完了' : '✗ 取得失敗') + '</h2>' +
    '<p>' + msg.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</p><p style="color:#666">' + stamp + '</p></div>'
  ).setTitle('デンタマップ 今すぐ取得');
}

// ------------------------------------------------------------ 取得の本体

function fetchAll_() {
  var c = new DentamapClient_();
  var info = c.login();
  var root = getFolder_(DriveApp.getRootFolder(), ROOT_FOLDER_NAME);
  var monthDir = getFolder_(root, '月次');
  var futureDir = getFolder_(root, '未来分');

  var tz = 'Asia/Tokyo';
  var now = new Date();
  var first = new Date(now.getFullYear(), now.getMonth(), 1);
  var prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  [prev, first].forEach(function (m) {
    var ym = Utilities.formatDate(m, tz, 'yyyyMM');
    var bytes = c.download(info, 'yoyaku_data', { date: ym }, isMonthCsv_);
    saveFile_(monthDir, ym + 'データ.csv', Utilities.newBlob(bytes, 'text/csv'));
    Logger.log('月次CSVを保存: ' + ym);
  });

  var ymd = Utilities.formatDate(now, tz, 'yyyyMMdd');
  var page = c.download(info, 'backup_list',
    { date: ymd, no: String(FUTURE_DAYS), print_flg: PRINT_FLG }, isFutureHtml_);
  var html = Utilities.newBlob(page).getDataAsString('UTF-8');
  var csv = makeCsvText(html);
  var bom = [0xEF, 0xBB, 0xBF];
  var csvBytes = bom.concat(Utilities.newBlob(csv).getBytes());
  saveFile_(futureDir, 'yoyaku_data' + ymd + '.csv', Utilities.newBlob(csvBytes, 'text/csv'));
  Logger.log('未来分CSVを保存: ' + ymd + '（' + (csv.split('\n').length - 2) + '行）');

  cleanupFuture_(futureDir);
  writeStatus_('成功');
}

function isMonthCsv_(bytes) {
  return Utilities.newBlob(bytes).getDataAsString('Shift_JIS').indexOf('予約明細') >= 0;
}

function isFutureHtml_(bytes) {
  return Utilities.newBlob(bytes).getDataAsString('UTF-8').indexOf('id="csv_data"') >= 0;
}

// ------------------------------------------------------------ デンタマップとの通信

function DentamapClient_() {
  this.cookies = {};
}

DentamapClient_.prototype.request_ = function (url, opt) {
  opt = opt || {};
  var headers = opt.headers || {};
  headers['User-Agent'] = UA;
  var jar = this.cookies;
  var cookie = Object.keys(jar).map(function (k) { return k + '=' + jar[k]; }).join('; ');
  if (cookie) headers['Cookie'] = cookie;
  var params = { method: opt.method || 'get', headers: headers, followRedirects: false, muteHttpExceptions: true };
  if (opt.payload) params.payload = opt.payload;
  var res = UrlFetchApp.fetch(url, params);
  var set = res.getAllHeaders()['Set-Cookie'];
  if (set) {
    (Array.isArray(set) ? set : [set]).forEach(function (sc) {
      var kv = String(sc).split(';')[0];
      var i = kv.indexOf('=');
      if (i > 0) jar[kv.slice(0, i).trim()] = kv.slice(i + 1).trim();
    });
  }
  return res;
};

DentamapClient_.prototype.postHandler_ = function (path, obj, info) {
  var body = {};
  Object.keys(obj).forEach(function (k) { body[k] = obj[k]; });
  body.user = info;
  var res = this.request_(BASE + 'com_handler/' + path, {
    method: 'post',
    payload: { data: JSON.stringify(body), auto_load: '{}' },
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'Referer': BASE + 'main.php' }
  });
  try {
    return JSON.parse(res.getContentText('UTF-8'));
  } catch (e) {
    throw new Error(path + ' の応答を読めませんでした（HTTP ' + res.getResponseCode() + '）');
  }
};

DentamapClient_.prototype.login = function () {
  var props = PropertiesService.getScriptProperties();
  var user = props.getProperty('DENTAMAP_USER');
  var pass = props.getProperty('DENTAMAP_PASS');
  if (!user || !pass) throw new Error('スクリプト プロパティに DENTAMAP_USER と DENTAMAP_PASS を登録してください');

  var top = this.request_(BASE + 'index.php');
  if (top.getResponseCode() !== 200) {
    throw new Error('デンタマップのログイン画面に接続できませんでした（HTTP ' + top.getResponseCode() + '）。Googleのサーバーからの接続が拒否されている可能性があります');
  }
  var r = this.request_(BASE + 'dmp_first_login.php', {
    method: 'post',
    payload: { u: user, p: pass },
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'Referer': BASE + 'index.php' }
  });
  var res;
  try {
    res = JSON.parse(r.getContentText('UTF-8'));
  } catch (e) {
    throw new Error('ログインの応答を読めませんでした（HTTP ' + r.getResponseCode() + '）。接続が拒否されたか、デンタマップの仕組みが変わった可能性があります');
  }
  var d = res.data || {};
  if (d.err_type === 'dp_type6_err') throw new Error('料金のお支払いが確認できなかったため、デンタマップが使えない状態です');
  if (d.err_type === 'dp_sys_err') throw new Error('デンタマップ側のエラー：' + (res.err_text || ''));
  if (String(res.res) !== '1') throw new Error('ログインできませんでした（ユーザー名かパスワードが違う可能性があります）');

  var info = { id: d.u, pass: d.p, ip: d.i };
  var first = this.postHandler_('loginfirst.html', {}, info);
  if (String(first.res) !== '1') throw new Error('予約画面の読み込みに失敗しました（res=' + first.res + '）');
  var m = first.data && first.data.master_info && first.data.master_info[0];
  if (!m) throw new Error('予約画面の情報を読めませんでした（デンタマップの仕組みが変わった可能性があります）');
  info.dev_flg = m.dev_flg;
  info.version = m.version;
  return info;
};

DentamapClient_.prototype.download = function (info, kind, params, ok) {
  for (var attempt = 0; attempt < 3; attempt++) {
    var res = this.postHandler_('tmp/download_file_session_start.html', { kind: kind }, info);
    if (String(res.res) === '1') {
      var q = 'r=' + new Date().getTime();
      Object.keys(params).forEach(function (k) { q += '&' + k + '=' + encodeURIComponent(params[k]); });
      var r = this.request_(BASE + 'com_handler/tmp/download_file_load.html?' + q, { headers: { 'Referer': BASE + 'main.php' } });
      var bytes = r.getContent();
      if (r.getResponseCode() === 200 && ok(bytes)) return bytes;
      Logger.log('取得をやり直します（' + kind + ' ' + JSON.stringify(params) + ' HTTP ' + r.getResponseCode() + '）');
    } else {
      Logger.log('ダウンロードの受付に失敗（' + kind + ' res=' + res.res + '）');
    }
    Utilities.sleep(5000 * (attempt + 1));
  }
  throw new Error('ダウンロードに失敗しました：' + kind + ' ' + JSON.stringify(params));
};

// ------------------------------------------------------------ ドライブ

function getFolder_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function saveFile_(folder, name, blob) {
  var it = folder.getFilesByName(name);
  while (it.hasNext()) it.next().setTrashed(true);
  folder.createFile(blob.setName(name));
}

function cleanupFuture_(folder) {
  var files = [];
  var it = folder.getFiles();
  while (it.hasNext()) {
    var f = it.next();
    if (/^yoyaku_data\d{8}\.csv$/.test(f.getName())) files.push(f);
  }
  files.sort(function (a, b) { return a.getName() < b.getName() ? -1 : 1; });
  files.slice(0, Math.max(0, files.length - KEEP_FUTURE_FILES)).forEach(function (f) { f.setTrashed(true); });
}

function writeStatus_(text) {
  var root = getFolder_(DriveApp.getRootFolder(), ROOT_FOLDER_NAME);
  var stamp = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm');
  saveFile_(root, '最終取得.txt', Utilities.newBlob('最終取得 ' + stamp + '　' + text + '\n', 'text/plain'));
}

// ------------------------------------------------------------ 未来分HTML → CSV
// デンタマップのHTMLにある csv_make() と同じ出力を作る

function unescapeHtml_(s) {
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|lt|gt|amp|quot|apos|#39|nbsp);/g, function (m, e) {
    if (e[0] === '#') {
      var n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return String.fromCodePoint(n);
    }
    return { lt: '<', gt: '>', amp: '&', quot: '"', apos: "'", nbsp: ' ' }[e];
  });
}

function tag_(block, name) {
  var m = block.match(new RegExp('<' + name + '>([\\s\\S]*?)</' + name + '>'));
  return m ? unescapeHtml_(m[1]) : '';
}

function makeCsvText(page) {
  var CANCEL = { '1': 'TELキャンセル', '2': '無断キャンセル', '3': 'NETキャンセル', '4': 'TELキャンセル(変更)', '5': 'NETキャンセル(変更)' };
  var header = '患者番号,患者名,予約日,予約開始時間,予約終了時間,ユニット,診療内容,' +
    '担当１,担当２,担当３,部位番号,カラー番号,技工,予約記号,キャンセル,MEMO,Apo link\n';
  var um = page.match(/g_unit_array\s*=\s*new Array\(([\s\S]*?)\);/);
  var units = um ? (um[1].match(/"(.*?)"/g) || []).map(function (x) { return x.slice(1, -1); }) : [];
  var pm = page.match(/var p_num\s*=\s*(\d+)/);
  var pNum = pm ? parseInt(pm[1], 10) : 0;
  var staff = {};
  var sm = page.match(/<div id="staff_data">([\s\S]*?)<\/div>/);
  if (sm) {
    sm[1].split(/<br\s*\/?>/i).forEach(function (part) {
      var i = part.indexOf(',');
      if (i >= 0) staff[part.slice(0, i)] = part.slice(i + 1);
    });
  }
  var sec = page.slice(page.indexOf('<div id="csv_data">'), page.indexOf('<div id="day_memo_data">'));
  var rows = [], links = [], ids = [], linkNo = {}, counter = 0;
  var dateRe = /<date id\s*=\s*"(\d{8})">([\s\S]*?)<\/date>/g, dm;
  while ((dm = dateRe.exec(sec)) !== null) {
    var date = dm[1];
    var listRe = /<list id\s*=\s*"(\d+)">([\s\S]*?)<\/list>/g, lm;
    while ((lm = listRe.exec(dm[2])) !== null) {
      var yid = lm[1], b = lm[2];
      if (!b.trim()) continue;
      var rId = tag_(b, 'r_id');
      if (rId === '@system2') continue;
      var unit = tag_(b, 'unit');
      var ui = /^-?\d+$/.test(unit) ? parseInt(unit, 10) : null;
      if (ui !== null && ui >= 0 && ui < units.length && units[ui]) unit = units[ui];
      else if (ui !== null) unit = (ui <= pNum ? '列' : '予備') + ui;
      var cv = parseInt(tag_(b, 'u_color'), 16);
      var color = isNaN(cv) ? 'NaN' : String(cv);
      var g = parseInt(tag_(b, 'gikou') || '0', 10) || 0;
      var dispG = (g & 1) ? '技工' : '';
      var kigou = '';
      [[2, '● '], [4, '▲ '], [8, '■ '], [16, '★ '], [32, '◆ '], [64, '◎']].forEach(function (p) { if (g & p[0]) kigou += p[1]; });
      var st = function (t) {
        var v = tag_(b, t);
        return (v !== '' && v !== '0' && staff[v] !== undefined) ? staff[v].split('<br>')[0] : '';
      };
      var bui = tag_(b, 'bui');
      var link = tag_(b, 'link_data');
      if (link && !(link in linkNo)) { counter++; linkNo[link] = counter; }
      var cancel = CANCEL[tag_(b, 'cancel')] || '';
      var memo;
      if (rId === '@system') { rId = 'system'; memo = bui; bui = ''; }
      else memo = tag_(b, 'memo');
      memo = memo.replace(/\r\n/g, '　').replace(/\n/g, '　').replace(/\r/g, '　');
      rows.push([rId, tag_(b, 'title1'), date, tag_(b, 'sdate'), tag_(b, 'edate'), unit, tag_(b, 'med'),
        st('t1'), st('t2'), st('t3'), bui, color, dispG, kigou, cancel, memo].join(',') + ',');
      links.push(link);
      ids.push(yid);
    }
  }
  var out = header;
  for (var i = 0; i < rows.length; i++) {
    var lt = '';
    if (links[i]) lt = 'C' + linkNo[links[i]];
    else if (ids[i] in linkNo) lt = 'P' + linkNo[ids[i]];
    out += rows[i] + lt + '\n';
  }
  return out;
}
