/**
 * 取得のあとに続けて行う集計（Code.gs の fetchAll_ から呼ばれる）
 *
 * 1. 予約データ_最新.csv
 *    月次CSV（同じ月は最新の取得で上書き済み）＋ 最新の未来分CSV を、
 *    月次CSVの「予約明細表」と同じ22列・同じ文字コード（Shift_JIS）で1つにまとめる。
 *    月次CSVがある月は月次CSVを使い、ない月（来月以降）は最新の未来分を使う。
 *    → 分析や電話リストはこのファイルを使えば、常に「新しいほうが優先」された状態になる。
 *
 * 2. 予約の埋まり具合_YYYY.csv
 *    毎回の取得時点で、先の予約日ごと・区分（院長／衛生士／その他）ごとに
 *    何枠埋まっているかを1行ずつ追記していく（その日時点の記録を残す）。
 *    同じ日に何度取得した場合は、その日の分を最新で置き換える。
 *
 * 未来分CSV（未来分/yoyaku_dataYYYYMMDD.csv）は消さずに残す（その日時点の予約の生データ）。
 */

var MERGED_NAME = '予約データ_最新.csv';
var HISTORY_PREFIX = '予約の埋まり具合_';
var CHARSET_MONTH = 'Windows-31J';   // 月次CSVの文字コード（Shift_JISのWindows版）

// 区分の決め方（予約表の列名 → 区分）
var UNIT_GROUP = { '院長1': '院長', '院長2': '院長', '大山果奈': '衛生士', 'DH': '衛生士' };

var MONTH_HEADER = '患者番号,患者名,予約日,予約開始時間,予約終了時間,列,診療内容,担当１,担当２,担当３,' +
  '部位番号,カラー番号,技工,予約記号,キャンセル,Memo,来院時間,案内時間,完了時間,来院～案内時間(分),案内～完了時間(分),Apo link';

function buildSummaries_(root, monthDir, futureCsvText, fetchDate) {
  buildMerged_(root, monthDir, futureCsvText);
  appendHistory_(root, futureCsvText, fetchDate);
}

// ------------------------------------------------------------ 1. 最新データの統合

function buildMerged_(root, monthDir, futureCsvText) {
  var months = {};
  var header = null;
  var rows = [];
  var it = monthDir.getFiles();
  while (it.hasNext()) {
    var f = it.next();
    var m = f.getName().match(/^(\d{6})データ\.csv$/);
    if (!m) continue;
    var text = f.getBlob().getDataAsString(CHARSET_MONTH).replace(/\r\n/g, '\n');
    var lines = text.split('\n');
    var idx = -1;
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].indexOf('予約明細') >= 0) { idx = i; break; }
    }
    if (idx < 0) continue;
    if (!header) header = lines[idx + 1];
    months[m[1]] = true;
    for (var j = idx + 2; j < lines.length; j++) {
      if (lines[j].trim()) rows.push(lines[j]);
    }
  }
  futureRows_(futureCsvText).forEach(function (r) {
    if (months[r[2].slice(0, 6)]) return;           // 月次CSVがある月は月次を優先
    rows.push([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13], r[14], r[15],
      '', '', '', '', '', r[16]].join(','));
  });
  rows.sort(function (a, b) {
    var x = a.split(','), y = b.split(',');
    var ka = x[2] + x[3], kb = y[2] + y[3];
    return ka < kb ? -1 : ka > kb ? 1 : 0;
  });
  var out = (header || MONTH_HEADER) + '\n' + rows.join('\n') + '\n';
  var blob = Utilities.newBlob('', 'text/csv', MERGED_NAME);
  blob.setDataFromString(out, CHARSET_MONTH);
  saveFile_(root, MERGED_NAME, blob);
  Logger.log('最新データを作成: ' + rows.length + '件（月次 ' + Object.keys(months).length + 'か月分＋未来分）');
}

/** 未来分CSVを17列の配列にする。診療内容のカンマで列が増えた行は「、」でつないで戻す */
function futureRows_(csvText) {
  var lines = csvText.replace(/^﻿/, '').split('\n');
  var out = [];
  for (var i = 1; i < lines.length; i++) {
    if (!lines[i]) continue;
    var r = lines[i].split(',');
    if (r.length < 17) continue;
    if (r.length > 17) {
      var extra = r.length - 17;
      r = r.slice(0, 6).concat([r.slice(6, 7 + extra).join('、')]).concat(r.slice(7 + extra));
    }
    out.push(r);
  }
  return out;
}

// ------------------------------------------------------------ 2. その日時点の埋まり具合の記録

function appendHistory_(root, futureCsvText, fetchDate) {
  var tz = 'Asia/Tokyo';
  var fd = Utilities.formatDate(fetchDate, tz, 'yyyy/MM/dd');
  var WEEK = ['日', '月', '火', '水', '木', '金', '土'];

  var agg = {};
  futureRows_(futureCsvText).forEach(function (r) {
    var pid = r[0], date = r[2], unit = r[5], cancel = r[14];
    if (pid === 'system') return;
    var group = UNIT_GROUP[unit] || 'その他';
    var key = date + '|' + group;
    var a = agg[key] || (agg[key] = { date: date, group: group, slots: 0, cancels: 0, patients: {} });
    if (cancel) {
      a.cancels++;
    } else {
      a.slots++;
      a.patients[pid || ('名:' + r[1].replace(/[\s　]/g, ''))] = true;
    }
  });

  var newLines = Object.keys(agg).sort().map(function (k) {
    var a = agg[k];
    var d = new Date(+a.date.slice(0, 4), +a.date.slice(4, 6) - 1, +a.date.slice(6, 8));
    var daysAhead = Math.round((d.getTime() - new Date(fetchDate.getFullYear(), fetchDate.getMonth(), fetchDate.getDate()).getTime()) / 86400000);
    return [fd, a.date.slice(0, 4) + '/' + a.date.slice(4, 6) + '/' + a.date.slice(6, 8), WEEK[d.getDay()], daysAhead,
      a.group, a.slots, Object.keys(a.patients).length, a.cancels].join(',');
  });

  var name = HISTORY_PREFIX + Utilities.formatDate(fetchDate, tz, 'yyyy') + '.csv';
  var head = '取得日,予約日,曜日,何日先,区分,予約枠数（キャンセル除く）,患者数,キャンセル済み枠数';
  var old = [];
  var it = root.getFilesByName(name);
  if (it.hasNext()) {
    old = it.next().getBlob().getDataAsString('UTF-8').replace(/^﻿/, '').split('\n')
      .filter(function (l, i) { return i > 0 && l && l.indexOf(fd + ',') !== 0; });   // 同じ取得日の分は置き換え
  }
  var text = '﻿' + head + '\n' + old.concat(newLines).join('\n') + '\n';
  saveFile_(root, name, Utilities.newBlob(text, 'text/csv', name));
  Logger.log('埋まり具合を記録: ' + newLines.length + '行（' + name + '）');
}
