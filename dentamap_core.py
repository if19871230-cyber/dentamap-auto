"""デンタマップへのログインと予約データ取得（パソコン版・クラウド版で共通）"""
import json
import time

import requests

BASE = "https://plus.dentamap.jp/apl/"
LOGIN_URL = BASE + "index.php"
MAIN_URL = BASE + "main.php"
LOAD_URL = BASE + "com_handler/tmp/download_file_load.html"
LOGIN_API = BASE + "dmp_first_login.php"
FUTURE_DAYS = 180
PRINT_FLG = "1,1,1,1,1,1,0,0,0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
LOGIN_ERRORS = {
    "dp_type6_err": "料金のお支払いが確認できなかったため、デンタマップが使えない状態です",
    "dp_sys_err": "デンタマップ側のエラーです",
}

log = print


def set_logger(f):
    global log
    log = f


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


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
