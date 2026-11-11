"""デンタマップ予約データ 自動取得アプリ

使い方
  ・ダブルクリック          → 設定画面（ID・パスワード・保存先・実行時刻を登録）
  ・DentamapAuto.exe --run → 取得を1回実行（タスク スケジューラから毎日呼ばれる）

毎回取得するもの
  ・先月と今月の月次CSV（バックアップ画面「過去分」csv形式と同じ）  → 保存先\\月次\\YYYYMMデータ.csv
  ・未来分180日のCSV（「未来分」HTMLの「CSVダウンロード」と同じ）   → 保存先\\未来分\\yoyaku_dataYYYYMMDD.csv
"""
import base64
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback

import requests

from future_csv import make_csv_text

APP_NAME = "DentamapAuto"
BASE = "https://plus.dentamap.jp/apl/"
LOGIN_URL = BASE + "index.php"
MAIN_URL = BASE + "main.php"
LOAD_URL = BASE + "com_handler/tmp/download_file_load.html"
FUTURE_DAYS = 180
PRINT_FLG = "1,1,1,1,1,1,0,0,0"
KEEP_FUTURE_FILES = 14
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

APPDATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(APPDATA_DIR, "config.json")
LOG_PATH = os.path.join(APPDATA_DIR, "log.txt")


# ---------------------------------------------------------------- 記録
def log(msg):
    os.makedirs(APPDATA_DIR, exist_ok=True)
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


# ---------------------------------------------------------------- パスワードの暗号化（Windows DPAPI）
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text: str) -> str:
    src, _keep = _blob(text.encode("utf-8"))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("パスワードの暗号化に失敗しました")
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return base64.b64encode(data).decode()


