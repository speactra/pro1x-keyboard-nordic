# Runtime design and packaging interface

## Scope and readiness

The runtime uses Python 3.8+ and the Linux input ABI, with no additional Python
packages. It targets the QX1050 community keyboard driver. The root runtime
imports the existing `pro1x_nordic.py`, which remains the authority for normal
transaction order and rollback. Layout registration, XKB symbols and RPM units
are separate packaging responsibilities.

Dependencies: `/usr/bin/python3`, `/usr/bin/loginctl`, `/usr/bin/dconf`, systemd /
logind, user D-Bus, and OS shared libraries `libdconf.so.1`,
`libglib-2.0.so.0`, `libgobject-2.0.so.0`. Declare the phone distribution's packages
providing these, rather than adding pip dependencies.

## Stable commands

Install all four modules together, root-owned and not user-writable:

```
/usr/lib/pro1x-keyboard-nordic/pro1x_nordic.py
/usr/lib/pro1x-keyboard-nordic/service_runtime.py
/usr/lib/pro1x-keyboard-nordic/session_agent.py
/usr/lib/pro1x-keyboard-nordic/remove_package.py
```

Root/system unit `pro1x-keyboard-nordic.service`:

```
ExecStart=/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py daemon
ExecStopPost=/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py recover
```

Use `Type=notify`, `NotifyAccess=main`, `WatchdogSec=20`,
`RuntimeDirectory=pro1x-keyboard-nordic`, `RuntimeDirectoryMode=0755`,
`TimeoutStartSec=20`, `TimeoutStopSec=20`, `Restart=on-failure`, `RestartSec=2`.
Order after/want `systemd-logind.service`. Do not use `PrivateDevices=yes` or hide
`/sys/class/input`, `/sys/bus/i2c`, `/dev/input`, or the system D-Bus. Do not prefix
ExecStopPost with `-`: failure to restore must be visible. The independent recover
process acquires the same exclusive lock as the daemon; calling it while the
daemon is alive fails instead of racing it. The daemon also performs bounded
cleanup on SIGTERM/SIGINT before releasing its lock.

User unit `pro1x-keyboard-nordic-session.service`:

```
ExecStart=/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/session_agent.py watch
```

No arguments means `watch` too. Use `Type=notify`, `NotifyAccess=main`,
`WatchdogSec=20`, `TimeoutStartSec=20`, `TimeoutStopSec=15`, `Restart=on-failure`,
`RestartSec=2`. Require/order after **user** `dbus.socket`, order before
`user-session.target`, and install into `user-session.target` (optionally
`PartOf=user-session.target` for stop propagation). Use the actual user manager's
D-Bus environment; do not invent a bus address, UID or home directory. Do not
attach this to `graphical-session.target`: that target is inactive on the tested
Sailfish system. The agent need not require `lipstick.service`, which would risk
an ordering cycle with pre-user-session startup.

Manual root operations:

```
/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py status
/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py prepare-remove
# Only with the daemon stopped (e.g. ExecStopPost):
/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py recover
```

Optional **user-session** helper:

```
/usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/session_agent.py prepare-remove
```

This helper confirms only a dconf fallback, not root map recovery; its JSON
explicitly has `safe_to_remove:false`. It is not a substitute for the root
coordinated command.

Commands print JSON to stdout, exit 0 on success, 1 on operational failure
(argparse usage errors exit 2). Watch logs changed errors as JSON; it does not log
input keys, secrets or arbitrary dconf data. Root status is read-only and returns
for example these fields (values below describe the schema, not a phone result):

* `ok`: boolean, `error`: null or string
* `enabled`: whether an outstanding lease owns Nordic mode
* `lease_seconds`: 6.0
* `removing`: boolean, latched until daemon restart after prepare-remove begins
* `device_present`: whether device validation and map read succeeded
* `map`: six observed integer keycodes, or null before discovery/on failure

`enabled` is not a promise about the compositor's asynchronously loaded XKB state.
Offline status fails with `ok:false,safe_to_remove:false`; it never silently
recovers or starts a daemon. Recover success includes
`ok:true,enabled:false,map:[56,54,29,125,464,464]`. Coordinated removal success
adds `safe_to_remove:true` to the daemon status and follows original-map readback.

