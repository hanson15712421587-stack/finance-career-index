import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from quality_gate import TZ, main, validate

NOW = dt.datetime(2026, 10, 10, 12, tzinfo=TZ)


def good():
    return {"updated_at": "2026-10-10 11:39:09",
            "treasury": {"date": "2026-10-09", "cn2": 1, "cn5": 1.5,
                         "cn10": 2, "us10": 4, "spread_10y2y": 1,
                         "cn10_hist": [["2026-10-09", 2]]},
            "indices": {"src": "东财", "items": [
                {"name": n, "price": 100, "chg": 0}
                for n in ["上证指数", "沪深300", "创业板指", "深证成指", "科创50"]]},
            "industries": {"src": "东财",
                           "up": [{"name": f"u{i}", "code": f"U{i}", "chg": 8-i} for i in range(8)],
                           "down": [{"name": f"d{i}", "code": f"D{i}", "chg": -5+i} for i in range(5)]}}


class GateTests(unittest.TestCase):
    def errors(self, data):
        return [i["rule"] for i in validate(data, NOW) if i["level"] == "ERROR"]

    def test_good_and_fallback(self):
        data = good()
        self.assertEqual(self.errors(data), [])
        data["indices"]["src"] = "新浪财经"
        data["industries"]["src"] = "同花顺"
        self.assertEqual(self.errors(data), [])
        self.assertEqual(sum(i["rule"] == "SOURCE_FALLBACK" for i in validate(data, NOW)), 2)

    def test_missing_each_section(self):
        for key in ("treasury", "indices", "industries"):
            with self.subTest(key=key):
                data = good()
                data[key] = None
                self.assertIn("SECTION_MISSING", self.errors(data))

    def test_invalid_mutations(self):
        cases = [
            ("TREASURY_DATE", lambda d: d["treasury"].update(date="2026-09-01")),
            ("TREASURY_SPREAD", lambda d: d["treasury"].update(spread_10y2y=5)),
            ("TREASURY_HISTORY", lambda d: d["treasury"].update(cn10_hist=[])),
            ("TREASURY_NUMBER", lambda d: d["treasury"].update(cn2=float("nan"))),
            ("ROW_COUNT", lambda d: d["indices"]["items"].pop()),
            ("PRICE_INVALID", lambda d: d["indices"]["items"][0].update(price=True)),
            ("CHANGE_INVALID", lambda d: d["industries"]["up"][0].update(chg=float("inf"))),
            ("INDUSTRY_CODE", lambda d: d["industries"]["up"][0].update(code="")),
            ("INDUSTRY_ORDER", lambda d: d["industries"]["up"].reverse()),
            ("SOURCE_UNKNOWN", lambda d: d["indices"].update(src="unknown")),
            ("SNAPSHOT_AGE", lambda d: d.update(updated_at="2026-10-01 12:00:00")),
        ]
        for rule, mutate in cases:
            with self.subTest(rule=rule):
                data = good()
                mutate(data)
                self.assertIn(rule, self.errors(data))

    def test_holiday_warning(self):
        data = good()
        data["treasury"]["date"] = "2026-10-01"
        data["treasury"]["cn10_hist"][0][0] = "2026-10-01"
        self.assertEqual(self.errors(data), [])
        self.assertIn("TREASURY_STALE", [i["rule"] for i in validate(data, NOW)])

    def test_publish_and_block_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            live, candidate = Path(temp)/"live.json", Path(temp)/"candidate.json"
            live.write_bytes(b"previous")
            for content in ('{"treasury": null}', '{bad'):
                candidate.write_text(content)
                self.assertEqual(main([str(candidate), "--publish", str(live), "--now", NOW.isoformat()]), 1)
                self.assertEqual(live.read_bytes(), b"previous")
            candidate.write_text(json.dumps(good()))
            expected = candidate.read_bytes()
            self.assertEqual(main([str(candidate), "--publish", str(live), "--now", NOW.isoformat()]), 0)
            self.assertEqual(live.read_bytes(), expected)
            self.assertFalse(candidate.exists())

    def test_collector_each_failure_returns_nonzero(self):
        with patch.dict(sys.modules, {"akshare": types.ModuleType("akshare")}):
            spec = importlib.util.spec_from_file_location("collector", "update_data.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        for failing in ("fetch_treasury", "fetch_indices", "fetch_industries"):
            with self.subTest(failing=failing), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)/"candidate.json"
                with patch.object(module, "fetch_treasury", return_value={}), patch.object(module, "fetch_indices", return_value={}), patch.object(module, "fetch_industries", return_value={}):
                    with patch.object(module, failing, side_effect=RuntimeError("fixture failure")):
                        self.assertEqual(module.main(["--output", str(output)]), 1)
                self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
