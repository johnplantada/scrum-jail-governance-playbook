#!/usr/bin/env python3
"""Unit tests for the runner's pure helpers (no gh, no filesystem).

Run: PYTHONPATH=scripts python3 scripts/test_runner.py  (CI does this)
"""
import unittest

from runner import (attribute_issue_bumps, banner_dept, batch_wakes, body_banner, dedup,
                    depts_from_labels, issue_number_from_url, next_cursor,
                    normalize_comment, normalize_issue, normalize_run, parse_gh_response,
                    prune_issue_cache, rate_hold, route, rule_matches, spend_today,
                    split_retryable)

RULES = [
    {"match": {"kind": "issue", "label": "dept:it"}, "wake": "from-label"},
    {"match": {"kind": "comment"}, "wake": "from-label"},
    {"match": {"kind": "pr", "label": "dept:it"}, "wake": "from-label"},
    {"match": {"kind": "pr", "repo": "org"}, "wake": "ceo"},
    {"match": {"kind": "pr", "repo": "product"}, "wake": "it"},
    {"match": {"kind": "run", "repo": "product", "workflow": "deploy.yml"}, "wake": "it"},
    {"match": {"kind": "run", "repo": "product", "workflow": "demo-evidence.yml",
               "conclusion": "failure"}, "wake": "it"},
]


def issue_ev(**kw):
    base = {"id": "e1", "kind": "issue", "repo": "org", "at": "2026-07-05T10:00:00Z",
            "title": "t", "url": "u", "labels": []}
    base.update(kw)
    return base


def pr_ev(**kw):
    base = {"id": "p1", "kind": "pr", "repo": "org", "at": "2026-07-16T22:26:10Z",
            "title": "t", "url": "u", "labels": [], "issue_state": "closed",
            "pr_state": "merged"}
    base.update(kw)
    return base


class TestNormalize(unittest.TestCase):
    def test_issue_carries_labels_and_updated_at(self):
        ev = normalize_issue({"number": 7, "updated_at": "2026-07-05T10:00:00Z",
                              "title": "Wire CTA", "html_url": "u", "state": "open",
                              "labels": [{"name": "dept:it"}, {"name": "bug"}]}, "org")
        self.assertEqual(ev["kind"], "issue")
        self.assertEqual(ev["labels"], ["bug", "dept:it"])
        self.assertIn("issue-7", ev["id"])
        self.assertEqual(ev["number"], 7)
        self.assertEqual(ev["issue_state"], "open")

    def test_comment_takes_first_body_line_and_stamps_body_banner(self):
        ev = normalize_comment({"id": 3, "body": "first line\nsecond", "created_at": "t",
                                "html_url": "u", "issue_url": "iu"}, "org")
        self.assertEqual(ev["title"], "first line")
        self.assertEqual(ev["issue_url"], "iu")
        self.assertIsNone(ev["banner"])
        ev = normalize_comment({"id": 4, "created_at": "t", "html_url": "u",
                                "issue_url": "iu",
                                "body": "<!-- warden-report -->\n**🔒 Warden —** report"},
                               "org")
        self.assertEqual(ev["banner"], "warden")

    def test_body_banner_skips_markers_and_blank_lines_only(self):
        self.assertEqual(body_banner("**🛠️ IT —** pushed"), "it")
        self.assertEqual(body_banner("<!-- warden-report -->\n\n**🔒 Warden —** report"),
                         "warden")
        self.assertIsNone(body_banner("plain human text\n**🛠️ IT —** quoted below"))
        self.assertIsNone(body_banner(""))
        self.assertIsNone(body_banner(None))

    def test_pr_splits_out_with_merge_state(self):
        item = {"number": 13, "updated_at": "2026-07-16T22:26:10Z", "state": "closed",
                "title": "[CHARTER] warden", "html_url": "u", "labels": [],
                "pull_request": {"merged_at": "2026-07-16T22:26:09Z"}}
        ev = normalize_issue(item, "org")
        self.assertEqual(ev["kind"], "pr")
        self.assertEqual(ev["pr_state"], "merged")
        self.assertEqual(ev["issue_state"], "closed")
        self.assertIn("pr-13", ev["id"])
        # open and closed-without-merge keep the plain state
        ev = normalize_issue(dict(item, state="open",
                                  pull_request={"merged_at": None}), "org")
        self.assertEqual(ev["pr_state"], "open")
        ev = normalize_issue(dict(item, pull_request={"merged_at": None}), "org")
        self.assertEqual(ev["pr_state"], "closed")

    def test_plain_issue_carries_no_pr_state(self):
        ev = normalize_issue({"number": 7, "state": "open"}, "org")
        self.assertEqual(ev["kind"], "issue")
        self.assertNotIn("pr_state", ev)

    def test_run_extracts_workflow_basename(self):
        ev = normalize_run({"id": 9, "path": ".github/workflows/deploy.yml",
                            "status": "completed", "conclusion": "success",
                            "created_at": "t", "html_url": "u"}, "product")
        self.assertEqual(ev["workflow"], "deploy.yml")
        self.assertEqual(ev["conclusion"], "success")