def unprotect(token: str) -> str:
    src, _keep = _blob(base64.b64decode(token))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("パスワードを読み出せませんでした（設定したユーザーと別のユーザーで実行していませんか）")
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return data.decode("utf-8")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    os.makedirs(APPDATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- ログイン
LOGIN_API = BASE + "dmp_first_login.php"
LOGIN_ERRORS = {
    "dp_type6_err": "料金のお支払いが確認できなかったため、デンタマップが使えない状態です",
    "dp_sys_err": "デンタマップ側のエラーです",
}


def _post_handler(s: requests.Session, path: str, obj: dict, user: dict):
    """画面の load_data() と同じ送り方：data=JSON（user付き）, auto_load=JSON"""
    body = dict(obj)
    body["user"] = user
    r = s.post(BASE + "com_handler/" + path,
               data={"data": json.dumps(body, ensure_ascii=False), "auto_load": "{}"},
               headers={"X-Requested-With": "XMLHttpRequest", "Referer": MAIN_URL}, timeout=120)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("%s の応答を読めませんでした HTTP %s" % (path, r.status_code))


def login(s: requests.Session, user: str, password: str) -> dict:
    """ログイン画面のボタンと同じ通信でログインし、予約画面を開いたときと同じ初期読み込みまで行う。"""
    s.get(LOGIN_URL, timeout=30)
    r = s.post(LOGIN_API, data={"u": user, "p": password}, timeout=30,
               headers={"X-Requested-With": "XMLHttpRequest", "Referer": LOGIN_URL})
    try:
        res = r.json()
    except ValueError:
        raise RuntimeError("ログインの応答を読めませんでした（デンタマップの仕組みが変わった可能性があります） HTTP %s" % r.status_code)
    d = res.get("data") or {}
    err = str(d.get("err_type") or "")
    if err in LOGIN_ERRORS:
        raise RuntimeError("%s：%s" % (LOGIN_ERRORS[err], res.get("err_text") or d.get("err_text") or ""))
    if str(res.get("res")) != "1":
        raise RuntimeError("ログインできませんでした（ユーザー名かパスワードが違う可能性があります）")
    info = {"id": d.get("u"), "pass": d.get("p"), "ip": d.get("i")}
    first = _post_handler(s, "loginfirst.html", {}, info)
    if str(first.get("res")) != "1":
        raise RuntimeError("予約画面の読み込みに失敗しました（res=%s）" % first.get("res"))
    try:
        m = first["data"]["master_info"][0]
        info["dev_flg"] = m.get("dev_flg")
        info["version"] = m.get("version")
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("予約画面の情報を読めませんでした（デンタマップの仕組みが変わった可能性があります）")
    log("ログインしました（バージョン %s）" % info.get("version"))
    return info


# ---------------------------------------------------------------- ダウンロード
def _download(s: requests.Session, info: dict, kind: str, params: dict, ok) -> bytes:
    """画面のダウンロードボタンと同じ：受付（download_file_session_start）→ 取得（download_file_load）"""
    for attempt in range(3):
        res = _post_handler(s, "tmp/download_file_session_start.html", {"kind": kind}, info)
        if str(res.get("res")) == "1":
            q = {"r": str(int(time.time() * 1000))}
            q.update(params)
            r = s.get(LOAD_URL, params=q, timeout=180, headers={"Referer": MAIN_URL})
            if r.status_code == 200 and ok(r.content):
                return r.content
            log("取得をやり直します（%s, HTTP %s, %dバイト）" % (params, r.status_code, len(r.content)))
        else:
            log("ダウンロードの受付に失敗（%s, res=%s）" % (kind, res.get("res")))
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("ダウンロードに失敗しました: %s %s" % (kind, params))


def _is_month_csv(b: bytes):
    try:
        return "予約明細" in b.decode("cp932", errors="ignore")
    except Exception:
        return False


def _is_future_html(b: bytes):
    return b'id="csv_data"' in b


def run_once(scheduled: bool = False):
    cfg = load_config()
    if not cfg:
        raise RuntimeError("設定がありません。先に DentamapAuto.exe をダブルクリックして設定してください。")
    if scheduled and cfg.get("skip_sunday", True) and dt.date.today().weekday() == 6:
        log("日曜日なので取得しません")
        return
    out = cfg["folder"]
    month_dir = os.path.join(out, "月次")
    future_dir = os.path.join(out, "未来分")
    os.makedirs(month_dir, exist_ok=True)
    os.makedirs(future_dir, exist_ok=True)

    s = requests.Session()
    s.headers["User-Agent"] = UA
    info = login(s, cfg["user"], unprotect(cfg["password"]))

    today = dt.date.today()
    first = today.replace(day=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    for m in (prev, first):
        ym = m.strftime("%Y%m")
        data = _download(s, info, "yoyaku_data", {"date": ym}, _is_month_csv)
        path = os.path.join(month_dir, "%sデータ.csv" % ym)
        with open(path, "wb") as f:
            f.write(data)
        log("月次CSVを保存: %s" % path)

    ymd = today.strftime("%Y%m%d")
    page = _download(s, info, "backup_list", {"date": ymd, "no": str(FUTURE_DAYS), "print_flg": PRINT_FLG}, _is_future_html)
    text = make_csv_text(page.decode("utf-8", errors="replace"))
    path = os.path.join(future_dir, "yoyaku_data%s.csv" % ymd)
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + text.encode("utf-8"))
    log("未来分CSVを保存: %s（%d行）" % (path, text.count("\n") - 1))

    olds = sorted(n for n in os.listdir(future_dir) if re.fullmatch(r"yoyaku_data\d{8}\.csv", n))
    for n in olds[:-KEEP_FUTURE_FILES]:
        try:
            os.remove(os.path.join(future_dir, n))
        except OSError:
            pass
    with open(os.path.join(out, "最終取得.txt"), "w", encoding="utf-8") as f:
        f.write("最終取得 %s　成功\n" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))


# ---------------------------------------------------------------- タスク スケジューラ登録
def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)


def register_task(hhmm: str, skip_sunday: bool = True):
    exe = exe_path()
    cmd, args = (exe, "--run") if getattr(sys, "frozen", False) else (sys.executable, '"%s" --run' % exe)
    start = "%sT%s:00" % (dt.date.today().isoformat(), hhmm)
    if skip_sunday:
        schedule = ("<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek>"
                    "<Monday/><Tuesday/><Wednesday/><Thursday/><Friday/><Saturday/>"
                    "</DaysOfWeek></ScheduleByWeek>")
    else:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>デンタマップ予約データの自動取得</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
    {schedule}</CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{html.escape(cmd)}</Command><Arguments>{html.escape(args)}</Arguments></Exec></Actions>
