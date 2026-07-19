#!/usr/bin/env python3
"""Unit tests for the handoff validator's pure helpers, plus the doc-sync assertion
that keeps agents/_policy.md §handoffs matching handoff_check.REQUIRED — the same
pattern as test_agent_workers.py asserting the worker roster's invariants.

Run: PYTHONPATH=scripts python3 scripts/test_handoff_check.py  (CI does this)
"""
import os
import re
import unittest

from handoff_check import REQUIRED, fenced_blocks, markers, validate

AGREEMENT_OK = """[AGREEMENT] converged after 3 replies
```yaml
plan: ship the pricing page
owners: {business: copy, it: deploy}
acceptance: page live and linked
tickets: [org#41, org#42]
```
"""


class TestMarkers(unittest.TestCase):
    def test_line_start_marker_detected(self):
        self.assertEqual(markers(AGREEMENT_OK), ["AGREEMENT"])

    def test_prose_mention_mid_sentence_ignored(self):
        self.assertEqual(markers("the [DEMO] gate needs evidence"), [])

    def test_indented_marker_still_counts(self):
        self.assertEqual(markers("  [CODEREVIEW] verdict below"), ["CODEREVIEW"])

    def test_empty_and_none(self):
        self.assertEqual(markers(""), [])
        self.assertEqual(markers(None), [])

    def test_wrapped_prose_mid_paragraph_ignored(self):
        # The business#129 false positive: a hard line-wrap landed a bare marker at
        # the start of a mid-paragraph line. A marker only leads a HANDOFF when it
        # leads a paragraph — mid-paragraph line starts are prose.
        body = ("**Business —**\n\n"
                "The reviewer gate means every product PR needs\n"
                "[CODEREVIEW] to pass before its demo is relayed.")
        self.assertEqual(markers(body), [])

    def test_banner_led_handoff_detected(self):
        # The compliant form: identity banner, blank line, marker leads its paragraph.
        body = "**IT —**\n\n[DEMO] evidence below\n```yaml\npr: o/r#1\n```"
        self.assertEqual(markers(body), ["DEMO"])

    def test_marker_after_blank_line_counts(self):
        self.assertEqual(markers("preamble prose\n\n[AGREEMENT] converged"),
                         ["AGREEMENT"])

    def test_crlf_bodies_handled(self):
        # GitHub comment bodies arrive with \r\n: a \r-only "blank" line still blanks,
        # and a wrap-landed marker mid-paragraph is still prose.
        body = "**Business —**\r\n\r\n[DEMO] evidence\r\n[CODEREVIEW] wrap-landed"
        self.assertEqual(markers(body), ["DEMO"])


class TestFencedBlocks(unittest.TestCase):
    def test_yaml_and_yml_fences(self):
        body = "```yaml\na: 1\n```\ntext\n```yml\nb: 2\n```"
        self.assertEqual(len(fenced_blocks(body)), 2)

    def test_other_fences_ignored(self):
        self.assertEqual(fenced_blocks("```python\nx = 1\n```"), [])


class TestValidate(unittest.TestCase):
    def test_valid_agreement(self):
        payload = {"plan": "p", "owners": {}, "acceptance": "a", "tickets": []}
        self.assertEqual(validate(["AGREEMENT"], [payload]), [])

    def test_missing_keys_reported(self):
        problems = validate(["DEMO"], [{"pr": "prod#12", "ci": "green"}])
        self.assertEqual(len(problems), 1)
        self.assertIn("evidence_run", problems[0])
        self.assertIn("acceptance", problems[0])

    def test_marker_without_payload(self):
        problems = validate(["CODEREVIEW"], [])
        self.assertEqual(len(problems), 1)
        self.assertIn("no fenced yaml payload", problems[0])

    def test_close_marker_detected_and_validated(self):
        body = "[CLOSE] org#7\n```yaml\nitem: org#7\nkind: story\nevidence:\n  pr: o/r#12\n```"
        self.assertEqual(markers(body), ["CLOSE"])
        self.assertEqual(validate(["CLOSE"], [{"item": "org#7", "kind": "story",
                                               "evidence": {"pr": "o/r#12"}}]), [])

    def test_close_rollup_empty_evidence_is_valid_shape(self):
        # Epics/objectives close by rollup — `evidence:` present but empty passes shape;
        # whether the evidence SUFFICES is workitems.py's call (the facts layer).
        self.assertEqual(validate(["CLOSE"], [{"item": "org#9", "kind": "epic",
                                               "evidence": None}]), [])

    def test_close_missing_keys_reported(self):
        problems = validate(["CLOSE"], [{"item": "org#7"}])
        self.assertIn("kind", problems[0])
        self.assertIn("evidence", problems[0])

    def test_any_satisfying_payload_passes(self):
        good = {k: "x" for k in REQUIRED["CODEREVIEW"]}
        self.assertEqual(validate(["CODEREVIEW"], [{"unrelated": 1}, good]), [])

    def test_closest_payload_reported_not_worst(self):
        nearly = {"pr": "p", "head_sha": "s", "verdict": "PASS", "findings": 0,
                  "review_url": "u"}  # missing only evidence_run
        problems = validate(["CODEREVIEW"], [{"unrelated": 1}, nearly])
        self.assertIn("evidence_run", problems[0])
        self.assertNotIn("head_sha", problems[0])


class TestPolicyDocSync(unittest.TestCase):
    """agents/_policy.md §handoffs documents the schema; REQUIRED is authoritative.
    This test fails CI if either side changes without the other."""

    def test_policy_lists_match_required(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(repo, "agents", "_policy.md"), encoding="utf-8") as fh:
            text = fh.read()
        documented = {}
        for para in re.split(r"\n\s*\n", text):
            m = re.match(r"`\[(\w+)\]` requires:", para.strip())
            if m:
                documented[m.group(1)] = set(re.findall(r"`(\w+):`", para))
        self.assertEqual(set(documented), set(REQUIRED),
                         "handoff types in _policy.md §handoffs ≠ handoff_check.REQUIRED")
        for t, keys in REQUIRED.items():
            self.assertEqual(documented[t], set(keys),
                             f"[{t}] keys drifted between _policy.md and handoff_check.py")


if __name__ == "__main__":
    unittest.main(verbosity=1)