class TestRouting(unittest.TestCase):
    def test_label_rule_routes_to_dept(self):
        wakes, unrouted = route([issue_ev(labels=["dept:it"])], RULES)
        self.assertEqual(wakes, [("it", issue_ev(labels=["dept:it"]))])
        self.assertEqual(unrouted, [])

    def test_unmatched_event_is_unrouted(self):
        wakes, unrouted = route([issue_ev(labels=["question"])], RULES)
        self.assertEqual(wakes, [])
        self.assertEqual(len(unrouted), 1)

    def test_first_matching_rule_wins_and_conclusion_filters(self):
        ok = {"id": "r1", "kind": "run", "repo": "product", "workflow": "demo-evidence.yml",
              "conclusion": "success", "at": "t", "labels": []}
        bad = dict(ok, id="r2", conclusion="failure")
        wakes, unrouted = route([ok, bad], RULES)
        self.assertEqual([w[0] for w in wakes], ["it"])   # only the failure wakes
        self.assertEqual(unrouted, [ok])

    def test_catchup_is_oldest_first(self):
        old = issue_ev(id="a", at="2026-07-05T01:00:00Z", labels=["dept:it"])
        new = issue_ev(id="b", at="2026-07-05T02:00:00Z", labels=["dept:it"])
        wakes, _ = route([new, old], RULES)
        self.assertEqual([w[1]["id"] for w in wakes], ["a", "b"])

    def test_comment_routes_via_resolved_issue_labels(self):
        ev = issue_ev(kind="comment", labels=["dept:business"])
        wakes, _ = route([ev], RULES)
        self.assertEqual(wakes[0][0], "business")

    def test_rule_matches_requires_every_field(self):
        self.assertTrue(rule_matches({"kind": "issue"}, issue_ev()))
        self.assertFalse(rule_matches({"kind": "issue", "repo": "product"}, issue_ev()))
        self.assertEqual(depts_from_labels(["bug"]), [])

    def test_two_party_thread_wakes_both_sides(self):
        ev = issue_ev(kind="comment", labels=["dept:business", "dept:it"], title="plain ask")
        wakes, _ = route([ev], RULES)
        self.assertEqual(sorted(w[0] for w in wakes), ["business", "it"])

    def test_banner_suppresses_self_echo(self):
        ev = issue_ev(kind="comment", labels=["dept:business", "dept:it"],
                      title="**🛠️ IT —** pushed the fix")
        wakes, _ = route([ev], RULES)
        self.assertEqual([w[0] for w in wakes], ["business"])

    def test_self_comment_on_own_ticket_is_handled_not_unrouted(self):
        ev = issue_ev(kind="comment", labels=["dept:it"], title="**🛠️ IT —** noting progress")
        wakes, unrouted = route([ev], RULES)
        self.assertEqual(wakes, [])
        self.assertEqual(unrouted, [])

    def test_chairman_merge_on_org_repo_wakes_ceo(self):
        # org#13: the warden-charter merge that woke nobody — the motivating case.
        wakes, unrouted = route([pr_ev()], RULES)
        self.assertEqual([w[0] for w in wakes], ["ceo"])
        self.assertEqual(unrouted, [])

    def test_product_pr_defaults_to_it(self):
        ev = pr_ev(repo="product", issue_state="open", pr_state="open")
        wakes, _ = route([ev], RULES)
        self.assertEqual([w[0] for w in wakes], ["it"])

    def test_pr_dept_label_beats_repo_default(self):
        wakes, _ = route([pr_ev(labels=["dept:it"])], RULES)
        self.assertEqual([w[0] for w in wakes], ["it"])

    def test_unsigned_comment_fails_safe_and_wakes_all(self):
        self.assertIsNone(banner_dept("no banner here"))
        self.assertEqual(banner_dept("**📈 Business —** thoughts?"), "business")
        ev = issue_ev(kind="comment", labels=["dept:it"], title="human chairman comment")
        wakes, _ = route([ev], RULES)
        self.assertEqual([w[0] for w in wakes], ["it"])


