# Pro1 X Nordic

Nordic physical-keyboard layout for the **F(x)tec Pro1 X (QX1050)** running
Sailfish OS. It matches the keyboard's printed legends and appears as a separate
**Pro1 X Nordic** entry in the physical-keyboard settings.

The package adds its own layout, registration and services. It does not replace
`xkeyboard-config`, modify the kernel or patch existing Sailfish program files.

## Status and compatibility

Verified on a QX1050 with Sailfish OS **5.1.0.11**, systemd **238**,
Python **3.11.15** and the community `aw9523b` keyboard driver:

- Settings registration and physical key behaviour, including direct accents.
- Layout switching, modifier-map activation and recovery.
- Installation, removal and reinstallation during development.
- Automatic Nordic recovery after a full reboot, followed by a physical-key test.

The test suite contains **65 automated tests** and **59 XKB symbol checks**.
Python 3.8 syntax compatibility is checked, but Python 3.8 execution has not been
verified. Other device variants, kernels and Sailfish releases need separate
validation. This is a device-specific project, not a general keyboard framework.

RPM **0.1.0-0.2** is a documentation and release-metadata revision of the tested
0.1.0-0.1 package. Runtime modules, layout registration, service units and lifecycle
scriptlets are unchanged. The XKB file has comment-only cleanup; its layout
definitions are unchanged. The new RPM is built and inspected
locally; a new device installation is not needed merely for this documentation
revision, and its exact package transaction has not been repeated on the device.

## Key behaviour

- Normal letters and Shift: `å/Å`, `ä/Ä`, `ö/Ö`.
- Right yellow arrow is AltGr: W → `@`, Q → `§`, E → `$`, R → `°`.
- AltGr U/I/O/P → `{ [ ] }`; Å → backslash; K → `*`; L → apostrophe.
- AltGr Z/N → `| ~`; comma/period/minus → `; : _`.
- Yellow number-row symbols are available with Shift or right AltGr; 4 gives `€`.
- Accent keys emit acute, grave, diaeresis and circumflex directly. They are not
  dead keys and do not compose an accent with the next letter.
- The left yellow arrow provides an additional technical symbol level. Logo,
  Sym and globe actions are unchanged.

## Why there are runtime services

The driver emits a synthetic right-Shift event for the physical ö key and handles
the yellow-arrow modifiers internally. A symbol file alone cannot distinguish
that synthetic event from physical Shift. An ordered, device-specific modifier
remap is therefore required in addition to the XKB symbols.

The root coordinator validates the exact device and applies the restricted
modifier map. The unprivileged session watcher follows the selected layout and
maintains an authenticated, expiring lease with the coordinator. Switching away
from Nordic restores the original map. No keystrokes are streamed, logged or
grabbed by these services.

Only the verified `Fxtec Pro1` device with I²C identifiers
`0018:9523:0701:0001` and driver `aw9523b` can be modified. Unknown maps are
rejected. GPIO changes apply only to that device, but the selected physical XKB
layout applies to the user session, including any external keyboard.

## Installation and activation

Keep the on-screen keyboard, SSH access and a known-good generic physical layout
available. Release physical keys when changing layouts. Installation does not
automatically start services or select Nordic.

1. Install the release RPM as root:

   ```sh
   rpm -ivh pro1x-keyboard-nordic-0.1.0-0.2.noarch.rpm
   systemctl enable --now pro1x-keyboard-nordic.service
   ```

