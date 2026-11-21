import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import service_runtime as runtime
from unittest import mock
import struct
from pro1x_nordic import ORIGINAL, NORDIC, UnsafeState


class Device:
    def __init__(self, values=ORIGINAL):
        self.values = list(values)
        self.held = False
        self.closed = False
    def read_map(self): return tuple(self.values)
    def write_key(self, index, code): self.values[index] = code
    def keys_held(self): return self.held
    def validate(self):
        if self.closed: raise OSError('device gone')
    def close(self): self.closed = True


class CoreTests(unittest.TestCase):
    def test_authenticated_lease_expires_to_original(self):
        device = Device()
        session = lambda: ('c1', 100000)
        core = runtime.LeaseController(lambda: device, session, clock=lambda: 0)
        with self.assertRaises(PermissionError): core.request(200000, 'enable', 'client', 0)
        self.assertEqual(device.read_map(), ORIGINAL)
        self.assertTrue(core.request(100000, 'enable', 'client', 0)['enabled'])
        self.assertEqual(device.read_map(), NORDIC)
        core.tick(10)
        self.assertEqual(device.read_map(), ORIGINAL)
        self.assertFalse(core.status()['enabled'])

    def test_status_reports_observed_fixed_map(self):
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        core.tick(0)
        self.assertEqual(core.status()['map'], list(ORIGINAL))
        self.assertTrue(core.status()['device_present'])

    def test_late_heartbeat_cannot_resurrect_expired_lease(self):
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        core.request(100000, 'enable', 'a', 0)
        with self.assertRaises(PermissionError): core.request(100000, 'heartbeat', 'a', 10)
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_idle_tick_recovers_owned_partial_state(self):
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        core.tick(0)
        device.values = [56, 42, 29, 125, 464, 54]
        core.tick(1)
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_request_binds_lease_to_session_not_only_uid(self):
        device = Device()
        active = [('c1', 100000)]
        core = runtime.LeaseController(lambda: device, lambda: active[0])
        core.request(100000, 'enable', 'a', 0)
        active[0] = ('c2', 100000)
        with self.assertRaises(PermissionError): core.request(100000, 'heartbeat', 'a', 1)
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_disconnect_held_key_retries_without_waiting_for_lease(self):
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        core.request(100000, 'enable', 'a', 1)
        device.held = True
        with self.assertRaises(UnsafeState): core.disconnect('a')
        device.held = False
        core.tick(2)
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_session_loss_disconnect_and_held_safety(self):
        device = Device()
        active = [('c1', 100000)]
        core = runtime.LeaseController(lambda: device, lambda: active[0])
        core.request(100000, 'enable', 'a', 0)
        device.held = True
        with self.assertRaises(UnsafeState): core.tick(9)
        self.assertTrue(core.status()['enabled'])
        device.held = False
        active[0] = ('c2', 100001)
        core.tick(1)
        self.assertEqual(device.read_map(), ORIGINAL)
        active[0] = ('c1', 100000)
        core.request(100000, 'enable', 'a', 1)
        core.disconnect('a')
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_enumerated_recovery_and_unknown_refusal(self):
        for values in runtime.OWNED_MAPS:
            device = Device(values)
            runtime.reconcile(device, False)
            self.assertEqual(device.read_map(), ORIGINAL)
        device = Device((56, 42, 29, 125, 464, 464))
        with self.assertRaises(UnsafeState): runtime.reconcile(device, False)
        self.assertEqual(device.values[1], 42)

    def test_failed_transition_rolls_back(self):
        class Failed(Device):
            def write_key(self, index, code):
                if index == 4 and code == 100: raise OSError('injected')
                super().write_key(index, code)
        device = Failed()
        with self.assertRaises(OSError): runtime.reconcile(device, True)
        self.assertEqual(device.read_map(), ORIGINAL)

    def test_recreated_device_restored_without_renewing_lease(self):
        old, new = Device(), Device(NORDIC)
        devices = [old, new]
        core = runtime.LeaseController(lambda: devices.pop(0), lambda: ('c1', 100000))
        core.request(100000, 'enable', 'a', 0)
        old.closed = True
        core.tick(1)
        self.assertEqual(new.read_map(), ORIGINAL)
        self.assertFalse(core.status()['enabled'])