class TestAttributeIssueBumps(unittest.TestCase):
    """The wake_filter-v2 self-echo signal: an issue event's updatedAt EXACTLY matching
    a same-poll agent-bannered comment on the same issue proves the comment caused the
    bump (GitHub copies the causing comment's timestamp onto the parent issue)."""
    TS = "2026-07-12T13:34:19Z"

    def bump(self, **kw):
        base = {"id": f"org-issue-138-{self.TS}", "kind": "issue", "repo": "org",
                "at": self.TS, "number": 138, "issue_state": "open", "title": "t",
                "url": "u", "labels": ["dept:it", "dept:warden"]}
        base.update(kw)
        return base

    def cause(self, **kw):
        base = {"id": "org-comment-1", "kind": "comment", "repo": "org", "at": self.TS,
                "title": "<!-- warden-report -->", "banner": "warden", "url": "u",
                "issue_url": "https://api.github.com/repos/o/r/issues/138", "labels": []}
        base.update(kw)
        return base

    def test_exact_ts_match_on_same_issue_stamps_bump_dept(self):
        evs = attribute_issue_bumps([self.bump(), self.cause()])
        self.assertEqual(evs[0].get("bump_dept"), "warden")

    def test_no_stamp_without_exact_match(self):
        # A later human action (label, close, edit) moves updatedAt off the comment's
        # timestamp — the bump is no longer the agent's and must stay unattributed.
        for miss in (self.cause(at="2026-07-12T13:34:20Z"),          # ts off by 1s
                     self.cause(banner=None),                        # unsigned = human
                     self.cause(issue_url="https://api.github.com/repos/o/r/issues/9"),
                     self.cause(repo="product")):                    # other repo
            evs = attribute_issue_bumps([self.bump(), miss])
            self.assertNotIn("bump_dept", evs[0], miss)

    def test_pr_bump_attributes_like_an_issue_bump(self):
        # PR conversation comments ARE issue comments (issue_url carries the PR number),
        # so an agent's bannered PR comment must stamp the PR bump it caused.
        bump = self.bump(kind="pr", id=f"org-pr-138-{self.TS}", pr_state="open")
        evs = attribute_issue_bumps([bump, self.cause()])
        self.assertEqual(evs[0].get("bump_dept"), "warden")

    def test_attribution_is_pre_dedup_only_annotation(self):
        # The causing comment may itself be deduped away afterwards (an edit keeps its
        # id); the stamp must live on the issue event, not depend on the comment.
        evs = attribute_issue_bumps([self.bump(), self.cause()])
        survivors = dedup(evs, ["org-comment-1"])
        self.assertEqual([e["kind"] for e in survivors], ["issue"])
        self.assertEqual(survivors[0]["bump_dept"], "warden")

    def test_issue_number_from_url(self):
        self.assertEqual(issue_number_from_url(
            "https://api.github.com/repos/o/r/issues/138"), 138)
        self.assertIsNone(issue_number_from_url("https://github.com/o/r/pull/101"))
        self.assertIsNone(issue_number_from_url(""))


