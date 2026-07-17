#!/usr/bin/env python3
"""Unit tests for wake_filter's pure helpers (no gh, no filesystem).

Run: PYTHONPATH=scripts python3 scripts/test_wake_filter.py  (CI does this)
"""
import unittest

from wake_filter import (banner_dept, batch_verdict, cooldown_active, deadman_due,
                         deploy_hold, event_verdict, last_cycle_ts, noop_streak)


def comment(title="**🛠️ IT —** pushed the fix", issue_state="open", **kw):
    base = {"id": "c1", "kind": "comment", "repo": "org", "at": "2026-07-11T10:00:00Z",
            "title": title, "url": "u", "labels": ["dept:business"],
            "issue_state": issue_state}
    base.update(kw)
    return base


def issue(issue_state="open", **kw):
    base = {"id": "org-issue-138-2026-07-12T13:34:19Z", "kind": "issue", "repo": "org",
            "at": "2026-07-12T13:34:19Z", "number": 138,
            "title": "[OBJECTIVE] In progress work summary", "url": "u",
            "labels": ["dept:it", "dept:warden"], "issue_state": issue_state}
    base.update(kw)
    return base


def pr(issue_state="closed", **kw):
    base = {"id": "org-pr-13-2026-07-16T22:26:10Z", "kind": "pr", "repo": "org",
            "at": "2026-07-16T22:26:10Z", "number": 13,
            "title": "[CHARTER] warden", "url": "u", "labels": [],
            "issue_state": issue_state, "pr_state": "merged"}
    base.update(kw)
    return base


def cycle_row(agent="it", outcome="noop", ts="2026-07-11 10:00:00", **kw):
    base = {"source": "cycle", "agent": agent, "status": "ok", "ts": ts}
    if outcome is not None:
        base["outcome"] = outcome
    base.update(kw)
    return base


class TestDeployHold(unittest.TestCase):
    def test_word_match_includes_the_ledgers_real_shapes(self):
        data = {"blockers": [{"state": "open", "blocks": ["ugc-c1-deploy"]}]}
        self.assertTrue(deploy_hold(data))
        data = {"blockers": [{"state": "open", "blocks": ["deploy-authority", "x"]}]}
        self.assertTrue(deploy_hold(data))
        data = {"blockers": [{"blocks": ["revenue"]}]}  # state defaults to open
        self.assertTrue(deploy_hold(data))

    def test_cleared_entries_and_non_hold_blocks_do_not_hold(self):
        self.assertFalse(deploy_hold({"blockers": [
            {"state": "cleared", "blocks": ["deploy"]},
            {"state": "open", "blocks": ["pm#40", "merch"]},
        ]}))
        # substring inside a word is NOT a match ('redeployment' ≠ deploy gate)
        self.assertFalse(deploy_hold({"blockers": [
            {"state": "open", "blocks": ["redeployments"]}]}))
        self.assertFalse(deploy_hold({}))
        self.assertFalse(deploy_hold(None))