class BoundaryTests(unittest.TestCase):
    def test_local_tty_session_allowed_remote_rejected(self):
        responses = ['ActiveSession=c1\n', 'User=100000\nActive=yes\nRemote=no\nSeat=seat0\nClass=user\nType=tty\n']
        with mock.patch.object(runtime, 'run_text', side_effect=responses):
            self.assertEqual(runtime.active_session(), ('c1', 100000))
        responses[1] = responses[1].replace('Remote=no', 'Remote=yes')
        with mock.patch.object(runtime, 'run_text', side_effect=responses):
            self.assertIsNone(runtime.active_session())

    def test_protocol_is_bounded_fixed_mode(self):
        self.assertEqual(runtime.decode_request(b'{"command":"enable"}'), 'enable')
        for value in (b'{"command":"enable","path":"/dev/a"}', b'[]', b'x' * 257,
                      b'{"command":"exec"}', b'{"command":"enable","command":"disable"}'):
            with self.assertRaises(ValueError): runtime.decode_request(value)

    def test_device_rejects_regular_nodes_and_wrong_evdev_identity(self):
        import tempfile
        from pathlib import Path
        import types
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            event = base / 'event0'
            (event / 'device' / 'id').mkdir(parents=True)
            (event / 'dev').write_text('13:64')
            (event / 'device' / 'name').write_text('Fxtec Pro1\n')
            for key, value in zip(('bustype', 'vendor', 'product', 'version'), ('0018', '9523', '0701', '0001')):
                (event / 'device' / 'id' / key).write_text(value)
            (event / 'driver').symlink_to(runtime.DRIVER)
            node = base / 'node'
            node.write_text('not an evdev node')
            device = object.__new__(runtime.Keyboard)
            device.event, device.sysreal, device.node = event, event.resolve(), node
            device.fd = os.open(str(node), os.O_RDONLY)
            device.open_stat = os.fstat(device.fd)
            try:
                with self.assertRaises(OSError): device.validate()
                fake = types.SimpleNamespace(st_mode=0o020600, st_uid=os.getuid(), st_rdev=os.makedev(13, 64), st_dev=1, st_ino=2)
                device.open_stat = fake
                def identity(fd, op, data, mutate):
                    if op == 0x80084502: data[:] = struct.pack('=HHHH', *runtime.DEVICE_ID)
                    elif op == 0x81004506: data[:12] = b'Wrong device'
                with mock.patch.object(runtime.os, 'fstat', return_value=fake), mock.patch.object(runtime.os, 'stat', return_value=fake), mock.patch.object(runtime.fcntl, 'ioctl', side_effect=identity):
                    with self.assertRaises(OSError): device.validate()
            finally: device.close()

    def test_input_abi_and_identity(self):
        self.assertEqual(runtime.KEYMAP.size, 40)
        calls = []
        def ioctl(fd, op, data, mutate=True):
            calls.append((op, bytes(data)))
            if op == runtime.GET_KEYCODE:
                data[:] = runtime.KEYMAP.pack(1, 0, 4, 100, b'\0' * 32)
        device = object.__new__(runtime.Keyboard)
        device.fd = 123
        with mock.patch.object(runtime.fcntl, 'ioctl', side_effect=ioctl):
            self.assertEqual(device.read_key(4), 100)
            device.write_key(5, 54)
        self.assertEqual(calls[0][0], 0x80284504)
        self.assertEqual(calls[1][0], 0x40284504)
        self.assertEqual(runtime.KEYMAP.unpack(calls[1][1])[:4], (1, 0, 5, 54))


