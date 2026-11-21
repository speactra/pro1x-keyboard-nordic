#!/usr/bin/env python3
"""Inspect actual RPM header, dependencies, script syntax and unpacked payload."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    'usr/lib/pro1x-keyboard-nordic/pro1x_nordic.py': 'src/pro1x_nordic.py',
    'usr/lib/pro1x-keyboard-nordic/service_runtime.py': 'src/service_runtime.py',
    'usr/lib/pro1x-keyboard-nordic/session_agent.py': 'src/session_agent.py',
    'usr/lib/pro1x-keyboard-nordic/remove_package.py': 'src/remove_package.py',
    'usr/share/X11/xkb/symbols/pro1x_nordic': 'src/xkb/symbols/pro1x_nordic',
    'usr/share/maliit/plugins/com/jolla/hwkbdlayouts/pro1x_nordic.conf': 'packaging/pro1x_nordic.conf',
    'usr/lib/systemd/system/pro1x-keyboard-nordic.service': 'packaging/pro1x-keyboard-nordic.service',
    'usr/lib/systemd/user/pro1x-keyboard-nordic-session.service': 'packaging/pro1x-keyboard-nordic-session.service',
}


def cpio_entries(data):
    offset = 0
    result = {}
    while offset + 110 <= len(data):
        header = data[offset:offset+110]
        if header[:6] not in (b'070701', b'070702'):
            raise ValueError('Unexpected cpio payload header')
        fields = [int(header[6+i*8:14+i*8],16) for i in range(13)]
        mode, uid, gid, size, namesize = fields[1], fields[2], fields[3], fields[6], fields[11]
        offset += 110
        rawname = data[offset:offset+namesize]
        if not rawname.endswith(b'\0'):
            raise ValueError('Invalid cpio member name')
        name = rawname[:-1].decode()
        offset = (offset+namesize+3)&~3
        content = data[offset:offset+size]
        if len(content) != size:
            raise ValueError('Truncated cpio entry')
        offset = (offset+size+3)&~3
        if name == 'TRAILER!!!':
            return result
        if name.startswith('./'):
            name = name[2:]
        if not name or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('Unsafe payload path')
        if name in result:
            raise ValueError('Duplicate payload path')
        if uid != 0 or gid != 0 or stat.S_ISLNK(mode):
            raise ValueError('Unexpected payload owner or symbolic link')
        result[name] = (mode,content)
    raise ValueError('Missing cpio trailer')


def output(args, binary=False):
    return subprocess.check_output(args, text=not binary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rpm',type=Path)
    parser.add_argument('--rpm-command',default=os.environ.get('RPM','rpm'))
    parser.add_argument('--rpm2cpio',default=os.environ.get('RPM2CPIO','rpm2cpio'))
    args = parser.parse_args()
    # RPM 6 may open a transaction lock even for package-file queries. Keep
    # that private metadata outside the host's system RPM database.
    dbpath = ROOT/'.build/query-rpmdb'
    dbpath.mkdir(parents=True,exist_ok=True)
    query = [args.rpm_command,'--dbpath',str(dbpath)]
    rpm = str(args.rpm.resolve())
    metadata = output(query+['-qp','--qf',
                       '%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}\n%{PAYLOADCOMPRESSOR}\n',rpm]).splitlines()
    if metadata != ['pro1x-keyboard-nordic','0.1.0','0.2','noarch','gzip']:
        raise SystemExit('Unexpected package metadata: '+repr(metadata))
    dependencies = output(query+['-qp','--requires',rpm])
    for unsupported in ('PayloadIsZstd','PayloadIsXz','rpmlib(RichDependencies)','rpmlib(ShortCircuited)'):
        if unsupported in dependencies:
            raise SystemExit('Unsupported generated dependency: '+unsupported)
    scripts = output(query+['-qp','--scripts',rpm])
    if 'remove_package.py || exit 1' not in scripts:
        raise SystemExit('Missing safe package-removal hook')
    # Check each shell section independently; do not execute any scriptlet.
    body = []
    for line in scripts.splitlines()+['END scriptlet (using /bin/sh):']:
        if 'scriptlet (using ' in line:
            if body:
                subprocess.run(['/bin/sh','-n'],input='\n'.join(body)+'\n',text=True,check=True)
            body = []
        else:
            body.append(line)
    entries = cpio_entries(output([args.rpm2cpio,rpm],binary=True))
    for target,source in EXPECTED.items():
        mode,content = entries[target]
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode)!=0o644:
            raise SystemExit('Unexpected payload mode: '+target)
        if content != (ROOT/source).read_bytes():
            raise SystemExit('Payload differs from reviewed source: '+target)
    extras = set(entries)-set(EXPECTED)
    for path in extras:
        if path.startswith(('usr/share/doc/pro1x-keyboard-nordic',
                            'usr/share/licenses/pro1x-keyboard-nordic')):
            continue
        if stat.S_ISDIR(entries[path][0]) and path in ('usr/lib/pro1x-keyboard-nordic',):
            continue
        raise SystemExit('Unexpected package-owned path: '+path)
    print('Header: '+', '.join(metadata))
    print('Payload paths: '+str(len(entries)))
    print('Exact source comparisons: '+str(len(EXPECTED)))
    print('Shell scriptlet syntax verified; scriptlets NOT executed.')
    print('Dependencies:\n'+dependencies,end='')
    print('SHA256 '+hashlib.sha256(args.rpm.read_bytes()).hexdigest())
    print('RPM artifact inspection passed; not an installation/lifecycle test.')


if __name__ == '__main__':
    main()
