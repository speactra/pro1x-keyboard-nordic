"""Bounded removal ordering; no real systemctl, dconf or phone access."""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import service_runtime as runtime


class RemovalTests(unittest.TestCase):
    def run_removal(self, fail=None, changed=False):
        import remove_package as removal
        events = []
        session = ('c1', 100000)
        sessions = iter([session, ('c2', 100001) if changed else session])
        def run(args, **kwargs):
            events.append((args, kwargs))
            if fail and fail in args:
                raise RuntimeError('injected failure')
        with mock.patch.object(removal, 'active_session', side_effect=lambda: next(sessions)), \
             mock.patch.object(removal.os, 'geteuid', return_value=0), \
             mock.patch.object(removal.pwd, 'getpwuid', return_value=mock.Mock(pw_gid=100000, pw_dir='/home/test', pw_name='test')), \
             mock.patch.object(removal.subprocess, 'run', side_effect=run):
            try:
                removal.remove()
            except Exception:
                return events, sys.exc_info()[1]
        return events, None

    def test_order_handshake_user_stop_fallback_root_stop_recovery(self):
        events, error = self.run_removal()
        self.assertIsNone(error)
        commands = [item[0] for item in events]
        self.assertEqual(commands[0][-1], 'prepare-remove')
        self.assertEqual(commands[1], ['/usr/bin/systemctl', '--user', 'disable', '--now', 'pro1x-keyboard-nordic-session.service'])
        self.assertEqual(commands[2][-1], 'prepare-remove')
        self.assertIn('session_agent.py', commands[2][1])
        self.assertEqual(commands[3], ['/usr/bin/systemctl', 'disable', '--now', 'pro1x-keyboard-nordic.service'])
        self.assertEqual(commands[4][-1], 'recover')
        for index in (1, 2):
            options = events[index][1]
            self.assertIn('preexec_fn', options)
            self.assertEqual(options['env']['XDG_RUNTIME_DIR'], '/run/user/100000')
            self.assertEqual(options['env']['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/run/user/100000/dbus/user_bus_socket')
        self.assertTrue(all(item[1]['check'] and item[1]['timeout'] for item in events))

    def test_handshake_failure_does_not_stop_services(self):
        events, error = self.run_removal(fail='prepare-remove')
        self.assertIsNotNone(error)
        self.assertEqual(len(events), 1)

    def test_user_stop_failure_prevents_root_stop(self):
        events, error = self.run_removal(fail='--user')
        self.assertIsNotNone(error)
        self.assertEqual(len(events), 2)

    def test_changed_session_aborts_before_stopping_services(self):
        events, error = self.run_removal(changed=True)
        self.assertIsNotNone(error)
        self.assertEqual(len(events), 1)

    def test_user_fallback_failure_prevents_root_stop(self):
        import remove_package as removal
        events = []
        def run(args, **kwargs):
            events.append(args)
            if args[1] == removal.BASE + 'session_agent.py':
                raise RuntimeError('fallback failed')
        with mock.patch.object(removal.os, 'geteuid', return_value=0), \
             mock.patch.object(removal, 'active_session', return_value=('c1', 100000)), \
             mock.patch.object(removal.pwd, 'getpwuid'), \
             mock.patch.object(removal.subprocess, 'run', side_effect=run):
            with self.assertRaises(RuntimeError): removal.remove()
        self.assertEqual(len(events), 3)

    def test_user_commands_drop_all_root_credentials_in_order(self):
        import remove_package as removal
        changes = []
        def run(args, **kwargs):
            if 'preexec_fn' in kwargs: kwargs['preexec_fn']()
        with mock.patch.object(removal.os, 'geteuid', return_value=0), \
             mock.patch.object(removal, 'active_session', return_value=('c1', 100000)), \
             mock.patch.object(removal.pwd, 'getpwuid', return_value=mock.Mock(pw_gid=100000, pw_dir='/home/test', pw_name='test')), \
             mock.patch.object(removal.os, 'setgroups', side_effect=lambda groups: changes.append(('groups', groups))), \
             mock.patch.object(removal.os, 'setgid', side_effect=lambda gid: changes.append(('gid', gid))), \
             mock.patch.object(removal.os, 'setuid', side_effect=lambda uid: changes.append(('uid', uid))), \
             mock.patch.object(removal.subprocess, 'run', side_effect=run):
            removal.remove()
        self.assertEqual(changes, [('groups', []), ('gid', 100000), ('uid', 100000)] * 2)

    def test_no_active_session_aborts_without_commands(self):
        import remove_package as removal
        with mock.patch.object(removal.os, 'geteuid', return_value=0), \
             mock.patch.object(removal, 'active_session', return_value=None), \
             mock.patch.object(removal.subprocess, 'run') as run:
            with self.assertRaises(runtime.UnsafeState): removal.remove()
            run.assert_not_called()

    def test_root_stop_failure_aborts_before_recovery(self):
        events, error = self.run_removal(fail='pro1x-keyboard-nordic.service')
        self.assertIsNotNone(error)
        self.assertEqual(len(events), 4)

    def test_recovery_failure_is_not_success(self):
        events, error = self.run_removal(fail='recover')
        self.assertIsNotNone(error)
        self.assertEqual(len(events), 5)

    def test_nonroot_rejected(self):
        import remove_package as removal
        with mock.patch.object(removal.os, 'geteuid', return_value=100000):
            with self.assertRaises(PermissionError): removal.remove()


if __name__ == '__main__':
    unittest.main()