class TestVerdicts(unittest.TestCase):
    def test_runs_and_unattributed_issues_always_fire(self):
        run_ev = {"kind": "run", "workflow": "deploy.yml"}
        self.assertEqual(event_verdict(run_ev, cooldown=True, hold=True)[0], "fire")
        # No bump_dept = the bump could be a human's (edit, label, close) — a bannered
        # TITLE is not attribution; unattributed issue events pierce every rule.
        ev = issue(title="**🛠️ IT —** whatever")
        self.assertEqual(event_verdict(ev, cooldown=True, hold=True), ("fire", "issue"))

    def test_human_comments_pierce_everything(self):
        ev = comment(title="please prioritize the store URL")  # unsigned = human
        self.assertEqual(event_verdict(ev, cooldown=True)[0], "fire")
        ev = comment(title="chairman note", issue_state="closed")
        self.assertEqual(event_verdict(ev, cooldown=True)[0], "fire")

    def test_agent_comment_on_closed_issue_defers(self):
        ev = comment(issue_state="closed")
        action, reason = event_verdict(ev, cooldown=False)
        self.assertEqual((action, reason), ("defer", "closed-thread-echo"))

    def test_agent_comment_on_open_issue_fires_unless_cooldown(self):
        ev = comment(issue_state="open")
        self.assertEqual(event_verdict(ev, cooldown=False)[0], "fire")
        self.assertEqual(event_verdict(ev, cooldown=True)[0], "defer")

    def test_unknown_issue_state_fails_safe_to_fire(self):
        ev = comment(issue_state="")
        self.assertEqual(event_verdict(ev, cooldown=False)[0], "fire")

    def test_batch_fires_if_any_event_fires(self):
        evs = [comment(issue_state="closed"), comment(title="human ask")]
        self.assertEqual(batch_verdict(evs, cooldown=False)[0], "fire")

    def test_batch_defers_only_when_all_defer(self):
        evs = [comment(issue_state="closed"), comment(issue_state="closed", id="c2")]
        action, reason = batch_verdict(evs, cooldown=False)
        self.assertEqual(action, "defer")
        self.assertIn("closed-thread-echo", reason)

    def test_empty_batch_defers(self):
        self.assertEqual(batch_verdict([], cooldown=False)[0], "defer")

    def test_banner_parsing(self):
        self.assertEqual(banner_dept("**🛠️ IT —** pushed"), "it")
        self.assertEqual(banner_dept("**📈 Business —** thoughts?"), "business")
        self.assertIsNone(banner_dept("no banner"))

    def test_marker_led_comment_uses_runner_stamped_banner(self):
        # Warden's report opens '<!-- warden-report -->'; the runner stamps `banner`
        # from the full body, so the comment is an agent's despite the unsigned title.
        ev = comment(title="<!-- warden-report -->", banner="warden", issue_state="closed")
        self.assertEqual(event_verdict(ev, cooldown=False),
                         ("defer", "closed-thread-echo"))
        ev = comment(title="<!-- warden-report -->", banner=None, issue_state="closed")
        self.assertEqual(event_verdict(ev, cooldown=False)[0], "fire")


class TestIssueSelfEcho(unittest.TestCase):
    def test_attributed_bump_on_open_issue_defers_unconditionally(self):
        # The org#138 loop: warden edits its report comment, the bump mints a fresh
        # issue event. No cooldown/hold needed — the echo defers on every tick.
        ev = issue(bump_dept="warden")
        self.assertEqual(event_verdict(ev, cooldown=False), ("defer", "issue-self-echo"))

    def test_attributed_bump_on_closed_issue_defers(self):
        # The issue-side of v1's closed-thread echo — without this, the issue event
        # fired anyway and defeated the comment-side deferral.
        ev = issue(bump_dept="it", issue_state="closed")
        self.assertEqual(event_verdict(ev, cooldown=False),
                         ("defer", "issue-closed-thread-echo"))

    def test_attributed_unknown_state_fires_unless_hold_cooldown(self):
        # State only picks the reason label; attribution is the invariant. But an
        # unknown state still fails safe to fire outside the deploy-hold cooldown.
        ev = issue(bump_dept="it", issue_state="")
        self.assertEqual(event_verdict(ev, cooldown=False, hold=True)[0], "fire")
        self.assertEqual(event_verdict(ev, cooldown=True, hold=False)[0], "fire")
        self.assertEqual(event_verdict(ev, cooldown=True, hold=True),
                         ("defer", "noop-streak-cooldown"))

    def test_batch_of_pure_echoes_defers_but_any_human_event_fires_it(self):
        echoes = [issue(bump_dept="warden"), issue(bump_dept="warden", id="i2")]
        action, reason = batch_verdict(echoes, cooldown=False)
        self.assertEqual(action, "defer")
        self.assertIn("issue-self-echo", reason)
        self.assertEqual(batch_verdict(echoes + [comment(title="chairman ask")],
                                       cooldown=False)[0], "fire")
        self.assertEqual(batch_verdict(echoes + [issue(id="i3")],  # unattributed bump
                                       cooldown=False)[0], "fire")


class TestPrEcho(unittest.TestCase):
    def test_chairman_merge_pierces_everything(self):
        # org#13: a merge is never comment-attributed, so it must fire even under
        # cooldown + hold — the whole point of routing PRs is that this wake happens.
        self.assertEqual(event_verdict(pr(), cooldown=True, hold=True), ("fire", "pr"))

    def test_attributed_pr_bump_defers_like_an_issue_bump(self):
        # An agent's bannered PR comment bumps the PR the same way it bumps an issue.
        ev = pr(bump_dept="it", issue_state="open", pr_state="open")
        self.assertEqual(event_verdict(ev, cooldown=False), ("defer", "pr-self-echo"))
        ev = pr(bump_dept="it", issue_state="closed")
        self.assertEqual(event_verdict(ev, cooldown=False),
                         ("defer", "pr-closed-thread-echo"))


