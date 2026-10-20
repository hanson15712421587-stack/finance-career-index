# -*- coding: utf-8 -*-
"""抓取实时财经数据并写入 data/live.json（本地与 GitHub Actions 共用）"""
import json
import datetime
import pathlib
import argparse
import math
import sys

import akshare as ak

OUT = pathlib.Path(__file__).parent / "data" / "live.json"


def num(v, default=None):
    try:
        f = float(v)
        return round(f, 4) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return default


def fetch_treasury():
    df = ak.bond_zh_us_rate(start_date="20250101")
    date_col = df.columns[0]
    cols = {c: c for c in df.columns}
    cn2 = next(c for c in df.columns if "中国" in c and "2年" in c)
    cn5 = next(c for c in df.columns if "中国" in c and "5年" in c and "美国" not in c)
    cn10 = next(c for c in df.columns if "中国" in c and "10年" in c and "-" not in c)
    us10 = next(c for c in df.columns if "美国" in c and "10年" in c and "-" not in c)
    df = df.dropna(subset=[cn10])
    hist = [
        [str(r[date_col])[:10], num(r[cn10])]
        for _, r in df.tail(180).iterrows()
        if num(r[cn10]) is not None
    ]
    last = df.iloc[-1]
    return {
        "date": str(last[date_col])[:10],
        "cn2": num(last[cn2]),
        "cn5": num(last[cn5]),
        "cn10": num(last[cn10]),
        "us10": num(last[us10]),
        "spread_10y2y": round(num(last[cn10]) - num(last[cn2]), 4),
        "cn10_hist": hist,
    }


def fetch_indices():
    want = ["上证指数", "沪深300", "创业板指", "深证成指", "科创50"]
    try:
        df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        src = "东财"
        rows = {r["名称"]: r for _, r in df.iterrows()}
        get = lambda w: rows.get(w)
    except Exception:
        df = ak.stock_zh_index_spot_sina()
        src = "新浪财经"
        rows = {r["名称"].replace("Ａ股指数", "A股指数"): r for _, r in df.iterrows()}
        get = lambda w: rows.get(w)
    out = []
    for w in want:
        r = get(w)
        if r is not None:
            out.append({"name": w, "price": num(r.get("最新价")), "chg": num(r.get("涨跌幅"))})
    return {"src": src, "items": out}


def fetch_industries():
    try:
        df = ak.stock_board_industry_name_em()
        df = df.dropna(subset=["涨跌幅"]).sort_values("涨跌幅", ascending=False)
        src, name_col = "东财", "板块名称"

        def pack(rows):
            return [{"name": r[name_col], "code": str(r["代码"]), "chg": num(r["涨跌幅"])}
                    for _, r in rows.iterrows()]
    except Exception:
        df = ak.stock_board_industry_summary_ths()
        df = df.dropna(subset=["涨跌幅"]).sort_values("涨跌幅", ascending=False)
        src, name_col = "同花顺", "板块"

        def pack(rows):
            return [{"name": r[name_col], "code": "", "chg": num(r["涨跌幅"])}
                    for _, r in rows.iterrows()]
    return {"src": src, "up": pack(df.head(8)), "down": pack(df.tail(5)[::-1])}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=OUT.with_name("live.candidate.json"))
    args = parser.parse_args(argv)
    failed = False
    data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc)
        .astimezone(datetime.timezone(datetime.timedelta(hours=8)))
        .strftime("%Y-%m-%d %H:%M:%S"),
        "source": "akshare / 东方财富公开数据",
    }
    for key, fn in [("treasury", fetch_treasury), ("indices", fetch_indices), ("industries", fetch_industries)]:
        try:
            data[key] = fn()
        except Exception as e:  # Collect all failures for diagnosis, but never report success
            failed = True
            data[key] = None
            data.setdefault("errors", {})[key] = str(e)[:200]
            print(json.dumps({"event": "fetch_error", "section": key, "level": "ERROR", "exception_type": type(e).__name__, "message": str(e)[:200]}, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    print("written", args.output, data["updated_at"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