## Root device and session boundary

The socket is fixed at `/run/pro1x-keyboard-nordic/control.sock`. The directory is
root-owned 0755 and the socket root-owned 0666: access is authorized by
`SO_PEERCRED`, not membership in an input group. User clients independently require
a server peer UID of 0. A root-only lock in the same directory prevents competing
package daemon/recover processes. No UID or home is hardcoded.

The controller asks `loginctl show-seat seat0 -p ActiveSession`, then reads that
session's `User`, `Active`, `Remote`, `Seat`, `Class`. Only a positive UID with
`Active=yes`, `Remote=no`, `Seat=seat0`, `Class=user` is allowed. It deliberately
**does not reject `Type=tty`**: Sailfish graphics lives on tty7. Leases bind to both
the UID and session identifier. Missing/failed logind discovery is not authority
to enable. Root may request status and coordinated removal, but cannot use the
user enable/heartbeat interface to bypass session authentication.

Device discovery enumerates `/sys/class/input/event*`; exactly one verified
candidate must exist. Validation requires all of:

* exact input name `Fxtec Pro1`;
* bus/vendor/product/version `0018:9523:0701:0001` in sysfs and `EVIOCGID`;
* an ancestor's driver symlink resolving to `/sys/bus/i2c/drivers/aw9523b`;
* `O_NOFOLLOW` open of `/dev/input/eventN`, a character node, node inode/device
  consistency with the opened descriptor, and rdev matching sysfs `dev`;
* unchanged sysfs realpath plus repeat `EVIOCGNAME`/`EVIOCGID` checks.

Checks recur while idle and leased. Replaced/vanished devices revoke the old
lease; a rediscovered device is reconciled to original, never automatically
re-enabled under the old lease. Only the six by-index modifier entries are read;
only indices 1, 4, 5 can be written, and only their original/Nordic constants.
`GET_KEYCODE=0x80284504`, `SET_KEYCODE=0x40284504`, structure `=BBHI32s` (40 bytes),
by-index flag 1. There are no input-event reads, grabs, streaming, subprocess
paths from clients, or caller-selected keycodes.

## Maps, held keys, recovery and leases

Original is `(56,54,29,125,464,464)`; Nordic is `(56,42,29,125,100,54)`.
Normal transitions use the imported transactional primitive. Recovery accepts
**only** these two plus the exact intermediate states reachable in the package's
ordered operations and rollback:

```
(56,54,29,125,464,54)
(56,42,29,125,464,54)
(56,54,29,125,100,54)
```

No cartesian product of possible key values is accepted. Intermediate recovery
writes original indices in order 1,4,5 and checks readback; restoring rightShift
54 at index 1 precedes removing its temporary index-5 copy. Unknown maps are never
overwritten. Failed ordinary transitions use the primitive's rollback; failed
recovery is retried only while it remains one of the enumerated owned states.

Before actual changes, `EVIOCGKEY` samples the held-key bitmap. Any reported held
key prevents a transition. It is not an event stream. Once keys are released,
normal ticks retry restoration. Independent recovery retries for at most five
seconds, then exits nonzero. A lease is six monotonic seconds; the user agent
checks selection/renews about once per second, with immediate checks on dconf
invalidation/control socket readability. A late heartbeat cannot resurrect an
expired lease. Disconnect, active-session change, expiry or identity failure
revokes/restores the fixed map. Probe failures drop connections, causing a live
agent to fall back. No device is an error, not a successful recovery.

The daemon checks on a nominal 0.5-second cadence and handles at most one client
packet between tick opportunities. Loginctl/dconf subprocesses have two-second
timeouts; socket operations are bounded. This is not a hard real-time guarantee:
blocking kernel ioctls, scheduler stalls, held keys and logind probe time can delay
restoration. Systemd's watchdog covers a stuck main loop, and ExecStopPost provides
independent cleanup after forced termination. Held-key safety wins over forcing a
map write at the lease deadline.