</Task>"""
    tmp = os.path.join(tempfile.gettempdir(), "dentamap_task.xml")
    with open(tmp, "w", encoding="utf-16") as f:
        f.write(xml)
    r = subprocess.run(["schtasks", "/Create", "/TN", APP_NAME, "/XML", tmp, "/F"],
                       capture_output=True, text=True, creationflags=0x08000000)
    os.remove(tmp)
    if r.returncode != 0:
        raise RuntimeError("タスク スケジューラへの登録に失敗しました: " + (r.stderr or r.stdout))


# ---------------------------------------------------------------- 設定画面
def setup_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    cfg = load_config() or {}
    root = tk.Tk()
    root.title("デンタマップ 予約データ自動取得 設定")
    root.resizable(False, False)
    pad = {"padx": 10, "pady": 6}

    tk.Label(root, text="デンタマップ ユーザー名").grid(row=0, column=0, sticky="w", **pad)
    e_user = tk.Entry(root, width=36)
    e_user.insert(0, cfg.get("user", ""))
    e_user.grid(row=0, column=1, columnspan=2, **pad)

    tk.Label(root, text="パスワード").grid(row=1, column=0, sticky="w", **pad)
    e_pw = tk.Entry(root, width=36, show="●")
    e_pw.grid(row=1, column=1, columnspan=2, **pad)
    if cfg.get("password"):
        tk.Label(root, text="（変更しない場合は空欄のまま）", fg="gray").grid(row=2, column=1, sticky="w", padx=10)

    tk.Label(root, text="保存先フォルダ").grid(row=3, column=0, sticky="w", **pad)
    v_folder = tk.StringVar(value=cfg.get("folder", os.path.join(os.path.expanduser("~"), "Documents", "デンタマップ予約データ")))
    tk.Entry(root, textvariable=v_folder, width=36).grid(row=3, column=1, **pad)
    tk.Button(root, text="選ぶ", command=lambda: v_folder.set(filedialog.askdirectory() or v_folder.get())).grid(row=3, column=2, **pad)

    tk.Label(root, text="毎日の実行時刻").grid(row=4, column=0, sticky="w", **pad)
    e_time = tk.Entry(root, width=8)
    e_time.insert(0, cfg.get("time", "20:00"))
    e_time.grid(row=4, column=1, sticky="w", **pad)

    v_sun = tk.BooleanVar(value=cfg.get("skip_sunday", True))
    tk.Checkbutton(root, text="日曜日は取得しない", variable=v_sun).grid(row=4, column=1, columnspan=2, sticky="e", **pad)

    status = tk.Label(root, text="", fg="blue", wraplength=420, justify="left")
    status.grid(row=6, column=0, columnspan=3, sticky="w", **pad)

    def collect():
        user, pw, folder, hhmm = e_user.get().strip(), e_pw.get(), v_folder.get().strip(), e_time.get().strip()
        if not user or not folder or not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            raise ValueError("ユーザー名・保存先・時刻（例 20:00）を入れてください")
        if not pw and not cfg.get("password"):
            raise ValueError("パスワードを入れてください")
        new = {"user": user, "folder": folder, "time": "%02d:%s" % (int(hhmm.split(":")[0]), hhmm.split(":")[1]),
               "password": protect(pw) if pw else cfg["password"], "skip_sunday": bool(v_sun.get())}
        return new

    def on_save():
        try:
            new = collect()
            save_config(new)
            cfg.update(new)
            register_task(new["time"], new["skip_sunday"])
            days = "月〜土曜日" if new["skip_sunday"] else "毎日"
            messagebox.showinfo("登録しました", "%s の %s に自動で取得します。\n「今すぐテスト」で一度動作を確認してください。" % (days, new["time"]))
        except Exception as e:
            messagebox.showerror("エラー", str(e))

    def on_test():
        try:
            new = collect()
            save_config(new)
            cfg.update(new)
            status.config(text="取得中です（1〜2分かかります）…", fg="blue")
            root.update()
            run_once()
            status.config(text="成功しました。保存先：%s" % new["folder"], fg="green")
            os.startfile(new["folder"])
        except Exception as e:
            log("テスト失敗: %s" % e)
            status.config(text="失敗しました：%s" % e, fg="red")

    tk.Button(root, text="保存して毎日実行を登録", command=on_save, width=24).grid(row=5, column=1, sticky="w", **pad)
    tk.Button(root, text="今すぐテスト", command=on_test, width=14).grid(row=5, column=2, **pad)
    root.mainloop()


def main():
    if "--run" in sys.argv:
        try:
            run_once(scheduled=True)
        except Exception as e:
            log("失敗: %s\n%s" % (e, traceback.format_exc()))
            cfg = load_config()
            if cfg:
                try:
                    with open(os.path.join(cfg["folder"], "最終取得.txt"), "w", encoding="utf-8") as f:
                        f.write("最終取得 %s　失敗：%s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M"), e))
                except OSError:
                    pass
            sys.exit(1)
    else:
        p = exe_path().lower()
        if getattr(sys, "frozen", False) and ".zip" in p and "\\temp\\" in p:
            import tkinter as tk
            from tkinter import messagebox
            tk.Tk().withdraw()
            messagebox.showwarning(
                "ZIPを展開してください",
                "ZIPの中から直接開いています。\n\nZIPを右クリック →「すべて展開」で取り出し、"
                "取り出した DentamapAuto.exe を C:\\DentamapAuto などの決まった場所に置いてから開いてください。")
            return
        setup_gui()


if __name__ == "__main__":
    main()
