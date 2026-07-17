#!/usr/bin/env python3
"""Unit tests for the decisions-ledger validator (no yaml, no I/O).

Run: PYTHONPATH=scripts python3 scripts/test_decisions.py  (CI does this)
"""
import unittest

from decisions import render, validate


def entry(**kw):
    base = {"id": "spring-store-spend", "type": "spend", "dept": "business",
            "what": "Open the Spring store", "why": "first merch conversion path",
            "cost_usd": 0, "chairman_minutes": 20, "reversibility": "reversible — close the store",
            "unblocks": ["pm#40"], "proposed": "2026-07-05"}
    base.update(kw)
    return base


class TestValidate(unittest.TestCase):
    def test_empty_ledger_is_valid(self):
        self.assertEqual(validate({"decisions": []}), [])

    def test_valid_entry_passes(self):
        self.assertEqual(validate({"decisions": [entry()]}), [])

    def test_missing_key_and_wrong_shape(self):
        self.assertEqual(validate({}), ["missing top-level 'decisions' key"])
        self.assertEqual(validate({"decisions": "nope"}), ["'decisions' must be a list"])

    def test_duplicate_ids_are_forever(self):
        problems = validate({"decisions": [entry(), entry()]})
        self.assertTrue(any("duplicate id" in p for p in problems))

    def test_required_fields_enforced(self):
        problems = validate({"decisions": [entry(why="")]})
        self.assertTrue(any("missing required field 'why'" in p for p in problems))

    def test_type_enum_enforced(self):
        problems = validate({"decisions": [entry(type="vibes")]})
        self.assertTrue(any("type must be one of" in p for p in problems))

    def test_cost_must_be_numeric(self):
        problems = validate({"decisions": [entry(cost_usd="a lot")]})
        self.assertTrue(any("cost_usd must be a number" in p for p in problems))

    def test_org_shape_types_need_the_payload(self):
        problems = validate({"decisions": [entry(type="charter")]})
        self.assertTrue(any("needs the org-shape payload" in p for p in problems))
        ok = entry(type="charter", payload={"name": "product"})
        self.assertEqual(validate({"decisions": [ok]}), [])

    def test_non_mapping_entry(self):
        problems = validate({"decisions": ["just a string"]})
        self.assertTrue(any("not a mapping" in p for p in problems))


class TestRender(unittest.TestCase):
    def test_newest_first_one_line_each(self):
        old = entry(id="a", proposed="2026-07-01")
        new = entry(id="b", proposed="2026-07-04")
        lines = render([old, new]).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("[spend] b", lines[0])
        self.assertIn("[spend] a", lines[1])

    def test_empty_renders_empty(self):
        self.assertEqual(render([]), "")


if __name__ == "__main__":
    unittest.main()