**Important hardware limit:** physical Fn is consumed internally by this driver.
`EVIOCGKEY` cannot prove Fn is released, nor prevent a physical key press racing
a bitmap sample. Zero-key-event-streaming plus this driver cannot provide a
perfect all-physical-keys-up barrier. On-device testing and potentially a driver
change are needed for a stronger guarantee.

## Wire protocol

Linux `AF_UNIX/SOCK_SEQPACKET` (not a newline stream). Each request is a single
ASCII JSON packet, max 256 bytes, with **exactly** one unique key:

```
{"command":"enable"}
{"command":"disable"}
{"command":"heartbeat"}
{"command":"status"}
{"command":"prepare-remove"}
```

Duplicate/extra keys, unsupported commands, oversized or malformed packets are
rejected and disconnected. Replies/events are bounded to 4096 bytes. There are at
most 16 clients, 12-second idle expiry, one lease owner. Non-owner renewal/disable
is rejected. Root status does not register an agent. An authenticated user request
registers a session-agent channel, kept live even while a generic layout is
selected. Treat `{"event":"prepare-remove"}` as an asynchronous request to
conditionally fall back, read back, then acknowledge with `prepare-remove` on
that **same** authenticated connection. It is not a successful operation reply.

## dconf and fallback

Only `/desktop/lipstick-jolla-home/layout` is watched/read/written. There is one
layout identifier, `pro1x_nordic`, selected through the separately packaged Jolla
Settings registry entry. No separate toggle is needed.

The watcher uses stdlib ctypes to call `dconf_client_watch_sync` and connects the
`changed` signal **before** the first read. This closes the initial subscription
race; `dconf watch` CLI has no readiness acknowledgement, so merely starting that
process would not suffice. GLib events are pumped without blocking, bounded per
iteration. Notifications are invalidations only; values are re-read with bounded
`dconf read` subprocesses. A periodic read also covers lost/coalesced notifications.
Writes use `dconf_client_write_sync` and GLib string variants, followed by
readback. Native change tags distinguish own writes from external notifications;
an external change increments a revision counter, including notifications whose
current value matches the fallback. The existing service watchdog also covers
native synchronous calls; unlike dconf-read subprocesses they are not individual
two-second child processes.

The last observed generic selection is saved only in
`$XDG_CONFIG_HOME/pro1x-keyboard-nordic/fallback.json` (default
`~/.config/pro1x-keyboard-nordic/fallback.json`); missing/invalid state defaults to
`us`. The same private directory holds a singleton agent lock. Symlink/foreign
owner state targets are refused; no root process writes user configuration. A
stored generic layout must remain installed; the registry is not independently
validated by this module. State stores a bounded identifier and a boolean pending
Nordic-resume marker, never key data. Legacy state without the marker remains a
generic fallback only. No new state file/service is introduced.

Before a safety fallback on error or orderly exit, the watcher records the
pending choice. On restart it resumes only while the raw layout still matches
that fallback, no external layout notification has intervened, and the marker
still exists. It first obtains an authenticated enable/readback from root, then
pumps/rechecks dconf and the marker, selects Nordic and clears the marker. A
notified generic selection, different raw value or explicit marker clear cancels
resume. Removal clears the marker before acknowledgement and close cannot
re-arm it while removing. Repeated same-value writes can emit no notification;
an offline same-value choice cannot be distinguished from unchanged fallback.
Use the user's prepare-remove command to explicitly cancel that ambiguous case.

On disconnect, enable failure, or orderly exit the agent re-reads the current
selection immediately before fallback. It writes only if the current value is
still `pro1x_nordic`, and confirms it no longer is. A newly observed generic
selection is left alone and saved. The compositor, dconf and evdev map are
separate asynchronous systems: **there is no atomic XKB/map commit and no dconf
compare-and-swap**. Another writer can race the last read and fallback write.
The implementation minimizes that window; it cannot mathematically guarantee
never overwriting a selection made inside it. A killed/hung user agent cannot
itself perform fallback until restarted. Do not describe this as atomic or as a
complete held-Fn safety proof.

## Erase/upgrade protocol (must abort on failure)