class TestCursorAndDedup(unittest.TestCase):
    def test_dedup_drops_seen_ids(self):
        evs = [issue_ev(id="a"), issue_ev(id="b")]
        self.assertEqual([e["id"] for e in dedup(evs, ["a"])], ["b"])

    def test_cursor_advances_to_newest_never_past_now(self):
        evs = [issue_ev(id="a", at="2026-07-05T10:00:00Z")]
        cur = next_cursor({}, evs, "2026-07-05T11:00:00Z")
        self.assertEqual(cur["since"], "2026-07-05T10:00:00Z")
        cur = next_cursor({}, [issue_ev(at="2026-07-05T12:00:00Z")], "2026-07-05T11:00:00Z")
        self.assertEqual(cur["since"], "2026-07-05T11:00:00Z")

    def test_seen_ring_is_bounded_and_rolls(self):
        cur = next_cursor({"seen": ["x"]}, [issue_ev(id="a")], "t")
        self.assertEqual(cur["seen"], ["x", "a"])

    def test_no_events_keeps_since(self):
        cur = next_cursor({"since": "s0", "seen": []}, [], "now")
        self.assertEqual(cur["since"], "s0")


class TestBatchingAndSpend(unittest.TestCase):
    def test_one_wake_per_dept_carries_all_events(self):
        wakes = [("it", issue_ev(id="a")), ("it", issue_ev(id="b")),
                 ("business", issue_ev(id="c"))]
        batched = batch_wakes(wakes)
        self.assertEqual(len(batched["it"]), 2)
        self.assertEqual(len(batched["business"]), 1)

    def test_spend_today_counts_only_today(self):
        rows = [{"ts": "2026-07-05 09:00:00", "cost_usd": 1.5},
                {"ts": "2026-07-04 09:00:00", "cost_usd": 9.0},
                {"ts": "2026-07-05 10:00:00", "cost_usd": "bad"}]
        self.assertEqual(spend_today(rows, "2026-07-05"), 1.5)


class TestSplitRetryable(unittest.TestCase):
    """Failed-wake requeue: a nonzero dispatch re-queues its events with bounded
    retries — never a silent drop, never an infinite crash loop."""

    def test_first_failure_marks_one_retry_and_respools(self):
        retry, dead = split_retryable([issue_ev()], max_retries=4)
        self.assertEqual(dead, [])
        self.assertEqual(retry[0]["retries"], 1)

    def test_exhausted_event_dead_letters(self):
        retry, dead = split_retryable([issue_ev(retries=4)], max_retries=4)
        self.assertEqual(retry, [])
        self.assertEqual(dead[0]["retries"], 5)

    def test_mixed_batch_splits_by_count(self):
        evs = [issue_ev(id="fresh"), issue_ev(id="spent", retries=4)]
        retry, dead = split_retryable(evs, max_retries=4)
        self.assertEqual([e["id"] for e in retry], ["fresh"])
        self.assertEqual([e["id"] for e in dead], ["spent"])

    def test_input_events_are_not_mutated(self):
        ev = issue_ev()
        split_retryable([ev], max_retries=4)
        self.assertNotIn("retries", ev)

    def test_garbage_retries_reads_as_zero(self):
        # A hand-edited spool must not crash the tick or dead-letter prematurely.
        retry, dead = split_retryable([issue_ev(retries="n/a")], max_retries=4)
        self.assertEqual(dead, [])
        self.assertEqual(retry[0]["retries"], 1)


