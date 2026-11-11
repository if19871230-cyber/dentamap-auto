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
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from future_csv import make_csv_text

APP_NAME = "DentamapAuto"
BASE = "https://plus.dentamap.jp/apl/"
LOGIN_URL = BASE + "index.php"
MAIN_URL = BASE + "main.php"
SESSION_URL = BASE + "com_handler/tmp/download_file_session_start.html"
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
class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms, self._cur = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._cur = {"action": a.get("action", ""), "method": (a.get("method") or "get").lower(), "inputs": []}
            self.forms.append(self._cur)
        elif tag == "input" and self._cur is not None:
            self._cur["inputs"].append(a)

    def handle_endtag(self, tag):
        if tag == "form":
            self._cur = None


def _is_login_page(resp):
    return "index.php" in resp.url or 'type="password"' in resp.text or "type='password'" in resp.text


def login(s: requests.Session, user: str, password: str):
    r = s.get(LOGIN_URL, timeout=30)
    r.encoding = r.apparent_encoding or "utf-8"
    p = _FormParser()
    p.feed(r.text)
    form = next((f for f in p.forms if any((i.get("type") or "").lower() == "password" for i in f["inputs"])), None)
    if form is None:
        raise RuntimeError("ログイン画面の入力欄が見つかりません（デンタマップの画面が変わった可能性があります）")
    data, user_set = {}, False
    for i in form["inputs"]:
        name, typ = i.get("name"), (i.get("type") or "text").lower()
        if not name:
            continue
        if typ == "password":
            data[name] = password
        elif typ in ("text", "email", "") and not user_set:
            data[name] = user
            user_set = True
        elif typ in ("hidden", "submit"):
            data[name] = i.get("value", "")
        elif typ == "checkbox" and "checked" in i:
            data[name] = i.get("value", "on")
    action = urljoin(r.url, form["action"] or r.url)
    if form["method"] == "post":
        r2 = s.post(action, data=data, timeout=30, allow_redirects=True)
    else:
        r2 = s.get(action, params=data, timeout=30, allow_redirects=True)
    check = s.get(MAIN_URL, timeout=30, allow_redirects=True)
    check.encoding = check.apparent_encoding or "utf-8"
    if _is_login_page(check):
        raise RuntimeError("ログインできませんでした（IDかパスワードが違う、またはログインの仕組みが変わった可能性があります）")
    log("ログインしました")


# ---------------------------------------------------------------- ダウンロード
def _download(s: requests.Session, params: dict, ok) -> bytes:
    """画面のダウンロードボタンと同じ順番（受付 → 取得）で取る。"""
    for attempt in range(3):
        try:
            s.post(SESSION_URL, data=params, timeout=60)
        except requests.RequestException:
            pass
        q = dict(params)
        q["r"] = str(int(time.time() * 1000))
        r = s.get(LOAD_URL, params=q, timeout=180)
        if r.status_code == 200 and ok(r.content):
            return r.content
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("ダウンロードに失敗しました: %s" % params)


def _is_month_csv(b: bytes):
    try:
        return "予約明細" in b.decode("cp932", errors="ignore")
    except Exception:
        return False


def _is_future_html(b: bytes):
    return b'id="csv_data"' in b


def run_once():
    cfg = load_config()
    if not cfg:
        raise RuntimeError("設定がありません。先に DentamapAuto.exe をダブルクリックして設定してください。")
    out = cfg["folder"]
    month_dir = os.path.join(out, "月次")
    future_dir = os.path.join(out, "未来分")
    os.makedirs(month_dir, exist_ok=True)
    os.makedirs(future_dir, exist_ok=True)

    s = requests.Session()
    s.headers["User-Agent"] = UA
    login(s, cfg["user"], unprotect(cfg["password"]))

    today = dt.date.today()
    first = today.replace(day=1)
    prev = (first - dt.timedelta(days=1)).replace(day=1)
    for m in (prev, first):
        ym = m.strftime("%Y%m")
        data = _download(s, {"date": ym}, _is_month_csv)
        path = os.path.join(month_dir, "%sデータ.csv" % ym)
        with open(path, "wb") as f:
            f.write(data)
        log("月次CSVを保存: %s" % path)

    ymd = today.strftime("%Y%m%d")
    page = _download(s, {"date": ymd, "no": str(FUTURE_DAYS), "print_flg": PRINT_FLG}, _is_future_html)
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


def register_task(hhmm: str):
    exe = exe_path()
    cmd, args = (exe, "--run") if getattr(sys, "frozen", False) else (sys.executable, '"%s" --run' % exe)
    start = "%sT%s:00" % (dt.date.today().isoformat(), hhmm)
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>デンタマップ予約データの自動取得</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
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

    status = tk.Label(root, text="", fg="blue", wraplength=420, justify="left")
    status.grid(row=6, column=0, columnspan=3, sticky="w", **pad)

    def collect():
        user, pw, folder, hhmm = e_user.get().strip(), e_pw.get(), v_folder.get().strip(), e_time.get().strip()
        if not user or not folder or not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            raise ValueError("ユーザー名・保存先・時刻（例 20:00）を入れてください")
        if not pw and not cfg.get("password"):
            raise ValueError("パスワードを入れてください")
        new = {"user": user, "folder": folder, "time": "%02d:%s" % (int(hhmm.split(":")[0]), hhmm.split(":")[1]),
               "password": protect(pw) if pw else cfg["password"]}
        return new

    def on_save():
        try:
            new = collect()
            save_config(new)
            cfg.update(new)
            register_task(new["time"])
            messagebox.showinfo("登録しました", "毎日 %s に自動で取得します。\n「今すぐテスト」で一度動作を確認してください。" % new["time"])
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
            run_once()
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
        setup_gui()


if __name__ == "__main__":
    main()