1. While the old daemon, user agent, registry and symbols still exist, run the
   **root** `service_runtime.py prepare-remove` command above. Do not stop the user
   agent before this handshake.
2. The daemon latches removing mode (rejects future enables), requires an active
   local session and a live authenticated agent, and sends `prepare-remove`.
3. The agent conditionally falls back/readbacks, then acknowledges on its existing
   channel. The daemon rechecks the same active session, discovers/validates the
   device if necessary, restores original, and replies `safe_to_remove:true`.
   Confirmation times out in five seconds; the CLI waits up to eight. Missing
   agent, inactive session, unknown map, held keys, failed dconf or failed readback
   is a nonzero failure, never presumed safe.
4. `remove_package.py` obtains the active UID via logind, rechecks the session
   after the handshake, disables/stops the user unit with dropped UID/GID/groups,
   and runs the user fallback/readback once more after the watcher exits. Then
   it disables/stops root and runs independent locked GPIO recovery. Every child
   command has a 45-second timeout; failure aborts pre-uninstall. The user's home
   and primary GID come from passwd; the target-verified Sailfish bus is
   `/run/user/UID/dbus/user_bus_socket`.
5. RPM in-place upgrades are rejected in `%pre`. Manually erase, reinstall and
   explicitly activate again. There is no previous-state restore automation.
   Even never-activated packages require starting both services for removal;
   no selection of Nordic or automatic enablement is needed.

There is deliberately **no offline-uninstall bypass**. Without a live agent there
is no safe way here for root to confirm that user's dconf selection. A failed
attempt can leave removing mode latched: fix the reported condition and retry,
or restart both services if abandoning removal. The handshake acknowledges the
active session only, not every inactive user's historical configuration. Stale
Nordic selections in other profiles and another actor selecting Nordic after
acknowledgement are genuine limitations of this active-session design; package
policy must not promise all-profile atomic removal. Keeping the agent alive in
removing mode until immediately before file removal reduces, not eliminates,
selection races.

## Local verification and remaining acceptance

Run from the repository:

```
python3 -m unittest discover -s tests -p test_services.py -v
python3 -m unittest discover -s tests -v
```

Tests use fake map devices and injected OS boundaries; real local Unix seqpacket
sockets exercise enable/disconnect, the session bridge's full select/enable/disable
path, and removal event/ack ordering. The latest local run passed 30 service tests
and 65 tests across the complete repository suite, including mocked removal
ordering, privilege dropping and fail-closed paths. The upgrade rejection and
fresh-install precondition are executed with a real shell. Python 3.8 grammar
parsing covers the runtime sources; the executing interpreter was Python 3.14.
They cover
UID/session authorization (including tty acceptance and remote rejection), leases,
late renewals, held-key deferral, session loss, recreation, write failure rollback,
enumerated recovery/refusal, node/identity checks, bounded protocol, readback,
fallback persistence/defaults, subscribe-before-read, conditional fallback and JSON
CLI errors. No tests open a real evdev device or require root.

A private `dbus-run-session` test with isolated XDG directories exercised real
libdconf reads/writes, own-tag suppression, external change detection, a real
local control socket and persisted recovery across watcher restart. The test
environment used an extracted dconf-service and did not change normal user
configuration. Ordinary automated tests use simulated layout and map devices.

The runtime was subsequently verified on QX1050/Sailfish OS 5.1.0.11 with
systemd 238 and Python 3.11.15. Nordic selection, an active authenticated lease,
the expected modifier table and both enabled/running services returned after a
full reboot without corrective writes. Physical keys worked after that reboot,
and RPM verification reported no differences. Earlier lifecycle tests also
covered layout fallback, removal and reinstallation.

Package revision 0.1.0-0.2 changes documentation and release metadata only. Its
Python modules, registration, units and lifecycle scriptlets are identical to the
device-tested 0.1.0-0.1 package. XKB definitions are unchanged apart from a comment
cleanup; the new package transaction itself has not been repeated on the
device. Python 3.8 grammar parsing is not Python 3.8 execution. Repeated
suspend/resume, other kernels and multiple-account scenarios remain follow-up
validation areas. See README for compatibility limits and update procedure.
