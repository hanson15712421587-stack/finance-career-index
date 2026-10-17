"""Offline snapshot validation and atomic publication; standard library only."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))
NAMES = {"上证指数", "沪深300", "创业板指", "深证成指", "科创50"}


def validate(data, now=None):
    now = now or dt.datetime.now(TZ)
    issues = []

    def issue(level, rule, section, field, value, message):
        issues.append(dict(level=level, rule=rule, section=section,
                           field=field, value=value, message=message))

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def date(value):
        try:
            return dt.date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    if not isinstance(data, dict):
        issue("ERROR", "ROOT_SCHEMA", "snapshot", "$", None, "Expected object")
        return issues
    try:
        stamp = dt.datetime.strptime(data.get("updated_at", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        age = (now - stamp).total_seconds()
        if age < -300 or age > 86400:
            issue("ERROR", "SNAPSHOT_AGE", "snapshot", "updated_at", data.get("updated_at"), "Future >5min or age >24h")
    except (TypeError, ValueError):
        issue("ERROR", "SNAPSHOT_TIME", "snapshot", "updated_at", data.get("updated_at"), "Invalid Shanghai timestamp")
    for section in ("treasury", "indices", "industries"):
        obj = data.get(section)
        if not isinstance(obj, dict):
            issue("ERROR", "SECTION_MISSING", section, section, obj, "Required section missing/null")
            continue
        fetch_errors = data.get("errors", {})
        if isinstance(fetch_errors, dict) and section in fetch_errors:
            issue("ERROR", "FETCH_ERROR", section, "errors", data["errors"][section], "Collector reported failure")
        if section == "treasury":
            day = date(obj.get("date"))
            age = (now.date() - day).days if day else None
            if age is None or age < 0 or age > 14:
                issue("ERROR", "TREASURY_DATE", section, "date", obj.get("date"), "Invalid/future or older than 14 calendar days")
            elif age > 3:
                issue("WARNING", "TREASURY_STALE", section, "date", obj["date"], "Older than 3 calendar days; holiday calendar not modeled")
            for field in ("cn2", "cn5", "cn10", "us10", "spread_10y2y"):
                v = obj.get(field)
                if not number(v) or (field != "spread_10y2y" and not -5 <= v <= 30):
                    issue("ERROR", "TREASURY_NUMBER", section, field, v, "Expected finite yield in [-5,30] percent")
            if all(number(obj.get(k)) for k in ("cn2", "cn10", "spread_10y2y")) and abs(obj["cn10"] - obj["cn2"] - obj["spread_10y2y"]) > 0.0002:
                issue("ERROR", "TREASURY_SPREAD", section, "spread_10y2y", obj["spread_10y2y"], "Spread inconsistent with cn10-cn2")
            hist = obj.get("cn10_hist")
            valid = isinstance(hist, list) and bool(hist)
            previous = None
            if valid:
                for row in hist:
                    if not isinstance(row, list) or len(row) != 2:
                        valid = False
                        break
                    d = date(row[0])
                    if not d or not number(row[1]) or not -5 <= row[1] <= 30 or (previous and d <= previous):
                        valid = False
                        break
                    previous = d
                valid = valid and previous == day and number(obj.get("cn10")) and abs(hist[-1][1] - obj["cn10"]) <= 0.0002
            if not valid:
                issue("ERROR", "TREASURY_HISTORY", section, "cn10_hist", None, "History must be ordered, unique, finite and end at current date/value")
            continue
        src = obj.get("src")
        allowed = {"东财", "新浪财经"} if section == "indices" else {"东财", "同花顺"}
        if not isinstance(src, str) or src not in allowed:
            issue("ERROR", "SOURCE_UNKNOWN", section, "src", src, "Unrecognized provider")
        elif src != "东财":
            issue("WARNING", "SOURCE_FALLBACK", section, "src", src, "Fallback provider used")
        issue("WARNING", "QUOTE_TIME_UNVERIFIED", section, "src", src, "Provider quote timestamp unavailable; fetch time cannot prove quote freshness")
        groups = ("items",) if section == "indices" else ("up", "down")
        for group in groups:
            rows = obj.get(group)
            expected = 5 if group in ("items", "down") else 8
            if not isinstance(rows, list) or len(rows) != expected:
                issue("ERROR", "ROW_COUNT", section, group, len(rows) if isinstance(rows, list) else None, f"Expected {expected} rows")
            if not isinstance(rows, list):
                continue
            names, codes, changes = [], [], []
            for i, row in enumerate(rows):
                field = f"{group}[{i}]"
                if not isinstance(row, dict):
                    issue("ERROR", "ROW_SCHEMA", section, field, None, "Expected object")
                    continue
                name = row.get("name")
                if not isinstance(name, str) or not name.strip() or name in names:
                    issue("ERROR", "NAME_INVALID", section, field + ".name", name, "Missing/duplicate name")
                names.append(name)
                chg = row.get("chg")
                if not number(chg) or abs(chg) > 100:
                    issue("ERROR", "CHANGE_INVALID", section, field + ".chg", chg, "Expected finite percent in [-100,100]")
                else:
                    changes.append(chg)
                if section == "indices":
                    if not number(row.get("price")) or row["price"] <= 0:
                        issue("ERROR", "PRICE_INVALID", section, field + ".price", row.get("price"), "Expected positive finite price")
                else:
                    code = row.get("code")
                    if not isinstance(code, str) or not code.strip() or code.lower() in ("nan", "none") or code in codes:
                        issue("ERROR", "INDUSTRY_CODE", section, field + ".code", code, "Missing/duplicate provider code")
                    codes.append(code)
            if section == "indices" and (len(names) != 5 or any(n not in NAMES for n in names) or len(set(str(n) for n in names)) != 5):
                issue("ERROR", "INDEX_SET", section, group, names, "Required five indices must appear exactly once")
            if section == "industries" and changes != sorted(changes, reverse=group == "up"):
                issue("ERROR", "INDUSTRY_ORDER", section, group, changes, "up descending/down ascending required")
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--publish", type=Path)
    parser.add_argument("--now", help="ISO timestamp with timezone, for reproducible audits")
    args = parser.parse_args(argv)
    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(TZ)
    try:
        data = json.loads(args.candidate.read_text(encoding="utf-8"))
        issues = validate(data, now)
    except (OSError, ValueError) as exc:
        issues = [dict(level="ERROR", rule="JSON_READ", section="snapshot", field="$", value=None, message=str(exc))]
    for item in issues:
        print(json.dumps(dict(event="quality_rule", checked_at=now.isoformat(), candidate=str(args.candidate), **item), ensure_ascii=False, default=str))
    errors = sum(i["level"] == "ERROR" for i in issues)
    print(json.dumps(dict(event="quality_summary", checked_at=now.isoformat(), errors=errors,
                         warnings=len(issues)-errors, status="blocked" if errors else "passed")))
    if errors:
        return 1
    if args.publish:
        args.publish.parent.mkdir(parents=True, exist_ok=True)
        os.replace(args.candidate, args.publish)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
