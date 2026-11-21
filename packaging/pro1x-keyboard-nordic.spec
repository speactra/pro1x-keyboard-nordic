# Additive physical keyboard layout; keep RPM v4 compatibility for Sailfish.
%global _rpmformat 4
%global _binary_payload w9.gzdio
%global _source_payload w9.gzdio
%global _binary_filedigest_algorithm 8
%global _source_filedigest_algorithm 8
%global __os_install_post %{nil}
%global debug_package %{nil}

Name:           pro1x-keyboard-nordic
Version:        0.1.0
Release:        0.2
Summary:        Pro1 X Nordic physical keyboard layout for Sailfish OS
License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
AutoReqProv:    no
BuildRequires:  python3-base
BuildRequires:  libxkbcommon
BuildRequires:  xkeyboard-config
Requires:       python3-base >= 3.8
Requires:       dconf
Requires:       glib2
Requires:       systemd >= 238
Requires:       xkeyboard-config
Requires:       jolla-keyboard
Requires(pre):  python3-base
Requires(pre):  systemd
Requires(post): systemd
Requires(preun): python3-base
Requires(preun): systemd
Requires(postun): systemd

%description
A separate physical keyboard layout matching the Nordic F(x)tec Pro1 X key
legends. Right yellow arrow is AltGr. Accent keys emit direct symbols.
Includes a restricted modifier coordinator and per-session layout watcher.
Does not replace xkeyboard-config, remap other input devices or require a
custom kernel. Supports the verified QX1050 aw9523b modifier map.
Services and layout selection are opt-in; see README before activation.

%prep
%setup -q

%build
# Python and XKB data only; no target-architecture compilation.

%install
install -d %{buildroot}/usr/lib/pro1x-keyboard-nordic
install -m 0644 src/pro1x_nordic.py src/service_runtime.py src/session_agent.py src/remove_package.py \
    %{buildroot}/usr/lib/pro1x-keyboard-nordic/
install -D -m 0644 src/xkb/symbols/pro1x_nordic \
    %{buildroot}/usr/share/X11/xkb/symbols/pro1x_nordic
install -D -m 0644 packaging/pro1x_nordic.conf \
    %{buildroot}/usr/share/maliit/plugins/com/jolla/hwkbdlayouts/pro1x_nordic.conf
install -D -m 0644 packaging/pro1x-keyboard-nordic.service \
    %{buildroot}/usr/lib/systemd/system/pro1x-keyboard-nordic.service
install -D -m 0644 packaging/pro1x-keyboard-nordic-session.service \
    %{buildroot}/usr/lib/systemd/user/pro1x-keyboard-nordic-session.service

%check
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 tests/check_layout.py

%pre
# Deliberately no in-place upgrade lifecycle; erase safely, then install.
if [ "$1" -gt 1 ]; then
    printf '%s\n' 'In-place upgrade unsupported: erase the old package first; see README.' >&2
    exit 1
fi

%post
# Do not auto-enable, auto-start, select a layout or edit user dconf here.
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || exit 1
fi

%preun
if [ "$1" -eq 0 ]; then
    /usr/bin/python3 /usr/lib/pro1x-keyboard-nordic/remove_package.py || exit 1
fi

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || exit 1
fi

%files
%license LICENSE
%doc README.md docs/runtime-design.md
%dir /usr/lib/pro1x-keyboard-nordic
/usr/lib/pro1x-keyboard-nordic/pro1x_nordic.py
/usr/lib/pro1x-keyboard-nordic/service_runtime.py
/usr/lib/pro1x-keyboard-nordic/session_agent.py
/usr/lib/pro1x-keyboard-nordic/remove_package.py
/usr/share/X11/xkb/symbols/pro1x_nordic
/usr/share/maliit/plugins/com/jolla/hwkbdlayouts/pro1x_nordic.conf
/usr/lib/systemd/system/pro1x-keyboard-nordic.service
/usr/lib/systemd/user/pro1x-keyboard-nordic-session.service

%changelog
* Sat Nov 21 2026 Pro1 X Nordic contributors - 0.1.0-0.2
- Refresh documentation and release metadata; runtime payload unchanged.
* Sat Nov 21 2026 Pro1 X Nordic contributors - 0.1.0-0.1
- Initial experimental, additive Nordic physical layout and recovery services.
