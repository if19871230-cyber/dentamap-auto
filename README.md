# デンタマップ予約データ 自動取得アプリ（DentamapAuto）

医院のWindowsパソコンで、デンタマップの予約データを毎日自動で取得します。

## 取得するもの（毎日）
- 先月・今月の月次CSV（バックアップ画面「過去分」csv形式と同じ） → `保存先\月次\YYYYMMデータ.csv`
- 未来分180日のCSV（「未来分」HTMLの「CSVダウンロード」と同じ） → `保存先\未来分\yoyaku_dataYYYYMMDD.csv`（直近14日分を残す）
- 結果 → `保存先\最終取得.txt`、詳しい記録 → `%APPDATA%\DentamapAuto\log.txt`

## 初回設定
1. ZIPを展開し、中の `DentamapAuto` フォルダごと分かりやすい場所（例：`C:\DentamapAuto\`）に置く（後で動かさない）
2. ダブルクリック →「WindowsによってPCが保護されました」が出たら「詳細情報」→「実行」
3. ユーザー名・パスワード・保存先・実行時刻を入れて「今すぐテスト」→ 成功したら「保存して毎日実行を登録」

パスワードはWindowsの暗号化機能（DPAPI）で保存され、設定したパソコン・ユーザー以外では読めません。

## 注意
- 実行時刻にパソコンの電源が入っていてWindowsにサインインしている必要があります（電源が切れていた日は、次に起動したときに実行されます）
- デンタマップの画面・仕組みが変わると動かなくなることがあります
- `future_csv.py` はデンタマップのHTMLにある `csv_make()` をPythonに移したもので、出力は本物の「CSVダウンロード」と一致することを確認済み

## ビルド
`main` に push すると GitHub Actions（windows-latest）で `DentamapAuto.exe` が作られ、Actions の Artifacts からダウンロードできます。
