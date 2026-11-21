import pathlib
import sys
import tempfile
import unittest
import contextlib
import os
import socket
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import session_agent as agent
import service_runtime as runtime
from test_services import Device


class Backend:
    def __init__(self, value='pro1x_nordic'):
        self.value = value
        self.revision = 0
        self.before_write = lambda value: None
    def subscribe(self): pass
    def pump(self): return None
    def read(self): return self.value
    def write(self, value):
        self.before_write(value)
        self.value = value
    def choose(self, value):
        self.value = value
        self.revision += 1


@contextlib.contextmanager
def running_bridge(ready=False):
    with tempfile.TemporaryDirectory() as tmp:
        store = agent.StateStore(tmp)
        store.save('se')
        backend = Backend()
        session = [('c1', os.getuid()) if ready else None]
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: session[0])
        server = runtime.ControlServer(core, tmp + '/s')
        stop = threading.Event()
        def serve():
            while not stop.is_set(): server.step(0.01)
        thread = threading.Thread(target=serve)
        thread.start()
        def connect():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            sock.settimeout(1)
            sock.connect(tmp + '/s')
            return sock
        bridge = agent.SessionBridge(backend, store, connect)
        try:
            bridge.start()
            yield bridge, backend, session, store, device, connect
        finally:
            bridge.disconnect()
            stop.set()
            thread.join(2)
            server.close()
            store.close()


class ResumeBridgeTests(unittest.TestCase):
    def test_explicit_state_clear_cancels_pending_without_requiring_value_change(self):
        with running_bridge() as (bridge, backend, session, store, _, _):
            bridge.cycle()
            store.save('se')
            session[0] = ('c1', os.getuid())
            self.assertTrue(bridge.cycle()['ok'])
            self.assertFalse(bridge.enabled)
            self.assertEqual(backend.value, 'se')

    def test_notified_generic_choice_cancels_resume_even_if_same_as_fallback(self):
        for selected in ('se', 'de'):
            with self.subTest(selected=selected), running_bridge() as (bridge, backend, session, store, device, _):
                bridge.cycle()
                backend.choose(selected)
                session[0] = ('c1', os.getuid())
                self.assertTrue(bridge.cycle()['ok'])
                self.assertFalse(bridge.enabled)
                self.assertEqual(backend.value, selected)
                self.assertEqual(device.read_map(), runtime.ORIGINAL)
                self.assertIsNone(store.load_resume())

    def test_repeated_unavailable_session_preserves_intent_but_never_enables(self):
        with running_bridge() as (bridge, backend, _, store, device, _):
            for _ in range(3):
                self.assertFalse(bridge.cycle()['enabled'])
                self.assertEqual(backend.value, 'se')
                self.assertEqual(store.load_resume(), 'se')
                self.assertEqual(device.read_map(), runtime.ORIGINAL)

    def test_user_change_during_resume_wins_over_automatic_restore(self):
        with running_bridge() as (bridge, backend, session, store, _, _):
            bridge.cycle()
            session[0] = ('c1', os.getuid())
            backend.pump = lambda: backend.choose('de')
            self.assertTrue(bridge.cycle()['ok'])
            self.assertFalse(bridge.enabled)
            self.assertEqual(backend.value, 'de')
            self.assertIsNone(store.load_resume())

    def test_held_keys_refuse_resume_without_selecting_nordic(self):
        with running_bridge() as (bridge, backend, session, store, device, _):
            bridge.cycle()
            session[0] = ('c1', os.getuid())
            device.held = True
            self.assertFalse(bridge.cycle()['ok'])
            self.assertEqual(backend.value, 'se')
            self.assertEqual(device.read_map(), runtime.ORIGINAL)
            self.assertEqual(store.load_resume(), 'se')
            device.held = False
            self.assertTrue(bridge.cycle()['enabled'])

    def test_removal_clears_pending_before_ack_and_does_not_rearm_on_close(self):
        with running_bridge() as (bridge, backend, _, store, _, _):
            bridge.cycle()
            self.assertEqual(store.load_resume(), 'se')
            def ack(command):
                self.assertEqual(command, 'prepare-remove')
                self.assertIsNone(store.load_resume())
                return {'ok': True}
            bridge.exchange = ack
            bridge.prepare()
            bridge.close()
            self.assertEqual(backend.value, 'se')
            self.assertIsNone(store.load_resume())

    def test_watcher_shutdown_and_new_start_resume_only_after_safe_map_enable(self):
        with running_bridge(True) as (bridge, backend, _, store, device, connect):
            self.assertTrue(bridge.cycle()['enabled'])
            bridge.close()
            self.assertEqual(backend.value, 'se')
            self.assertEqual(store.load_resume(), 'se')
            next_bridge = agent.SessionBridge(backend, store, connect)
            try:
                next_bridge.start()
                self.assertTrue(next_bridge.cycle()['enabled'])
                self.assertEqual(backend.value, 'pro1x_nordic')
                self.assertEqual(device.read_map(), runtime.NORDIC)
            finally:
                next_bridge.disconnect()

    def test_initial_session_rejection_falls_back_then_restores_after_authenticated_enable(self):
        with running_bridge() as (bridge, backend, session, store, device, _):
            self.assertFalse(bridge.cycle()['ok'])
            self.assertEqual(backend.value, 'se')
            self.assertEqual(device.read_map(), runtime.ORIGINAL)
            session[0] = ('c1', os.getuid())
            def check_map(value):
                if value == 'pro1x_nordic': self.assertEqual(device.read_map(), runtime.NORDIC)
            backend.before_write = check_map
            self.assertTrue(bridge.cycle()['enabled'])
            self.assertEqual(backend.value, 'pro1x_nordic')
            self.assertIsNone(store.load_resume())


class ResumeStoreTests(unittest.TestCase):
    def test_own_dconf_tags_do_not_hide_external_changes(self):
        backend = agent.DconfBackend()
        backend.own_tags = [b'our-write']
        backend.changed(None, None, None, b'our-write', None)
        self.assertEqual(backend.revision, 0)
        backend.changed(None, None, None, b'user-write', None)
        self.assertEqual(backend.revision, 1)
        self.assertTrue(backend.dirty)

    def test_pending_nordic_uses_existing_state_file_and_is_cleared_by_generic_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = agent.StateStore(tmp)
            self.assertIsNone(store.load_resume())
            store.save('se', resume=True)
            store.close()
            store = agent.StateStore(tmp)
            self.assertEqual(store.load(), 'se')
            self.assertEqual(store.load_resume(), 'se')
            store.save('se')
            self.assertIsNone(store.load_resume())
            self.assertEqual(sorted(p.name for p in store.path.iterdir()), ['fallback.json'])
            store.close()


if __name__ == '__main__':
    unittest.main()