class ServerTests(unittest.TestCase):
    def test_foreign_idle_peers_cannot_fill_client_pool(self):
        import tempfile
        import socket
        import json
        # Real SO_PEERCRED from this process; a different active UID makes it foreign.
        with tempfile.TemporaryDirectory() as tmp:
            active = [('c1', os.getuid() + 1)]
            core = runtime.LeaseController(Device, lambda: active[0])
            server = runtime.ControlServer(core, tmp + '/s')
            peers = []
            try:
                for _ in range(16):
                    peer = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                    peers.append(peer)
                    peer.connect(tmp + '/s')
                    server.step(0)
                self.assertEqual(len(server.clients), 0)
                active[0] = ('c2', os.getuid())
                legitimate = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                peers.append(legitimate)
                legitimate.settimeout(1)
                legitimate.connect(tmp + '/s')
                server.step(0)
                legitimate.send(b'{"command":"status"}')
                server.step(0)
                self.assertTrue(json.loads(legitimate.recv(4096))['ok'])
            finally:
                for peer in peers: peer.close()
                server.close()

    def test_socket_roundtrip_and_disconnect_restore(self):
        import tempfile
        import socket
        with tempfile.TemporaryDirectory() as tmp:
            device = Device()
            core = runtime.LeaseController(lambda: device, lambda: ('c1', os.getuid()))
            server = runtime.ControlServer(core, tmp + '/s')
            client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            try:
                client.connect(tmp + '/s')
                server.step(0)
                client.send(b'{"command":"enable"}')
                server.step(0)
                import json
                self.assertTrue(json.loads(client.recv(4096))['enabled'])
                client.close()
                server.step(0)
                self.assertEqual(device.read_map(), ORIGINAL)
            finally:
                client.close()
                server.close()

    def test_socket_removal_handshake_restores_after_agent_ack(self):
        import tempfile
        import socket
        import json
        with tempfile.TemporaryDirectory() as tmp:
            device = Device()
            core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
            server = runtime.ControlServer(core, tmp + '/s')
            agent = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            admin = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            for sock in (agent, admin): sock.settimeout(1)
            try:
                agent.connect(tmp + '/s')
                # Credentials must be present at accept, not patched into the pool later.
                with mock.patch.object(runtime.struct, 'unpack', return_value=(123, 100000, 100000)):
                    server.step(0)
                self.assertEqual(server.clients[next(iter(server.clients))]['uid'], 100000)
                agent.send(b'{"command":"enable"}'); server.step(0)
                self.assertTrue(json.loads(agent.recv(4096))['enabled'])
                admin.connect(tmp + '/s')
                with mock.patch.object(runtime.struct, 'unpack', return_value=(124, 0, 0)):
                    server.step(0)
                self.assertEqual(server.clients[list(server.clients)[-1]]['uid'], 0)
                admin.send(b'{"command":"prepare-remove"}'); server.step(0)
                self.assertEqual(json.loads(agent.recv(4096))['event'], 'prepare-remove')
                self.assertEqual(device.read_map(), NORDIC)
                agent.send(b'{"command":"prepare-remove"}'); server.step(0)
                self.assertTrue(json.loads(admin.recv(4096))['safe_to_remove'])
                self.assertEqual(device.read_map(), ORIGINAL)
                self.assertTrue(json.loads(agent.recv(4096))['ok'])
            finally:
                agent.close(); admin.close(); server.close()

    def test_prepare_requires_fallback_confirmation(self):
        device = Device()
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        core.request(100000, 'enable', 'agent', 0)
        core.begin_remove()
        with self.assertRaises(UnsafeState): core.request(100000, 'enable', 'agent', 1)
        self.assertEqual(device.read_map(), NORDIC)
        core.confirm_remove(100000, ('c1', 100000))
        self.assertEqual(device.read_map(), ORIGINAL)
        self.assertTrue(core.status()['removing'])


