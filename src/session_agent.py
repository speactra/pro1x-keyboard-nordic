#!/usr/bin/python3
"""Unprivileged dconf-to-lease bridge. Never reads keyboard input events."""
import argparse
import ast
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import time
from pro1x_nordic import LAYOUT, UnsafeState
from service_runtime import connect_control, receive_packet, run_text, send_packet, sd_notify

KEY = '/desktop/lipstick-jolla-home/layout'


def generic_layout(value):
    return (isinstance(value, str) and value != LAYOUT
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_+()-]{0,63}', value) is not None)


class StateStore:
    """Only our own XDG config directory; reject symlinks and foreign owners."""
    def __init__(self, config=None):
        base = config or os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
        if not os.path.isabs(base): raise ValueError('XDG_CONFIG_HOME must be absolute')
        self.path = Path(base) / 'pro1x-keyboard-nordic'
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise PermissionError('unsafe private state directory')
        self.dirfd = os.open(str(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)

    def _load(self):
        try:
            fd = os.open('fallback.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.dirfd)
            with os.fdopen(fd, 'r') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid(): return {}
                result = json.loads(stream.read(1025))
            return result if isinstance(result, dict) else {}
        except (OSError, ValueError): return {}

    def load(self):
        value = self._load().get('layout')
        return value if generic_layout(value) else 'us'

    def load_resume(self):
        result = self._load()
        value = result.get('layout')
        return value if result.get('resume') is True and generic_layout(value) else None

    def save(self, value, resume=False):
        if not generic_layout(value): raise ValueError('not a generic layout identifier')
        fd = os.open('fallback.json', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.dirfd)
        with os.fdopen(fd, 'w') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise PermissionError('unsafe fallback file')
            stream.seek(0)
            stream.write(json.dumps({'layout': value, 'resume': bool(resume)}) + '\n')
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())

    def close(self):
        if self.dirfd is not None:
            os.close(self.dirfd)
            self.dirfd = None

    def __del__(self):
        if getattr(self, 'dirfd', None) is not None: self.close()


class LayoutState:
    def __init__(self, backend, last_generic='us'):
        self.backend = backend
        self.last_generic = last_generic if generic_layout(last_generic) else 'us'

    def fallback(self):
        # Re-read immediately before writing; no write if another layout is now selected.
        if self.backend.read() == LAYOUT:
            self.backend.write(self.last_generic)
        if self.backend.read() == LAYOUT: raise UnsafeState('fallback readback still selects Nordic')
        return True


class DconfBackend:
    """libdconf synchronous subscription; bounded dconf CLI reads/writes.

    libdconf and GLib are OS libraries, not additional Python dependencies.
    dconf watch CLI has no ready acknowledgement and cannot close the initial
    subscribe/read race; dconf_client_watch_sync can.
    """
    def __init__(self):
        self.client = None
        self.dirty = True
        self.revision = 0
        self.own_tags = []
        self.dconf = self.glib = self.gobject = None
        self.callback = None
        self.handler = None

    def changed(self, client, prefix, changes, tag, user_data):
        self.dirty = True
        if tag in self.own_tags:
            self.own_tags.remove(tag)
        else:
            self.revision += 1

    def subscribe(self):
        if self.client is not None: return
        self.dconf = ctypes.CDLL('libdconf.so.1')
        self.glib = ctypes.CDLL('libglib-2.0.so.0')
        self.gobject = ctypes.CDLL('libgobject-2.0.so.0')
        self.dconf.dconf_client_new.restype = ctypes.c_void_p
        self.dconf.dconf_client_write_sync.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p)]
        self.dconf.dconf_client_write_sync.restype = ctypes.c_int
        self.glib.g_variant_new_string.argtypes = [ctypes.c_char_p]
        self.glib.g_variant_new_string.restype = ctypes.c_void_p
        self.glib.g_variant_ref_sink.argtypes = [ctypes.c_void_p]
        self.glib.g_variant_ref_sink.restype = ctypes.c_void_p
        self.glib.g_variant_unref.argtypes = [ctypes.c_void_p]
        self.glib.g_free.argtypes = [ctypes.c_void_p]
        self.glib.g_error_free.argtypes = [ctypes.c_void_p]
        self.dconf.dconf_client_watch_sync.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.dconf.dconf_client_unwatch_sync.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.glib.g_main_context_iteration.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.glib.g_main_context_iteration.restype = ctypes.c_int
        self.gobject.g_object_unref.argtypes = [ctypes.c_void_p]
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p,
                                       ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p)
        self.callback = callback_type(self.changed)
        self.gobject.g_signal_connect_data.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
            callback_type, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        self.gobject.g_signal_connect_data.restype = ctypes.c_ulong
        self.gobject.g_signal_handler_disconnect.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.client = self.dconf.dconf_client_new()
        if not self.client: raise UnsafeState('cannot create dconf client')
        self.handler = self.gobject.g_signal_connect_data(self.client, b'changed', self.callback,
                                                        None, None, 0)
        # This returns only after the D-Bus subscription is established.
        self.dconf.dconf_client_watch_sync(self.client, KEY.encode('ascii'))

    def pump(self):
        if self.glib is not None:
            for _ in range(64):
                if not self.glib.g_main_context_iteration(None, False): break
        dirty, self.dirty = self.dirty, False
        return dirty

    def read(self):
        text = run_text(['/usr/bin/dconf', 'read', KEY])
        if not text: return None
        try: value = ast.literal_eval(text)
        except (ValueError, SyntaxError): raise UnsafeState('dconf layout is not a string')
        if not isinstance(value, str) or len(value) > 128:
            raise UnsafeState('invalid dconf layout value')
        return value

    def write(self, value):
        if value != LAYOUT and not generic_layout(value): raise ValueError('unsafe layout')
        if self.client is None: raise UnsafeState('dconf subscription is not ready')
        assert self.glib is not None and self.dconf is not None
        variant = self.glib.g_variant_ref_sink(self.glib.g_variant_new_string(value.encode('ascii')))
        tag, error = ctypes.c_char_p(), ctypes.c_void_p()
        try:
            if not self.dconf.dconf_client_write_sync(self.client, KEY.encode('ascii'), variant,
                    ctypes.byref(tag), None, ctypes.byref(error)):
                raise UnsafeState('synchronous dconf write failed')
            if tag.value:
                self.own_tags.append(tag.value)
                self.own_tags = self.own_tags[-16:]
        finally:
            if tag: self.glib.g_free(tag)
            if error: self.glib.g_error_free(error)
            self.glib.g_variant_unref(variant)

    def close(self):
        if self.client is not None:
            self.dconf.dconf_client_unwatch_sync(self.client, KEY.encode('ascii'))
            self.gobject.g_signal_handler_disconnect(self.client, self.handler)
            self.gobject.g_object_unref(self.client)
            self.client = None