class TestStreakAndCooldown(unittest.TestCase):
    def test_streak_counts_consecutive_noops_only(self):
        rows = [cycle_row(outcome="post"), cycle_row(), cycle_row()]
        self.assertEqual(noop_streak(rows, "it"), 2)
        rows = [cycle_row(), cycle_row(outcome="ship"), cycle_row()]
        self.assertEqual(noop_streak(rows, "it"), 1)

    def test_streak_ignores_other_agents_siblings_and_untagged(self):
        rows = [cycle_row(),                       # it noop
                cycle_row(agent="ceo", outcome="ship"),
                {"source": "cycle", "agent": "it", "status": "ok",
                 "ts": "2026-07-11 10:00:01"},     # sibling row, no outcome: invisible
                cycle_row()]                       # it noop
        self.assertEqual(noop_streak(rows, "it"), 2)
        self.assertEqual(noop_streak([], "it"), 0)

    def test_error_rows_do_not_count(self):
        rows = [cycle_row(), cycle_row(status="error")]
        self.assertEqual(noop_streak(rows, "it"), 1)

    def test_cooldown_needs_streak_and_recency(self):
        recent = [cycle_row(ts="2026-07-11 09:50:00") for _ in range(3)]
        self.assertTrue(cooldown_active(recent, "it", "2026-07-11 10:00:00", hold=False))
        self.assertFalse(cooldown_active(recent[:2], "it", "2026-07-11 10:00:00", hold=False))
        # streak of 2 is enough during a deploy-hold
        self.assertTrue(cooldown_active(recent[:2], "it", "2026-07-11 10:00:00", hold=True))
        # stale streak: outside the window → no cooldown
        old = [cycle_row(ts="2026-07-11 06:00:00") for _ in range(3)]
        self.assertFalse(cooldown_active(old, "it", "2026-07-11 10:00:00", hold=False))

    def test_last_cycle_ts_picks_newest_tagged_row(self):
        rows = [cycle_row(ts="2026-07-11 09:00:00"),
                cycle_row(agent="ceo", ts="2026-07-11 09:30:00"),
                cycle_row(ts="2026-07-11 09:10:00")]
        self.assertEqual(last_cycle_ts(rows, "it"), "2026-07-11 09:10:00")
        self.assertEqual(last_cycle_ts([], "it"), "")


class TestDeadman(unittest.TestCase):
    def test_old_spooled_entry_trips_the_switch(self):
        entries = [{"deferred_at": "2026-07-11T02:00:00Z"}]
        self.assertTrue(deadman_due(entries, "2026-07-11T10:00:00Z", deadman_min=360))
        fresh = [{"deferred_at": "2026-07-11T09:30:00Z"}]
        self.assertFalse(deadman_due(fresh, "2026-07-11T10:00:00Z", deadman_min=360))

    def test_unparseable_or_empty_never_trips(self):
        self.assertFalse(deadman_due([], "2026-07-11T10:00:00Z"))
        self.assertFalse(deadman_due([{"deferred_at": "garbage"}], "2026-07-11T10:00:00Z"))

    def test_failed_wake_requeue_comes_due_on_the_shorter_retry_bound(self):
        # An entry carrying `retries` is a failed-wake requeue (runner.requeue_failed):
        # it must retry promptly, not wait out the full dead-man window.
        requeued = [{"deferred_at": "2026-07-11T09:30:00Z", "retries": 1}]
        self.assertTrue(deadman_due(requeued, "2026-07-11T10:00:00Z",
                                    deadman_min=360, retry_min=15))
        self.assertFalse(deadman_due(requeued, "2026-07-11T09:40:00Z",
                                     deadman_min=360, retry_min=15))

    def test_filter_deferral_still_waits_the_deadman_window(self):
        deferred = [{"deferred_at": "2026-07-11T09:30:00Z"}]
        self.assertFalse(deadman_due(deferred, "2026-07-11T10:00:00Z",
                                     deadman_min=360, retry_min=15))


if __name__ == "__main__":
    unittest.main()