class TestRateHold(unittest.TestCase):
    NOW = 1_000_000

    def res(self, **kw):
        return {name: {"remaining": rem, "reset": self.NOW + 600}
                for name, rem in kw.items()}

    def test_holds_when_core_below_floor(self):
        hold, why = rate_hold(self.res(core=120, graphql=4000), 500, self.NOW)
        self.assertTrue(hold)
        self.assertIn("core 120 remaining < floor 500", why)

    def test_no_hold_when_both_above_floor(self):
        self.assertEqual(rate_hold(self.res(core=800, graphql=800), 500, self.NOW),
                         (False, ""))

    def test_graphql_counts_and_worst_bucket_wins(self):
        hold, why = rate_hold(self.res(core=300, graphql=40), 500, self.NOW)
        self.assertTrue(hold)
        self.assertIn("graphql 40 remaining", why)

    def test_stale_window_does_not_hold(self):
        stale = {"core": {"remaining": 0, "reset": self.NOW - 5}}
        self.assertEqual(rate_hold(stale, 500, self.NOW), (False, ""))

    def test_fails_open_on_missing_or_malformed(self):
        self.assertEqual(rate_hold({}, 500, self.NOW), (False, ""))
        self.assertEqual(rate_hold(None, 500, self.NOW), (False, ""))
        self.assertEqual(rate_hold({"core": {"remaining": "n/a"}}, 500, self.NOW),
                         (False, ""))

    def test_floor_zero_disables(self):
        self.assertEqual(rate_hold(self.res(core=0, graphql=0), 0, self.NOW),
                         (False, ""))

    def test_reason_carries_minutes_to_reset(self):
        _, why = rate_hold(self.res(core=1), 500, self.NOW)
        self.assertIn("resets in ~11m", why)  # 600s ahead → ceil to 11


class TestParseGhResponse(unittest.TestCase):
    def test_200_with_etag_and_json_body(self):
        raw = (b"HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n"
               b'Etag: W/"abc123"\r\n\r\n[{"number": 7}]')
        status, etag, body = parse_gh_response(raw)
        self.assertEqual((status, etag), (200, 'W/"abc123"'))
        self.assertEqual(body, b'[{"number": 7}]')

    def test_304_not_modified(self):
        raw = b'HTTP/2.0 304 Not Modified\r\nEtag: "abc123"\r\n\r\n'
        status, etag, body = parse_gh_response(raw)
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_etag_header_is_case_insensitive(self):
        raw = b'HTTP/1.1 200 OK\nETAG: W/"x"\n\n[]'
        self.assertEqual(parse_gh_response(raw)[1], 'W/"x"')

    def test_lf_only_separator(self):
        status, _, body = parse_gh_response(b"HTTP/1.1 200 OK\nX: y\n\n{}")
        self.assertEqual((status, body), (200, b"{}"))

    def test_garbage_is_unparseable_not_a_crash(self):
        self.assertEqual(parse_gh_response(b"gh: connection refused"), (0, None, b""))
        self.assertEqual(parse_gh_response(b""), (0, None, b""))


class TestPruneIssueCache(unittest.TestCase):
    def test_keeps_newest_by_ts_and_caps(self):
        cache = {f"repos/o/r/issues/{i}": {"ts": f"2026-07-{i:02d}T00:00:00Z"}
                 for i in range(1, 6)}
        kept = prune_issue_cache(cache, keep=2)
        self.assertEqual(sorted(kept), ["repos/o/r/issues/4", "repos/o/r/issues/5"])

    def test_empty_and_missing_ts_are_safe(self):
        self.assertEqual(prune_issue_cache({}), {})
        self.assertEqual(prune_issue_cache(None), {})
        kept = prune_issue_cache({"a": {}, "b": {"ts": "2026-07-01"}}, keep=1)
        self.assertEqual(list(kept), ["b"])


if __name__ == "__main__":
    unittest.main()