class SessionBridge:
    def __init__(self, backend, store, connector=connect_control):
        self.backend, self.store, self.connector = backend, store, connector
        self.state = LayoutState(backend, store.load())
        pending = store.load_resume()
        self.resume = pending if generic_layout(pending) else None
        self.revision = getattr(backend, 'revision', 0)
        self.sock = None
        self.enabled = False
        self.removing = False
        self.remove_requested = False
        self.error = None

    def start(self):
        self.backend.subscribe()
        value = self.backend.read()
        if generic_layout(value) and value != self.resume:
            self.state.last_generic = value
            self.store.save(value)
            self.resume = None

    def clear_resume(self):
        if self.resume is not None:
            self.store.save(self.state.last_generic)
            self.resume = None

    def fallback(self, remember=True):
        try:
            if remember and not self.removing and self.backend.read() == LAYOUT:
                self.store.save(self.state.last_generic, resume=True)
                self.resume = self.state.last_generic
                self.revision = getattr(self.backend, 'revision', 0)
            elif not remember:
                self.clear_resume()
        finally:
            self.state.fallback()

    def exchange(self, command):
        send_packet(self.sock, {'command': command})
        for _ in range(4):
            result = receive_packet(self.sock)
            if result.get('event') == 'prepare-remove':
                self.remove_requested = True
                continue
            if not result.get('ok'): raise UnsafeState(result.get('error', 'control request failed'))
            return result
        raise UnsafeState('too many asynchronous events')

    def disconnect(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        self.enabled = False

    def prepare(self):
        self.removing = True
        self.fallback(remember=False)
        self.exchange('prepare-remove')
        self.enabled = False
        self.remove_requested = False

    def cycle(self):
        try:
            # Always re-read current state. Notifications are invalidations, not values.
            value = self.backend.read()
            if self.resume is not None and (value != self.resume or
                    getattr(self.backend, 'revision', 0) != self.revision or
                    self.store.load_resume() != self.resume):
                self.clear_resume()
            if self.sock is None:
                self.sock = self.connector()
                self.exchange('status')
            while select.select([self.sock], [], [], 0)[0]:
                event = receive_packet(self.sock)
                if event.get('event') != 'prepare-remove': raise UnsafeState('unexpected control packet')
                self.remove_requested = True
            if self.remove_requested:
                self.prepare()
                value = self.backend.read()
            if self.removing and value == LAYOUT:
                self.state.fallback()
                value = self.backend.read()
            if self.resume is not None and value == self.resume and not self.removing:
                result = self.exchange('enable')
                if not result.get('enabled'): raise UnsafeState('resume lease is not enabled')
                self.enabled = True
                # The hardware must be ready before restoring the user's symbol layout.
                self.backend.pump()
                if (self.backend.read() != value or
                        getattr(self.backend, 'revision', 0) != self.revision or
                        self.store.load_resume() != self.resume):
                    self.exchange('disable')
                    self.enabled = False
                    self.clear_resume()
                    value = self.backend.read()
                else:
                    self.backend.write(LAYOUT)
                    if self.backend.read() != LAYOUT: raise UnsafeState('resume layout readback failed')
                    self.clear_resume()
                    value = LAYOUT
            if value == LAYOUT:
                result = self.exchange('heartbeat' if self.enabled else 'enable')
                if not result.get('enabled'): raise UnsafeState('lease is not enabled')
                self.enabled = True
            else:
                self.exchange('disable' if self.enabled else 'status')
                self.enabled = False
                if generic_layout(value) and value != self.state.last_generic:
                    self.store.save(value)
                    self.state.last_generic = value
            if self.remove_requested: self.prepare()
            self.error = None
        except Exception as error:
            self.error = str(error)
            self.disconnect()
            # Do not leave the XKB choice expecting an unavailable modifier map.
            try: self.fallback()
            except Exception as fallback_error: self.error += '; fallback: ' + str(fallback_error)
        return {'ok': self.error is None, 'enabled': self.enabled,
                'removing': self.removing, 'error': self.error}

    def close(self):
        try: self.fallback(remember=not self.removing)
        finally: self.disconnect()


def watch(backend, store):
    fd = os.open('agent.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=store.dirfd)
    bridge = SessionBridge(backend, store)
    stopping = [False]
    handlers = {}
    started = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError('unsafe agent lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, lambda *_: stopping.__setitem__(0, True))
        started = True
        bridge.start()
        sd_notify('READY=1')
        last_cycle = 0
        last_error = None
        while not stopping[0]:
            dirty = backend.pump()
            readable = bridge.sock is not None and bool(select.select([bridge.sock], [], [], 0)[0])
            if dirty or readable or time.monotonic() - last_cycle >= 1:
                result = bridge.cycle()
                if result['error'] != last_error:
                    print(json.dumps(result, sort_keys=True), flush=True)
                    last_error = result['error']
                last_cycle = time.monotonic()
                sd_notify('WATCHDOG=1')
            time.sleep(0.1)
        sd_notify('STOPPING=1')
        return {'ok': True, 'stopped': True}
    finally:
        try:
            if started: bridge.close()
        finally:
            for sig, handler in handlers.items(): signal.signal(sig, handler)
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='watch', choices=('watch', 'prepare-remove'))
    args = parser.parse_args(argv)
    backend = store = None
    try:
        store = StateStore()
        backend = DconfBackend()
        if args.command == 'prepare-remove':
            # Convenience user-side fallback only; root coordinated command remains mandatory.
            backend.subscribe()
            store.save(store.load())
            LayoutState(backend, store.load()).fallback()
            result = {'ok': True, 'layout_safe': True, 'safe_to_remove': False,
                      'note': 'run root service_runtime.py prepare-remove for map confirmation'}
        else:
            result = watch(backend, store)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)[:400], 'safe_to_remove': False},
                         sort_keys=True), flush=True)
        return 1
    finally:
        if backend is not None: backend.close()
        if store is not None: store.close()


if __name__ == '__main__': sys.exit(main())
