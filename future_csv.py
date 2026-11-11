"""デンタマップ「未来分」HTMLから、HTML内の「CSVダウンロード」と同じCSVを作る。
デンタマップのHTMLに入っている csv_make() をPythonに移したもの。"""
import html, re

CANCEL = {"1": "TELキャンセル", "2": "無断キャンセル", "3": "NETキャンセル",
          "4": "TELキャンセル(変更)", "5": "NETキャンセル(変更)"}
HEADER = ("患者番号,患者名,予約日,予約開始時間,予約終了時間,ユニット,診療内容,"
          "担当１,担当２,担当３,部位番号,カラー番号,技工,予約記号,キャンセル,MEMO,Apo link\n")


def _tag(block, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
    return html.unescape(m.group(1)) if m else ""


def make_csv_text(page: str) -> str:
    units = re.search(r"g_unit_array\s*=\s*new Array\((.*?)\);", page, re.S)
    unit_names = re.findall(r'"(.*?)"', units.group(1)) if units else []
    m = re.search(r"var p_num\s*=\s*(\d+)", page)
    p_num = int(m.group(1)) if m else 0
    staff = {}
    m = re.search(r'<div id="staff_data">(.*?)</div>', page, re.S)
    if m:
        for part in re.split(r"<br\s*/?>", m.group(1), flags=re.I):
            if "," in part:
                k, v = part.split(",", 1)
                staff[k] = v
    sec = page[page.find('<div id="csv_data">'):page.find('<div id="day_memo_data">')]

    rows, links, ids = [], [], []
    link_no, counter = {}, 0
    for dm in re.finditer(r'<date id\s*=\s*"(\d{8})">(.*?)</date>', sec, re.S):
        date = dm.group(1)
        for lm in re.finditer(r'<list id\s*=\s*"(\d+)">(.*?)</list>', dm.group(2), re.S):
            yid, b = lm.group(1), lm.group(2)
            if not b.strip():
                continue
            r_id = _tag(b, "r_id")
            if r_id == "@system2":
                continue
            unit = _tag(b, "unit")
            try:
                ui = int(unit)
            except ValueError:
                ui = None
            if ui is not None and 0 <= ui < len(unit_names) and unit_names[ui]:
                unit = unit_names[ui]
            elif ui is not None:
                unit = ("列%d" if ui <= p_num else "予備%d") % ui
            try:
                color = str(int(_tag(b, "u_color"), 16))
            except ValueError:
                color = "NaN"
            g = int(_tag(b, "gikou") or 0)
            disp_g = "技工" if g & 1 else ""
            kigou = "".join(s for bit, s in ((2, "● "), (4, "▲ "), (8, "■ "), (16, "★ "), (32, "◆ "), (64, "◎")) if g & bit)
            def st(tag):
                t = _tag(b, tag)
                return staff[t].split("<br>")[0] if t not in ("", "0") and t in staff else ""
            bui = _tag(b, "bui")
            link = _tag(b, "link_data")
            if link and link not in link_no:
                counter += 1
                link_no[link] = counter
            cancel = CANCEL.get(_tag(b, "cancel"), "")
            if r_id == "@system":
                r_id, memo, bui = "system", bui, ""
            else:
                memo = _tag(b, "memo")
            memo = memo.replace("\r\n", "　").replace("\n", "　").replace("\r", "　")
            rows.append(",".join([r_id, _tag(b, "title1"), date, _tag(b, "sdate"), _tag(b, "edate"), unit,
                                  _tag(b, "med"), st("t1"), st("t2"), st("t3"), bui, color, disp_g, kigou,
                                  cancel, memo]) + ",")
            links.append(link)
            ids.append(yid)
    out = [HEADER]
    for row, link, yid in zip(rows, links, ids):
        lt = ""
        if link:
            lt = "C%d" % link_no[link]
        elif yid in link_no:
            lt = "P%d" % link_no[yid]
        out.append(row + lt + "\n")
    return "".join(out)