2. In the normal graphical user's terminal, outside the root shell:

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now pro1x-keyboard-nordic-session.service
   ```

3. In Sailfish's **physical-keyboard** settings, enable **Pro1 X Nordic** in the
   layout list, then select it as the active layout. This is separate from the
   virtual-keyboard language settings.

4. Test the physical keys, switch to a generic layout and back, and verify the
   selected layout after reboot.

The Sailfish user bus is `/run/user/UID/dbus/user_bus_socket`. Use the graphical
session's environment for user-service and dconf commands, not root's session.

## Recovery and retained selection

On connection failure or orderly watcher exit, the watcher falls back to a
generic layout. The existing private `fallback.json` also records a pending
Nordic selection. After restart or reconnection, the watcher authenticates with
the coordinator and enables/verifies the modifier map before selecting Nordic
again. This preserves the selection across reboot without another service.

A detected manual change to a generic layout cancels pending recovery. Own dconf
writes are distinguished by their native change tags. Removal preparation clears
the pending selection before acknowledging fallback and cannot re-arm it on exit.

A repeated selection of the already active fallback may emit no change
notification. The same value written while the watcher is stopped is also
indistinguishable from unchanged fallback. To explicitly cancel pending recovery,
run this as the normal graphical user:

```sh
python3 /usr/lib/pro1x-keyboard-nordic/session_agent.py prepare-remove
```

This clears the pending selection and confirms user-layout fallback; it is not a
substitute for the root removal handshake.

For independent modifier-map recovery, select a generic layout and stop the
coordinator first. Then, as root:

```sh
systemctl stop pro1x-keyboard-nordic.service
python3 /usr/lib/pro1x-keyboard-nordic/service_runtime.py recover
```

The system service also runs recovery when it stops. Recovering the modifier map
alone is insufficient if Nordic remains selected in the compositor.

## Removal and manual updates

In-place RPM upgrades are intentionally rejected before payload replacement.
For an update: **remove the old package, install the new RPM, then explicitly
activate it again**. Previous enablement and layout selection are not restored
by RPM scriptlets.

Before removal, keep the local graphical session active, release physical keys
and select a generic physical layout such as `se`. Do not change layouts or
sessions during the transaction. Select a generic layout in other accounts too;
the package does not rewrite inactive users' dconf databases.

Both services must be running for authenticated fallback confirmation, even if
the package has never been enabled or used. If necessary, start the root service
with `systemctl start pro1x-keyboard-nordic.service`, then run the following in
the normal graphical user's terminal:

```sh
systemctl --user daemon-reload
systemctl --user start pro1x-keyboard-nordic-session.service
```

Do not select Nordic merely to prepare removal. As root:

```sh
rpm -e pro1x-keyboard-nordic
```

The removal helper confirms user fallback, disables/stops the user watcher,
checks fallback again, disables/stops the coordinator and independently verifies
modifier recovery before allowing payload removal. Missing session, failed
fallback, unknown device/map, timeout or command failure aborts removal.

A partial failure may leave services stopped or disabled. Keep the generic
layout, correct the reported condition, restart root and then user services, and
retry. If abandoning removal, explicitly re-enable the services as in the initial
setup. Do not bypass lifecycle checks with `--noscripts`. Private fallback state
is intentionally retained after removal, with pending recovery cleared.

## Source layout and development

- `src/`: runtime modules and XKB symbols.
- `packaging/`: RPM specification, service units and layout registration.
- `tests/`: runtime, lifecycle, artifact-parser and symbol checks.
- `tools/`: RPM build/inspection and read-only target-library checks.
- `docs/runtime-design.md`: interfaces, authentication and recovery details.

Run from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 tests/check_layout.py
```

Symbol tests need `libxkbcommon` and system XKB data. `XKB_CONFIG_ROOT` can point
to another read-only XKB data tree. Target dependencies include `python3-base`,
`dconf`, `glib2`, systemd/logind, `xkeyboard-config` and `jolla-keyboard`.

With RPM build tools available:

```sh
python3 tools/build.py
python3 tools/check_artifacts.py dist/noarch/pro1x-keyboard-nordic-0.1.0-0.2.noarch.rpm
```

For an extracted toolchain, set `RPMBUILD`, `RPM_CONFIGDIR`, `LD_LIBRARY_PATH` and
`PATH` as required; the inspector accepts `--rpm-command` and `--rpm2cpio`.
The build emits the binary RPM, SRPM, source archive and `SHA256SUMS` under
`dist/`. Generated build files and release assets are ignored by Git. Upload
release assets to GitHub Releases rather than committing them to source history.
Packages are unsigned; checksums detect changed files but are not signatures.

## Limitations and possible future work

No additional implementation is required for the currently tested device and
workflow. Future updates should focus on concrete compatibility or reliability
needs:

- Revalidate after Sailfish, kernel or driver updates, especially device identity,
  modifier tables, logind session detection and the user D-Bus path.
- Expand on-device tests for repeated suspend/resume, forced process termination,
  device recreation and multiple local accounts. Existing automated tests cover
  many failure paths, but do not replace those hardware/runtime tests.
- Layout selection, dconf and GPIO changes are asynchronous, not an atomic
  compositor-integrated operation. Concurrent writes can race a readback.
- The driver consumes Fn internally, so `EVIOCGKEY` cannot prove every physical
  modifier is released. Release keys before switching layouts; stronger
  guarantees may require driver changes.
- Revisit same-value fallback cancellation only if it becomes a practical issue;
  the explicit cancellation command remains the unambiguous path.
- Automatic in-place upgrades, wider device support and package signing are
  optional future work, not implemented features.

## License

MIT. See [LICENSE](LICENSE).
