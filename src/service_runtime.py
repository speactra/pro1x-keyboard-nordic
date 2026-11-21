#!/usr/bin/python3
"""Fixed-mode, expiring Pro1 X keyboard lease. Python 3.8+, stdlib only."""
import time
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
from pro1x_nordic import ORIGINAL, NORDIC, UnsafeState, transition

SOCKET = '/run/pro1x-keyboard-nordic/control.sock'
LEASE_SECONDS = 6.0


class LeaseController:
    def __init__(self, discover, active_session, clock=time.monotonic):
        self.discover = discover
        self.active_session = active_session
        self.clock = clock
        self.device = None
        self.owner = None
        self.session = None
        self.deadline = 0
        self.error = None
        self.removing = False

    def status(self):
        observed = None
        if self.device is not None:
            try:
                self.device.validate()
                observed = list(self.device.read_map())
            except (OSError, ValueError) as error: self.error = str(error)
        return {'ok': self.error is None, 'enabled': self.owner is not None,
                'lease_seconds': LEASE_SECONDS, 'removing': self.removing,
                'map': observed, 'device_present': observed is not None,
                'error': self.error}

    def begin_remove(self):
        self.removing = True
        session = self.active_session()
        if session is None: raise UnsafeState('no active local session to confirm layout fallback')
        return session

    def confirm_remove(self, uid, session):
        if not self.removing or session != self.active_session() or session[1] != uid:
            raise PermissionError('session changed during removal')
        if self.device is None: self.device = self.discover()
        self.restore()
        return dict(self.status(), safe_to_remove=True)

    def restore(self):
        if self.device is not None:
            reconcile(self.device, False)
        self.owner = None
        self.session = None
        self.deadline = 0
        self.error = None

    def request(self, uid, command, client, now=None):
        now = self.clock() if now is None else now
        session = self.active_session()
        if self.owner is not None and session != self.session:
            self.restore()
        if session is None or session[1] != uid:
            raise PermissionError('peer is not the active local seat0 user')
        if command == 'status':
            return self.status()
        if command == 'enable':
            if self.removing: raise UnsafeState('removal in progress')
            if self.owner is not None and self.owner != client:
                raise UnsafeState('lease already owned')
            if self.device is None: self.device = self.discover()
            self.device.validate()
            reconcile(self.device, True)
            self.owner, self.session = client, session
            self.deadline = now + LEASE_SECONDS
        elif command == 'heartbeat':
            if self.owner is not None and now >= self.deadline:
                self.restore()
            if self.owner != client or session != self.session:
                raise PermissionError('no matching lease')
            self.deadline = now + LEASE_SECONDS
        elif command == 'disable':
            if self.owner not in (None, client): raise PermissionError('different lease owner')
            self.restore()
        else:
            raise ValueError('unsupported command')
        return self.status()

    def tick(self, now=None):
        now = self.clock() if now is None else now
        if self.device is not None:
            try:
                self.device.validate()
            except OSError:
                self.device.close()
                self.device = None
                self.owner = self.session = None
                self.deadline = 0
        if self.device is None:
            self.device = self.discover()
            reconcile(self.device, False)
        if self.owner is not None:
            if now >= self.deadline or self.active_session() != self.session:
                self.restore()
            elif tuple(self.device.read_map()) != NORDIC:
                raise UnsafeState('leased modifier map changed unexpectedly')
        else:
            reconcile(self.device, False)
        self.error = None

    def disconnect(self, client):
        if client == self.owner:
            # Keep retrying a held-key-deferred revoke, not the old live lease.
            self.deadline = 0
            self.restore()


OWNED_MAPS = frozenset((ORIGINAL, NORDIC,
    (56, 54, 29, 125, 464, 54),
    (56, 42, 29, 125, 464, 54),
    (56, 54, 29, 125, 100, 54)))