class AgentTests(unittest.TestCase):
    def test_disconnect_falls_back_only_while_selected(self):
        import session_agent as agent
        class Backend:
            value = 'pro1x_nordic'
            def read(self): return self.value
            def write(self, value): self.value = value
        backend = Backend()
        state = agent.LayoutState(backend, 'fi')
        self.assertTrue(state.fallback())
        self.assertEqual(backend.value, 'fi')
        backend.value = 'de'
        self.assertTrue(state.fallback())
        self.assertEqual(backend.value, 'de')

    def test_fallback_readback_failure_blocks_removal(self):
        import session_agent as agent
        class Backend:
            def read(self): return 'pro1x_nordic'
            def write(self, value): pass
        with self.assertRaises(UnsafeState): agent.LayoutState(Backend(), 'us').fallback()

    def test_watcher_subscribed_before_initial_read(self):
        import session_agent as agent
        calls = []
        class Backend:
            def subscribe(self): calls.append('subscribe')
            def read(self): calls.append('read'); return 'us'
        class Store:
            def load(self): return 'us'
            def load_resume(self): return None
            def save(self, value, resume=False): pass
        bridge = agent.SessionBridge(Backend(), Store(), lambda: None)
        bridge.start()
        self.assertEqual(calls[:2], ['subscribe', 'read'])

    def test_enable_failure_triggers_conditional_fallback(self):
        import session_agent as agent
        class Backend:
            value = 'pro1x_nordic'
            def subscribe(self): pass
            def read(self): return self.value
            def write(self, value): self.value = value
        class Store:
            def load(self): return 'fi'
            def load_resume(self): return None
            def save(self, value, resume=False): pass
        def failed_connect(): raise OSError('offline')
        backend = Backend()
        bridge = agent.SessionBridge(backend, Store(), failed_connect)
        bridge.start()
        bridge.cycle()
        self.assertEqual(backend.value, 'fi')
        self.assertFalse(bridge.enabled)

    def test_bridge_happy_path_over_real_local_socket(self):
        import session_agent as agent
        import tempfile
        import threading
        import socket
        class Backend:
            value = 'us'
            def subscribe(self): pass
            def read(self): return self.value
            def write(self, value): self.value = value
        class Store:
            def load(self): return 'us'
            def load_resume(self): return None
            def save(self, value, resume=False): pass
        with tempfile.TemporaryDirectory() as tmp:
            device = Device()
            core = runtime.LeaseController(lambda: device, lambda: ('c1', os.getuid()))
            server = runtime.ControlServer(core, tmp + '/s')
            stopped = threading.Event()
            def serve():
                while not stopped.is_set(): server.step(0.01)
            thread = threading.Thread(target=serve)
            thread.start()
            def connect():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                sock.settimeout(1)
                sock.connect(tmp + '/s')
                return sock
            backend = Backend()
            bridge = agent.SessionBridge(backend, Store(), connect)
            try:
                bridge.start()
                self.assertTrue(bridge.cycle()['ok'])
                backend.value = 'pro1x_nordic'
                self.assertTrue(bridge.cycle()['enabled'])
                self.assertEqual(device.read_map(), NORDIC)
                backend.value = 'de'
                self.assertFalse(bridge.cycle()['enabled'])
                self.assertEqual(device.read_map(), ORIGINAL)
                bridge.close()
                self.assertEqual(backend.value, 'de')
            finally:
                bridge.disconnect()
                stopped.set(); thread.join(2)
                server.close()

    def test_dconf_read_parses_only_string_values(self):
        import session_agent as agent
        backend = agent.DconfBackend()
        with mock.patch.object(agent, 'run_text', return_value="'fi'"):
            self.assertEqual(backend.read(), 'fi')
        with mock.patch.object(agent, 'run_text', return_value='false'):
            with self.assertRaises(UnsafeState): backend.read()
        backend.close()

    def test_safe_state_persistence_and_default(self):
        import session_agent as agent
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = agent.StateStore(tmp)
            self.assertEqual(store.load(), 'us')
            store.save('fi')
            self.assertEqual(store.load(), 'fi')
            for value in ('pro1x_nordic', '../x', 'a' * 1000):
                with self.assertRaises(ValueError): store.save(value)


class CommandTests(unittest.TestCase):
    def test_agent_cli_prepare_remove_outputs_confirmed_fallback(self):
        import session_agent as agent
        import io
        import json
        backend = mock.Mock()
        backend.read.return_value = 'us'
        store = mock.Mock()
        store.load.return_value = 'us'
        with mock.patch.object(agent, 'DconfBackend', return_value=backend), mock.patch.object(agent, 'StateStore', return_value=store):
            with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(agent.main(['prepare-remove']), 0)
        self.assertTrue(json.loads(output.getvalue())['layout_safe'])
        backend.write.assert_not_called()

    def test_confirm_removal_discovers_and_checks_map(self):
        device = Device(NORDIC)
        core = runtime.LeaseController(lambda: device, lambda: ('c1', 100000))
        session = core.begin_remove()
        result = core.confirm_remove(100000, session)
        self.assertEqual(device.read_map(), ORIGINAL)
        self.assertTrue(result['safe_to_remove'])

    def test_recover_has_readback_and_closes_device(self):
        device = Device(NORDIC)
        with mock.patch.object(runtime, 'discover_keyboard', return_value=device):
            result = runtime.recover_map(timeout=0)
        self.assertTrue(result['ok'])
        self.assertEqual(result['map'], list(ORIGINAL))
        self.assertTrue(device.closed)

    def test_sd_notify_uses_unix_datagram(self):
        import socket
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                sock.bind(tmp + '/notify')
                sock.settimeout(1)
                with mock.patch.dict(os.environ, {'NOTIFY_SOCKET': tmp + '/notify'}):
                    runtime.sd_notify('WATCHDOG=1')
                self.assertEqual(sock.recv(100), b'WATCHDOG=1')
            finally: sock.close()

    def test_cli_status_is_json_failure_when_offline(self):
        import io
        import json
        with mock.patch.object(runtime, 'connect_control', side_effect=OSError('offline')):
            with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(runtime.main(['status']), 1)
        self.assertFalse(json.loads(output.getvalue())['ok'])


if __name__ == '__main__': unittest.main()
