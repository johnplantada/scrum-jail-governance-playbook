"""Synthetic remote responses and durable local receipts, with no network calls."""
from copy import deepcopy
import hashlib
import tempfile
import unittest
from pathlib import Path

from sjg_control.store import Store
from test_github_backend import ScriptedGitHub


class EvidenceTrustTests(unittest.TestCase):
    key = 'wake-1234567890-demo'
    sha = 'a' * 40
    reference = {'demo': 'https://github.com/alice/acme-org/issues/7#issuecomment-99'}

    def setUp(self):
        self.issue = {'number': 7, 'body': 'Done-when: complete',
                      'labels': [{'name': 'feature'}]}
        self.payload = ('\n[DEMO]\naccepted: true\nitem: alice/acme-org#7\n'
                        'acceptance_digest: ' + hashlib.sha256(b'complete').hexdigest() + '\n'
                        'pr: alice/acme-product#3\nhead_sha: ' + self.sha + '\n'
                        'evidence_run: https://github.com/alice/acme-product/actions/runs/12')
        self.comment = {'id': 99, 'user': {'id': 42},
                        'issue_url': 'https://api.github.com/repos/alice/acme-org/issues/7',
                        'body': f'<!-- sjg-idempotency:{self.key} -->\n**Commercial Planner —** {self.payload}'}
        self.receipt = {'arguments': {'repository': 'org', 'id': 7, 'body': self.payload},
                        'result': {'comment_id': 99, 'comment_author_id': 42}}
        self.pr = {'head': {'sha': self.sha}}
        self.run = {'status': 'completed', 'conclusion': 'success',
                    'event': 'workflow_dispatch', 'path': '.github/workflows/demo-evidence.yml',
                    'head_branch': 'main', 'head_sha': 'b' * 40,
                    'repository': {'full_name': 'alice/acme-product'},
                    'actor': {'login': 'alice'}, 'triggering_actor': {'login': 'alice'}}
        self.artifacts = {'total_count': 1, 'artifacts': [
            {'name': f'demo-evidence-3-{self.sha}', 'expired': False}]}

    def backend(self):
        return ScriptedGitHub([self.comment, self.pr, self.run, self.artifacts],
                              receipt_lookup=lambda key: self.receipt if key == self.key else None)

    def test_receipted_human_dispatched_current_head_demo_is_evidence(self):
        backend = self.backend()
        self.assertTrue(backend._evidence(self.issue, self.reference))
        self.assertEqual(4, len(backend.calls))

    def test_missing_receipt_or_author_identity_fails_closed(self):
        self.assertFalse(ScriptedGitHub([self.comment])._evidence(self.issue, self.reference))
        del self.receipt['result']['comment_author_id']
        self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_comment_identity_and_body_are_bound_to_original_receipt(self):
        mutations = [('id', 100), ('user', {'id': 123}),
                     ('issue_url', 'https://api.github.com/repos/alice/acme-org/issues/8'),
                     ('body', self.comment['body'] + '\nchanged')]
        original = deepcopy(self.comment)
        for field, value in mutations:
            with self.subTest(field=field):
                self.comment = {**original, field: value}
                self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_receipt_cannot_be_replayed_for_another_issue_or_comment(self):
        for target, field, value in [(self.receipt['arguments'], 'id', 8),
                                     (self.receipt['arguments'], 'repository', 'product'),
                                     (self.receipt['result'], 'comment_id', 100)]:
            before = target[field]
            target[field] = value
            self.assertFalse(self.backend()._evidence(self.issue, self.reference))
            target[field] = before

    def test_current_acceptance_and_nonempty_criteria_required(self):
        for body in ('Done-when: changed', '', '## Acceptance\n_No response_'):
            with self.subTest(body=body):
                self.issue['body'] = body
                self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_missing_or_duplicate_product_fields_rejected_even_with_receipt(self):
        original = self.payload
        for text in (original.split('\npr:')[0],
                     original.replace('head_sha: ' + self.sha + '\n', ''),
                     original.replace('evidence_run:', 'not_evidence_run:'),
                     original + '\nhead_sha: ' + self.sha):
            self.payload = text
            self.receipt['arguments']['body'] = text
            self.comment['body'] = f'<!-- sjg-idempotency:{self.key} -->\n**Commercial Planner —** {text}'
            self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_stale_pr_head_rejected(self):
        self.pr['head']['sha'] = 'c' * 40
        self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_wrong_failed_incomplete_or_nonhuman_run_rejected(self):
        changes = {'status': 'in_progress', 'conclusion': 'failure', 'event': 'push',
                   'path': '.github/workflows/other.yml', 'head_branch': 'agent/software-implementer/x',
                   'repository': {'full_name': 'outsider/other'}, 'actor': {'login': 'outsider'},
                   'triggering_actor': {'login': 'outsider'}}
        original = deepcopy(self.run)
        for field, value in changes.items():
            with self.subTest(field=field):
                self.run = {**original, field: value}
                self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_expired_stale_or_incomplete_artifacts_rejected(self):
        for artifacts in ({'total_count': 2, 'artifacts': self.artifacts['artifacts']},
                          {'total_count': 0, 'artifacts': []},
                          {'total_count': 1, 'artifacts': [{'name': 'demo-evidence-3-' + 'b'*40, 'expired': False}]},
                          {'total_count': 1, 'artifacts': [{'name': f'demo-evidence-3-{self.sha}', 'expired': True}]}):
            with self.subTest(artifacts=artifacts):
                self.artifacts = artifacts
                self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_malformed_artifact_enumeration_is_not_complete_evidence(self):
        valid = deepcopy(self.artifacts['artifacts'])
        for artifacts in ({'total_count': 2, 'artifacts': valid + [None]},
                          {'total_count': True, 'artifacts': valid},
                          {'total_count': 1.0, 'artifacts': valid},
                          {'total_count': -1, 'artifacts': valid},
                          {'total_count': '1', 'artifacts': valid}):
            with self.subTest(artifacts=artifacts):
                self.artifacts = artifacts
                self.assertFalse(self.backend()._evidence(self.issue, self.reference))

    def test_merged_pr_cannot_bypass_feature_demo_or_changed_story_acceptance(self):
        body = 'Relates-to alice/acme-org#7\nacceptance_digest: ' + hashlib.sha256(b'complete').hexdigest()
        for kind, acceptance in [('feature', 'complete'), ('story', 'changed')]:
            issue = {**self.issue, 'labels': [{'name': kind}], 'body': 'Done-when: ' + acceptance}
            self.assertFalse(ScriptedGitHub([{'merged': True, 'body': body}])._evidence(
                issue, {'pr': 'alice/acme-product#3'}))

    def test_store_receipt_requires_succeeded_matching_policy_and_commercial_role(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'state.sqlite')
            self.addCleanup(store.close)
            session = store.create_session(org_id='acme-org', role='commercial-planner',
                                           policy_digest='digest', workspace_aliases={'org'}, global_cap=6)
            store.reserve_request(session.session_id, self.key, 'pm.issue.comment', 'hash',
                                  self.receipt['arguments'])
            lookup = lambda: store.commercial_demo_receipt(self.key, org_id='acme-org', policy_digest='digest')
            self.assertIsNone(lookup())
            store.mark_request_applying(self.key)
            store.finish_request(self.key, 'succeeded', {'status': 'ok', 'result': self.receipt['result']})
            self.assertEqual(self.receipt, lookup())
            for field, value in [('role', 'software-implementer'), ('policy_digest', 'other'), ('org_id', 'other')]:
                old = dict(store.connection.execute('SELECT * FROM wake_session').fetchone())[field]
                store.connection.execute(f'UPDATE wake_session SET {field}=?', (value,))
                self.assertIsNone(lookup())
                store.connection.execute(f'UPDATE wake_session SET {field}=?', (old,))


if __name__ == '__main__':
    unittest.main()