def reconcile(device, enable):
    device.validate()
    current = tuple(device.read_map())
    if current not in OWNED_MAPS: raise UnsafeState('unowned modifier map')
    desired = NORDIC if enable else ORIGINAL
    if current == desired: return
    if device.keys_held(): raise UnsafeState('keys held; refusing map change')
    if current not in (ORIGINAL, NORDIC):
        for index in (1, 4, 5): device.write_key(index, ORIGINAL[index])
        if tuple(device.read_map()) != ORIGINAL: raise UnsafeState('recovery readback mismatch')
    transition(device.read_map, device.write_key, enable)


KEYMAP = struct.Struct('=BBHI32s')
GET_KEYCODE, SET_KEYCODE = 0x80284504, 0x40284504
DEVICE_ID = (0x0018, 0x9523, 0x0701, 0x0001)
DRIVER = '/sys/bus/i2c/drivers/aw9523b'


def run_text(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=2, check=True, env=dict(os.environ, LC_ALL='C'))
    if len(result.stdout) > 8192: raise ValueError('subprocess output too long')
    return result.stdout.decode('utf-8').strip()


def properties(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def active_session():
    try:
        seat = properties(run_text(['/usr/bin/loginctl', 'show-seat', 'seat0', '-p', 'ActiveSession']))
        session = seat.get('ActiveSession', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session): return None
        info = properties(run_text(['/usr/bin/loginctl', 'show-session', session,
                                    '-p', 'User', '-p', 'Active', '-p', 'Remote',
                                    '-p', 'Seat', '-p', 'Class']))
        if (info.get('Active'), info.get('Remote'), info.get('Seat'), info.get('Class')) != (
                'yes', 'no', 'seat0', 'user'): return None
        uid = int(info['User'])
        return (session, uid) if uid > 0 else None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def decode_request(data):
    if len(data) > 256: raise ValueError('oversized request')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate key')
            result[key] = value
        return result
    value = json.loads(data.decode('ascii'), object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != {'command'}:
        raise ValueError('only command is accepted')
    command = value['command']
    if command not in ('enable', 'disable', 'heartbeat', 'prepare-remove', 'status'):
        raise ValueError('unsupported command')
    return command


class Keyboard:
    def __init__(self, event):
        self.event = Path(event)
        self.sysreal = self.event.resolve(strict=True)
        self.node = Path('/dev/input') / self.event.name
        self.fd = os.open(str(self.node), os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.open_stat = os.fstat(self.fd)
        try: self.validate()
        except Exception:
            self.close()
            raise

    def validate(self):
        if self.event.resolve(strict=True) != self.sysreal: raise OSError('sysfs device replaced')
        info = os.fstat(self.fd)
        node = os.stat(str(self.node), follow_symlinks=False)
        major, minor = (int(x) for x in (self.event / 'dev').read_text().strip().split(':'))
        if (not stat.S_ISCHR(info.st_mode) or not stat.S_ISCHR(node.st_mode)
                or info.st_rdev != os.makedev(major, minor)
                or (node.st_dev, node.st_ino, node.st_rdev) !=
                   (self.open_stat.st_dev, self.open_stat.st_ino, self.open_stat.st_rdev)):
            raise OSError('device node identity mismatch')
        dev = self.event / 'device'
        ident = tuple(int((dev / 'id' / key).read_text().strip(), 16)
                      for key in ('bustype', 'vendor', 'product', 'version'))
        if ident != DEVICE_ID or (dev / 'name').read_text().rstrip('\n') != 'Fxtec Pro1':
            raise OSError('sysfs input identity mismatch')
        if not any(str((parent / 'driver').resolve()) == DRIVER
                   for parent in (self.sysreal,) + tuple(self.sysreal.parents)):
            raise OSError('input is not driven by aw9523b')
        ident_buf = bytearray(8)
        fcntl.ioctl(self.fd, 0x80084502, ident_buf, True)
        name = bytearray(256)
        fcntl.ioctl(self.fd, 0x81004506, name, True)
        if struct.unpack('=HHHH', ident_buf) != DEVICE_ID or bytes(name).split(b'\0', 1)[0] != b'Fxtec Pro1':
            raise OSError('evdev identity mismatch')

    def read_key(self, index):
        data = bytearray(KEYMAP.pack(1, 0, index, 0, b'\0' * 32))
        fcntl.ioctl(self.fd, GET_KEYCODE, data, True)
        return KEYMAP.unpack(data)[3]

    def read_map(self): return tuple(self.read_key(index) for index in range(6))

    def write_key(self, index, code):
        if index not in (1, 4, 5) or code not in (ORIGINAL[index], NORDIC[index]):
            raise ValueError('not a package-owned keycode')
        fcntl.ioctl(self.fd, SET_KEYCODE, KEYMAP.pack(1, 0, index, code, b'\0' * 32))

    def keys_held(self):
        data = bytearray(96)
        fcntl.ioctl(self.fd, 0x80604518, data, True)
        return any(data)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def discover_keyboard():
    found = []
    for event in sorted(Path('/sys/class/input').glob('event[0-9]*')):
        try: found.append(Keyboard(event))
        except (OSError, ValueError): continue
    if len(found) != 1:
        for device in found: device.close()
        raise OSError('expected exactly one verified Fxtec Pro1 input device; found {}'.format(len(found)))
    return found[0]


def send_packet(sock, value):
    payload = json.dumps(value, separators=(',', ':')).encode('ascii')
    if len(payload) > 4096: raise ValueError('response too large')
    sock.sendall(payload)


def receive_packet(sock):
    data = sock.recv(4097)
    if not data or len(data) > 4096: raise OSError('invalid server packet')
    result = json.loads(data.decode('ascii'))
    if not isinstance(result, dict): raise ValueError('invalid server reply')
    return result


def connect_control():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    sock.settimeout(2)
    try:
        sock.connect(SOCKET)
        if struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1] != 0:
            raise PermissionError('control server is not uid 0')
        return sock
    except Exception:
        sock.close()
        raise


def request_packet(sock, command):
    send_packet(sock, {'command': command})
    result = receive_packet(sock)
    if result.get('event'): raise UnsafeState('asynchronous control event: ' + str(result['event']))
    if not result.get('ok'): raise UnsafeState(result.get('error', 'request rejected'))
    return result


class ControlServer:
    """Single-threaded, bounded SOCK_SEQPACKET server; injected core for tests."""
    def __init__(self, core, path=SOCKET):
        self.core, self.path = core, path
        self.selector = selectors.DefaultSelector()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.listener.bind(path)
        os.chmod(path, 0o666)
        self.listener.listen(8)
        self.listener.setblocking(False)
        self.selector.register(self.listener, selectors.EVENT_READ)
        self.clients = {}
        self.pending = None

    def drop(self, client):
        if client not in self.clients: return
        try: self.core.disconnect(client)
        except Exception as error: self.core.error = str(error)
        self.selector.unregister(client)
        del self.clients[client]
        client.close()

    def reply(self, client, value):
        try: send_packet(client, value)
        except OSError: self.drop(client)

    def step(self, timeout=0.25):
        for key, _ in self.selector.select(timeout)[:1]:
            client = key.fileobj
            if client is self.listener:
                connection, _ = self.listener.accept()
                connection.settimeout(0.1)
                uid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
                session = self.core.active_session()
                if uid != 0 and (session is None or uid != session[1]):
                    connection.close()
                    continue
                if len(self.clients) >= 16:
                    connection.close()
                    continue
                self.clients[connection] = {'uid': uid, 'last': time.monotonic(), 'agent': False}
                self.selector.register(connection, selectors.EVENT_READ)
                continue
            try:
                data = client.recv(257)
                if not data:
                    self.drop(client)
                    continue
                command = decode_request(data)
                peer = self.clients[client]
                peer['last'] = time.monotonic()
                uid = peer['uid']
                if command == 'prepare-remove' and uid == 0:
                    if self.pending is not None: raise UnsafeState('removal already pending')
                    session = self.core.begin_remove()
                    agents = [sock for sock, entry in self.clients.items()
                              if entry['uid'] == session[1] and entry['agent']]
                    if not agents: raise UnsafeState('no authenticated session agent to confirm fallback')
                    self.pending = (client, session, set(agents), time.monotonic() + 5)
                    for agent in agents: self.reply(agent, {'event': 'prepare-remove'})
                    continue
                if command == 'prepare-remove':
                    if self.pending is None or client not in self.pending[2]:
                        raise PermissionError('no pending removal for this agent')
                    result = self.core.confirm_remove(uid, self.pending[1])
                    self.reply(self.pending[0], result)
                    self.pending = None
                elif command == 'status' and uid == 0:
                    result = self.core.status()
                else:
                    result = self.core.request(uid, command, client)
                    peer['agent'] = True
                self.reply(client, result)
            except Exception as error:
                self.reply(client, {'ok': False, 'error': str(error)[:300]})
                self.drop(client)
        now = time.monotonic()
        if self.pending is not None and now >= self.pending[3]:
            self.reply(self.pending[0], {'ok': False, 'safe_to_remove': False,
                                       'error': 'layout fallback confirmation timed out'})
            self.pending = None
        for client, peer in list(self.clients.items()):
            if now - peer['last'] > 12: self.drop(client)

    def close(self):
        for client in list(self.clients): self.drop(client)
        self.selector.unregister(self.listener)
        self.listener.close()
        self.selector.close()
        os.unlink(self.path)


def sd_notify(message):
    target = os.environ.get('NOTIFY_SOCKET')
    if not target: return
    if target.startswith('@'): target = '\0' + target[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.2)
        sock.sendto(message.encode('utf-8'), target)
    except OSError: pass
    finally: sock.close()


def acquire_lock():
    directory = Path(SOCKET).parent
    directory.mkdir(mode=0o755, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError('runtime directory must be root-owned and not writable by users')
    fd = os.open(str(directory / 'device.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise PermissionError('unsafe runtime lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def recover_map(timeout=5):
    deadline = time.monotonic() + timeout
    device = None
    try:
        while True:
            try:
                if device is None: device = discover_keyboard()
                reconcile(device, False)
                return {'ok': True, 'enabled': False, 'map': list(device.read_map())}
            except (OSError, UnsafeState):
                if device is not None:
                    device.close()
                    device = None
                if time.monotonic() >= deadline: raise
                time.sleep(0.1)
    finally:
        if device is not None: device.close()


def run_daemon():
    lock = acquire_lock()
    server = None
    core = LeaseController(discover_keyboard, active_session)
    stopping = [False]
    handlers = {}
    try:
        if os.path.lexists(SOCKET):
            info = os.lstat(SOCKET)
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
                raise PermissionError('refusing to remove unsafe control socket')
            os.unlink(SOCKET)
        server = ControlServer(core)
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, lambda *_: stopping.__setitem__(0, True))
        sd_notify('READY=1')
        last_tick = 0
        while not stopping[0]:
            server.step()
            if time.monotonic() - last_tick >= 0.5:
                try: core.tick()
                except Exception as error:
                    core.error = str(error)[:300]
                    for client in list(server.clients): server.drop(client)
                last_tick = time.monotonic()
                sd_notify('WATCHDOG=1')
        sd_notify('STOPPING=1')
        return {'ok': True, 'stopped': True}
    finally:
        if server is not None: server.close()
        if core.device is not None: core.device.close()
        try:
            # Independent rediscovery, also used by ExecStopPost's separate process.
            recover_map()
        finally:
            for sig, handler in handlers.items(): signal.signal(sig, handler)
            os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('daemon', 'recover', 'prepare-remove', 'status'))
    args = parser.parse_args(argv)
    try:
        if args.command in ('daemon', 'recover'):
            if os.geteuid() != 0: raise PermissionError('root is required')
            if args.command == 'daemon': result = run_daemon()
            else:
                lock = acquire_lock()
                try: result = recover_map()
                finally: os.close(lock)
        else:
            if args.command == 'prepare-remove' and os.geteuid() != 0:
                raise PermissionError('root is required for coordinated removal')
            with connect_control() as sock:
                if args.command == 'prepare-remove': sock.settimeout(8)
                result = request_packet(sock, args.command)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('ok') else 1
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)[:400],
                          'safe_to_remove': False}, sort_keys=True))
        return 1


if __name__ == '__main__': sys.exit(main())
