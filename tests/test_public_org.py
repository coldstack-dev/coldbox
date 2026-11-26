import argparse
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_coldbox import cb, quiet


class PublicOrg(unittest.TestCase):
    target = 'acme/widget'

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.multiple(cb, APP_ENV=self.root / 'github-app.env',
                                             APP_KEY=self.root / 'github-app.pem', APP_TOKENS={}))
        self.repo = dict(visibility='public', full_name=self.target, owner=dict(type='Organization'))
        self.members = [dict(login='Alice')]
        self.count = 1
        self.more = False
        self.outside = []
        self.policy = cb.APPROVAL
        self.token = self.enterContext(mock.patch.object(cb, 'app_token', return_value='fixture'))
        self.real_request = cb.app_request
        self.enterContext(mock.patch.object(cb, 'app_request', side_effect=self.request))
        self.enterContext(mock.patch.object(cb, 'gh', side_effect=self.get))
        (self.root / 'github-app-acme.env').write_text('COLDBOX_GITHUB_APP_ID=2\nCOLDBOX_SOLE_MEMBER=alice\n')
        (self.root / 'github-app-acme.pem').touch()

    def request(self, path, token, *args, **kwargs):
        self.assertEqual(token, 'fixture')
        if path == 'graphql':
            return dict(data=dict(organization=dict(membersWithRole=dict(
                totalCount=self.count, pageInfo=dict(hasNextPage=self.more), nodes=self.members))))
        if path == 'repos/acme/widget/collaborators?affiliation=outside&per_page=1':
            return self.outside
        raise AssertionError(path)

    def get(self, path):
        if path == 'repos/acme/widget':
            return self.repo
        if path.endswith('/permissions/fork-pr-contributor-approval'):
            return dict(approval_policy=self.policy)
        if path.endswith('/collaborators?permission=push&per_page=100&page=1'):
            return [dict(login='Alice')]
        raise AssertionError(path)

    def test_registration_accepts_only_with_opt_in_and_scoped_member_access(self):
        with self.assertRaises(SystemExit):
            cb.guard(self.target, False)
        self.assertEqual(cb.guard(self.target, True), ['Alice'])
        self.token.assert_called_with('repos/acme/widget', dict(repositories=['widget'], permissions=dict(members='read', metadata='read')))

    def test_second_member_wrong_member_and_incomplete_membership_refused(self):
        for self.count, self.members, self.more in (
                (2, [dict(login='Alice'), dict(login='Bob')], False),
                (1, [dict(login='Bob')], False), (2, [dict(login='Alice')], False),
                (1, [dict(login='Alice')], True), (1, [], False), (1, [{}], False)):
            with self.subTest(count=self.count, members=self.members, more=self.more), self.assertRaises(SystemExit):
                cb.guard(self.target, True)

    def test_outside_reader_and_unreadable_collaborators_refused(self):
        for self.outside in ([dict(login='reader')], {}, None):
            with self.subTest(outside=self.outside), self.assertRaises(SystemExit):
                cb.guard(self.target, True)

    def test_partial_http_response_is_refused_even_with_an_empty_list(self):
        for status, headers in ((206, {}), (200, {'Link': '<more>; rel="next"'})):
            response = io.BytesIO(b'[]')
            response.status, response.headers = status, headers
            with self.subTest(status=status, headers=headers), \
                    mock.patch.object(cb.urllib.request, 'urlopen', return_value=response), self.assertRaises(cb.Unsafe):
                self.real_request('repos/acme/widget/collaborators', 'fixture', complete=True)

    def test_policy_required(self):
        self.policy = 'first_time_contributors'
        with self.assertRaises(SystemExit):
            cb.guard(self.target, True)

    def test_denied_or_unavailable_member_credential_fails_closed(self):
        for token, failure in ((None, None), (None, RuntimeError('HTTP 403'))):
            self.token.return_value, self.token.side_effect = token, failure
            with self.subTest(failure=failure), self.assertRaises(SystemExit):
                cb.guard(self.target, True)

    def test_user_owned_repo_uses_original_policy_without_members_access(self):
        self.repo['owner']['type'] = 'User'
        self.assertEqual(cb.guard(self.target, True), ['Alice'])
        self.token.assert_not_called()

    def test_autoscaler_refuses_after_member_added(self):
        self.assertEqual(cb.guard(self.target, True), ['Alice'])
        self.count = 2
        self.members.append(dict(login='Bob'))
        with self.assertRaises(cb.Unsafe):
            cb.public_rules(mock.Mock(get=self.get), argparse.Namespace(target=self.target, allow_public=True))

    def test_vm_hook_refuses_when_the_host_never_records_a_decision(self):
        with mock.patch.object(cb, 'ORG_JOB_GATE', self.root), \
                mock.patch.object(cb.time, 'monotonic', side_effect=[0, 120]), \
                self.assertRaisesRegex(cb.Unsafe, 'host refused'):
            cb.job_started(dict(GITHUB_EVENT_NAME='push', GITHUB_ACTOR='Alice', COLDBOX_PUBLIC_WRITERS='Alice'))

    def test_assignment_records_denial_on_credential_subprocess_error(self):
        args = argparse.Namespace(target=self.target, allow_public=True)
        writers = cb.guard(self.target, True)
        with mock.patch.object(cb.GitHub, 'get', side_effect=subprocess.CalledProcessError(1, 'gh')):
            self.assertFalse(cb.org_job_recheck(args, writers))

    def test_assignment_rechecks_on_host_before_vm_hook_allows_steps(self):
        for change in ('none', 'member', 'api failure'):
            with self.subTest(change=change), tempfile.TemporaryDirectory(dir=self.root) as tmp:
                state = Path(tmp)
                gate = state / 'gate'
                self.count = 1
                self.token.side_effect = None
                args = argparse.Namespace(target=self.target, allow_public=True, name='cbx-test', ttl=120,
                                          size='default', idle=0, once=True)
                allowed = []

                def api(*argv, **kwargs):
                    if argv[0] == '-X':
                        return dict(runner=dict(id=1), encoded_jit_config='jit')
                    return self.get(*argv)

                def guest(command, **kwargs):
                    if command == ('mkdir', str(gate)):
                        gate.mkdir()
                    if command[0] == 'touch' and str(gate) in command[-1]:
                        Path(command[-1]).touch()
                    return subprocess.CompletedProcess(command, 0, '')

                def assigned(command, log, data):
                    self.assertTrue(gate.is_dir())
                    self.assertEqual(list(gate.iterdir()), [])
                    if change == 'member':
                        self.count = 2
                    if change == 'api failure':
                        self.token.side_effect = RuntimeError('HTTP 403')
                    log.write(b'Running job: verify\n')
                    with mock.patch.object(cb, 'app_token', side_effect=AssertionError('VM requested a token')):
                        env = dict(GITHUB_EVENT_NAME='push', GITHUB_ACTOR='Alice', COLDBOX_PUBLIC_WRITERS='Alice')
                        if change == 'none':
                            allowed.append(cb.job_started(env) == 0)
                        else:
                            with self.assertRaisesRegex(cb.Unsafe, 'host refused'):
                                cb.job_started(env)
                            allowed.append(False)
                    return 0

                with mock.patch.multiple(cb, STATE=state, ORG_JOB_GATE=gate, gh=api,
                                         admit=mock.Mock(return_value=True), boot=mock.Mock(), ic=mock.Mock(),
                                         vm_shape=lambda _: (1, {}), host_cpus=lambda: 1, push_guest_files=mock.Mock(),
                                         ic_exec=lambda _, *args, **kw: args, tee=assigned,
                                         runner_admission=lambda *args: dict(host='host'), collect_tests=mock.Mock(),
                                         pull=mock.Mock(return_value=None), vm_resources=lambda _: {},
                                         copy_artifacts=lambda *a: ([], True), destroy=lambda _: True,
                                         publish_verify=mock.Mock()), mock.patch.object(cb.GitHub, 'get', self.get), \
                        mock.patch.object(cb.subprocess, 'run', guest):
                    quiet(cb.serve_worker, args, 'host')
                self.assertEqual(allowed, [change == 'none'])
