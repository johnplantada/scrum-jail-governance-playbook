from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sjg_control.delivery import SOURCE_IDS, Page, event_identity, poll_source
from sjg_control.reconcile import ManagedState, Observation, Truth, plan, report_hash
from sjg_control.store import StateError, Store


class Pages:
    def __init__(self, pages, fail_at=None):
        self.pages = pages
        self.fail_at = fail_at
        self.calls = 0
        self.watermarks = []

    def fetch_page(self, *, watermark, page_cursor, etag):
        self.calls += 1
        self.watermarks.append(watermark)
        if self.fail_at == self.calls:
            raise TimeoutError("page failed")
        items = self.pages[self.calls - 1]
        return Page(items, str(self.calls) if self.calls < len(self.pages) else None)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_page_two_failure_does_not_advance_checkpoint(self):
        source = Pages([[{"number": 1, "updated_at": "2026-01-01T01:00:00Z"}],
                        [{"number": 2, "updated_at": "2026-01-01T02:00:00Z"}]], fail_at=2)
        result = poll_source(self.store, "org-issues", source,
                             initial_watermark="2026-01-01T00:00:00Z")
        self.assertFalse(result.complete)
        checkpoint = self.store.checkpoint("org-issues", initial_watermark="never")
        self.assertEqual("2026-01-01T00:00:00Z", checkpoint["watermark"])
        self.assertEqual(0, result.inserted)
        self.assertEqual(0, self.store.connection.execute(
            "SELECT count(*) FROM observed_event").fetchone()[0])
        self.assertEqual(1, self.store.connection.execute(
            "SELECT count(*) FROM snapshot_event").fetchone()[0])
        staged_id = self.store.connection.execute(
            "SELECT event_id FROM snapshot_event").fetchone()[0]
        with self.assertRaises(StateError):
            self.store.add_route(staged_id, "software-implementer")

    def test_every_source_retains_checkpoint_on_late_page_failure(self):
        for index, source_id in enumerate(SOURCE_IDS, 1):
            identity = {"id": index} if source_id.endswith(("comments", "workflow-runs")) else {
                "number": index}
            first = {**identity, "updated_at": "2026-01-01T01:00:00Z"}
            second = {**identity, "updated_at": "2026-01-01T02:00:00Z"}
            source = Pages([[first], [second]], fail_at=2)
            with self.subTest(source=source_id):
                result = poll_source(self.store, source_id, source,
                                     initial_watermark="2026-01-01T00:00:00Z")
                self.assertFalse(result.complete)
                self.assertEqual("2026-01-01T00:00:00Z", self.store.checkpoint(
                    source_id, initial_watermark="never")["watermark"])

    def test_independent_source_advances_without_moving_failed_source(self):
        failed = Pages([[{"number": 1, "updated_at": "2026-01-01T01:00:00Z"}]], fail_at=1)
        good = Pages([[{"id": 9, "updated_at": "2026-01-01T03:00:00Z"}]])
        poll_source(self.store, "org-issues", failed, initial_watermark="2026-01-01T00:00:00Z")
        poll_source(self.store, "product-comments", good, initial_watermark="2026-01-01T00:00:00Z")
        self.assertEqual("2026-01-01T00:00:00Z", self.store.checkpoint(
            "org-issues", initial_watermark="never")["watermark"])
        self.assertEqual("2026-01-01T03:00:00Z", self.store.checkpoint(
            "product-comments", initial_watermark="never")["watermark"])

    def test_overlap_recovers_late_event_without_duplicate_delivery_input(self):
        first = Pages([[{"number": 1, "updated_at": "2026-01-01T01:00:00Z"}]])
        poll_source(self.store, "org-issues", first,
                    initial_watermark="2026-01-01T00:00:00Z")
        late = Pages([[{"number": 2, "updated_at": "2026-01-01T00:58:00Z"},
                       {"number": 1, "updated_at": "2026-01-01T01:00:00Z"}]])
        result = poll_source(self.store, "org-issues", late,
                             initial_watermark="2026-01-01T00:00:00Z")
        self.assertEqual("2026-01-01T00:55:00Z", late.watermarks[0])
        self.assertEqual(1, result.inserted)
        self.assertEqual("2026-01-01T01:00:00Z", result.watermark)

    def _event(self, number=1):
        self.store.checkpoint("org-issues", initial_watermark="2026-01-01T00:00:00Z")
        snapshot = self.store.begin_snapshot("org-issues")
        self.store.stage_observation(
            snapshot, "org-issues", str(number), f"{number}:v1",
            "2026-01-01T00:00:00Z", {"number": number})
        self.store.finish_snapshot(
            snapshot, complete=True, page_count=1,
            maximum_event_time="2026-01-01T00:00:00Z",
            next_watermark="2026-01-01T00:00:00Z")
        event_id = self.store.connection.execute(
            "SELECT event_id FROM observed_event WHERE external_id=?", (str(number),)
        ).fetchone()[0]
        return event_id

    def test_delivery_routes_are_atomic_idempotent_and_not_double_assigned(self):
        event_id = self._event()
        self.store.add_route(event_id, "software-implementer")
        delivery = self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-one")
        self.assertEqual(delivery, self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-one"))
        with self.assertRaises(StateError):
            self.store.enqueue_delivery(
                "software-implementer", [event_id], idempotency_key="route-two")

    def test_running_lease_expiry_is_uncertain_not_retried(self):
        event_id = self._event()
        self.store.add_route(event_id, "software-implementer")
        delivery = self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-one",
            eligible_at="2026-01-01T00:00:00Z")
        leased = self.store.lease_delivery(
            "host-a", now="2026-01-01T00:00:00Z", lease_seconds=10)
        self.assertEqual(delivery, leased["delivery_id"])
        self.store.start_delivery(delivery, "host-a", now="2026-01-01T00:00:01Z")
        self.assertIsNone(self.store.lease_delivery(
            "host-b", now="2026-01-01T00:00:11Z", lease_seconds=10))
        self.assertEqual("uncertain", self.store.connection.execute(
            "SELECT state FROM delivery WHERE delivery_id=?", (delivery,)).fetchone()[0])
        self.assertEqual("manual_review", self.store.connection.execute(
            "SELECT state FROM route_target WHERE delivery_id=?", (delivery,)).fetchone()[0])

    def test_prestart_lease_expiry_retries_and_dead_letter_is_terminal(self):
        event_id = self._event()
        self.store.add_route(event_id, "software-implementer")
        delivery = self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-one",
            eligible_at="2026-01-01T00:00:00Z")
        self.store.lease_delivery("host-a", now="2026-01-01T00:00:00Z", lease_seconds=10)
        reclaimed = self.store.lease_delivery(
            "host-b", now="2026-01-01T00:00:11Z", lease_seconds=10)
        self.assertEqual(delivery, reclaimed["delivery_id"])
        self.store.finish_delivery(delivery, "host-b", outcome="dead_letter", error="permanent")
        self.assertEqual("dead_letter", self.store.connection.execute(
            "SELECT state FROM route_target WHERE delivery_id=?", (delivery,)).fetchone()[0])
        self.assertIsNone(self.store.lease_delivery(
            "host-c", now="2026-01-01T00:01:00Z", lease_seconds=10))

    def test_retry_ceiling_dead_letters_without_a_fourth_execution(self):
        event_id = self._event()
        self.store.add_route(event_id, "software-implementer")
        delivery = self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-retry",
            eligible_at="2026-01-01T00:00:00Z")
        for attempt in range(3):
            owner = f"host-{attempt}"
            leased = self.store.lease_delivery(
                owner, now="2026-01-01T00:00:00Z", lease_seconds=10, max_attempts=3)
            self.assertEqual(delivery, leased["delivery_id"])
            self.store.finish_delivery(
                delivery, owner, outcome="retry", error="temporary",
                eligible_at="2026-01-01T00:00:00Z")
        self.assertIsNone(self.store.lease_delivery(
            "host-four", now="2026-01-01T00:00:00Z", max_attempts=3))
        self.assertEqual("dead_letter", self.store.connection.execute(
            "SELECT state FROM delivery WHERE delivery_id=?", (delivery,)).fetchone()[0])

    def test_budget_holds_do_not_consume_execution_attempts(self):
        event_id = self._event()
        self.store.add_route(event_id, "software-implementer")
        delivery = self.store.enqueue_delivery(
            "software-implementer", [event_id], idempotency_key="route-budget",
            eligible_at="2026-01-01T00:00:00Z")
        for index in range(5):
            owner = f"host-{index}"
            self.assertIsNotNone(self.store.lease_delivery(
                owner, now="2026-01-01T00:00:00Z", max_attempts=3))
            self.store.finish_delivery(delivery, owner, outcome="budget_hold")
            self.store.release_budget_hold(delivery, eligible_at="2026-01-01T00:00:00Z")
        leased = self.store.lease_delivery(
            "host-ready", now="2026-01-01T00:00:00Z", max_attempts=3)
        self.assertIsNotNone(leased)
        self.assertEqual(1, leased["attempt"])

    def test_running_delivery_cannot_retry_or_enter_budget_hold(self):
        for index, outcome in enumerate(("retry", "budget_hold"), 1):
            event_id = self._event(index + 20)
            self.store.add_route(event_id, "software-implementer")
            delivery = self.store.enqueue_delivery(
                "software-implementer", [event_id], idempotency_key=f"running-{outcome}",
                eligible_at="2026-01-01T00:00:00Z")
            self.store.lease_delivery(outcome, now="2026-01-01T00:00:00Z")
            self.store.start_delivery(delivery, outcome, now="2026-01-01T00:00:01Z")
            with self.assertRaises(StateError):
                self.store.finish_delivery(delivery, outcome, outcome=outcome)

    def test_edits_and_reruns_have_distinct_versions(self):
        self.assertNotEqual(
            event_identity("org-comments", {"id": 1, "updated_at": "a"})[1],
            event_identity("org-comments", {"id": 1, "updated_at": "b"})[1])
        self.assertNotEqual(
            event_identity("product-workflow-runs", {"id": 1, "run_attempt": 1,
                           "status": "completed", "conclusion": "failure", "updated_at": "a"})[1],
            event_identity("product-workflow-runs", {"id": 1, "run_attempt": 2,
                           "status": "completed", "conclusion": "success", "updated_at": "b"})[1])

    def test_budget_reservation_is_atomic(self):
        session = self.store.create_session(org_id="o", role="r", policy_digest="p",
                                            workspace_aliases={"org"}, global_cap=10)
        self.store.reserve_budget(session.session_id, epoch="2026-01-01", requested_tokens=80,
                                  buckets=[("org", "o", 100), ("role", "r", 100)])
        other = self.store.create_session(org_id="o", role="r2", policy_digest="p",
                                          workspace_aliases={"org"}, global_cap=10)
        with self.assertRaises(StateError):
            self.store.reserve_budget(other.session_id, epoch="2026-01-01", requested_tokens=30,
                                      buckets=[("org", "o", 100), ("role", "r2", 100)])
        third = self.store.create_session(org_id="o", role="r3", policy_digest="p",
                                          workspace_aliases={"org"}, global_cap=10)
        with self.assertRaises(StateError):
            self.store.reserve_budget(third.session_id, epoch="2026-01-01", requested_tokens=1,
                                      buckets=[("org", "o", 100), ("org", "o", 100)])

    def test_budget_settlement_updates_both_buckets_and_known_overage_is_charged(self):
        session = self.store.create_session(org_id="o", role="r", policy_digest="p",
                                            workspace_aliases={"org"}, global_cap=10)
        reservation = self.store.reserve_budget(
            session.session_id, epoch="2026-01-01", requested_tokens=80,
            buckets=[("org", "o", 100), ("role", "r", 100)])
        self.store.settle_budget(reservation, consumed_tokens=70)
        self.assertEqual([(70, 0), (70, 0)], [tuple(row) for row in self.store.connection.execute(
            "SELECT consumed_tokens,reserved_tokens FROM budget_bucket ORDER BY scope")])

        self.store.revoke_session(session.session_id)
        second = self.store.create_session(org_id="o", role="r", policy_digest="p",
                                           workspace_aliases={"org"}, global_cap=10)
        overage = self.store.reserve_budget(
            second.session_id, epoch="2026-01-01", requested_tokens=20,
            buckets=[("org", "o", 100), ("role", "r", 100)])
        with self.assertRaises(StateError):
            self.store.settle_budget(overage, consumed_tokens=40)
        self.assertEqual("uncertain", self.store.connection.execute(
            "SELECT state FROM budget_reservation WHERE reservation_id=?", (overage,)).fetchone()[0])
        self.assertEqual([(110, 0), (110, 0)], [tuple(row) for row in self.store.connection.execute(
            "SELECT consumed_tokens,reserved_tokens FROM budget_bucket ORDER BY scope")])
        self.store.revoke_session(second.session_id)
        third = self.store.create_session(org_id="o", role="r2", policy_digest="p",
                                          workspace_aliases={"org"}, global_cap=10)
        with self.assertRaisesRegex(StateError, "budget hold"):
            self.store.reserve_budget(
                third.session_id, epoch="2026-01-01", requested_tokens=1,
                buckets=[("org", "o", 100), ("role", "r2", 100)])

    def test_expired_wake_cannot_reserve_budget(self):
        session = self.store.create_session(
            org_id="expired", role="r", policy_digest="p", workspace_aliases={"org"},
            ttl_seconds=1, global_cap=10)
        self.store.connection.execute(
            "UPDATE wake_session SET expires_at='2000-01-01T00:00:00Z' WHERE session_id=?",
            (session.session_id,))
        with self.assertRaisesRegex(StateError, "active session"):
            self.store.reserve_budget(
                session.session_id, epoch="2026-01-01", requested_tokens=1,
                buckets=[("org", "expired", 10), ("role", "r", 10)])

    def test_host_can_reap_a_terminated_expired_wake_without_freeing_unknown_budget(self):
        session = self.store.create_session(
            org_id="recover", role="one", policy_digest="p", workspace_aliases={"org"},
            ttl_seconds=1, global_cap=2)
        reservation = self.store.reserve_budget(
            session.session_id, epoch="2026-01-01", requested_tokens=5,
            buckets=[("org", "recover", 10), ("role", "one", 10)])
        attempt = self.store.reserve_worker(
            session.session_id, "researcher", cumulative_cap=1, concurrent_cap=1,
            global_cap=2)
        self.store.connection.execute(
            "UPDATE worker_attempt SET state='running' WHERE attempt_id=?", (attempt,))
        self.store.connection.execute(
            "UPDATE wake_session SET expires_at='2000-01-01T00:00:00Z' WHERE session_id=?",
            (session.session_id,))
        self.store.reap_expired_session(session.session_id)
        self.assertEqual("expired", self.store.connection.execute(
            "SELECT state FROM wake_session WHERE session_id=?", (session.session_id,)).fetchone()[0])
        self.assertEqual("failed", self.store.connection.execute(
            "SELECT state FROM worker_attempt WHERE attempt_id=?", (attempt,)).fetchone()[0])
        self.assertEqual("uncertain", self.store.connection.execute(
            "SELECT state FROM budget_reservation WHERE reservation_id=?",
            (reservation,)).fetchone()[0])
        replacement = self.store.create_session(
            org_id="recover", role="one", policy_digest="p", workspace_aliases={"org"},
            global_cap=2)
        self.assertTrue(replacement.session_id)

    def test_parent_cannot_be_replaced_or_revoked_while_worker_is_running(self):
        session = self.store.create_session(
            org_id="bounded", role="one", policy_digest="p", workspace_aliases={"org"},
            global_cap=2)
        attempt = self.store.reserve_worker(
            session.session_id, "researcher", cumulative_cap=1, concurrent_cap=1,
            global_cap=2)
        self.store.connection.execute(
            "UPDATE worker_attempt SET state='running' WHERE attempt_id=?", (attempt,))
        with self.assertRaisesRegex(StateError, "workers are active"):
            self.store.revoke_session(session.session_id)
        with self.assertRaisesRegex(StateError, "role already"):
            self.store.create_session(
                org_id="bounded", role="one", policy_digest="p", workspace_aliases={"org"},
                global_cap=3)
        with self.assertRaisesRegex(StateError, "global active-agent cap"):
            self.store.create_session(
                org_id="bounded", role="two", policy_digest="p", workspace_aliases={"org"},
                global_cap=2)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def observation(self, truth, value=None, complete=True):
        return Observation(truth, complete, value)

    def state(self, **changes):
        values = {
            "source_key": "pr:product#7",
            "exists": self.observation(Truth.TRUE),
            "qualifies": self.observation(Truth.TRUE),
            "current_stage": self.observation(Truth.TRUE, "Todo"),
            "desired_stage": self.observation(Truth.TRUE, "In Review"),
        }
        values.update(changes)
        return ManagedState(**values)

    def test_unknown_or_incomplete_state_never_mutates(self):
        for field in ("exists", "qualifies", "current_stage", "desired_stage"):
            with self.subTest(field=field):
                self.assertEqual([], plan(self.state(**{field: self.observation(Truth.UNKNOWN)})))
                self.assertEqual([], plan(self.state(**{field: self.observation(Truth.FALSE, complete=False)})))

    def test_complete_absence_can_create_and_complete_nonqualification_can_close(self):
        create = plan(self.state(exists=self.observation(Truth.FALSE)))
        self.assertEqual("create", create[0].operation)
        close = plan(self.state(qualifies=self.observation(Truth.FALSE)))
        self.assertEqual("close", close[0].operation)

    def test_report_hash_ignores_clock_outside_model(self):
        model = {"findings": ["a"], "status": "held"}
        self.assertEqual(report_hash(model), report_hash(dict(model)))

    def test_reconciliation_journal_is_idempotent_and_records_ambiguity(self):
        first = self.store.reserve_reconcile(
            idempotency_key="sync-item-7", source_key="pr:product#7", operation="move",
            precondition={"from": "Awaiting Merge", "to": "Demo"})
        again = self.store.reserve_reconcile(
            idempotency_key="sync-item-7", source_key="pr:product#7", operation="move",
            precondition={"to": "Demo", "from": "Awaiting Merge"})
        self.assertEqual(first["operation_id"], again["operation_id"])
        self.store.mark_reconcile_applying(first["operation_id"])
        self.store.finish_reconcile(first["operation_id"], state="verify_pending",
                                    result={"error": "response lost"})
        row = self.store.connection.execute(
            "SELECT state FROM reconcile_operation WHERE operation_id=?",
            (first["operation_id"],),
        ).fetchone()
        self.assertEqual("verify_pending", row["state"])
        with self.assertRaises(StateError):
            self.store.finish_reconcile(first["operation_id"], state="retry_wait")
        with self.assertRaises(StateError):
            self.store.mark_reconcile_applying(first["operation_id"])
        with self.assertRaises(StateError):
            self.store.reserve_reconcile(
                idempotency_key="sync-item-7", source_key="issue:org#8", operation="close",
                precondition={"present": True})


if __name__ == "__main__":
    unittest.main()
