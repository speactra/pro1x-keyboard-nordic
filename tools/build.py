#!/usr/bin/env python3
"""Build RPM, SRPM and source archives without installing target packages."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

NAME = 'pro1x-keyboard-nordic'
VERSION = '0.1.0'
ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    print('+', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    rpmbuild = os.environ.get('RPMBUILD') or shutil.which('rpmbuild')
    if not rpmbuild:
        raise SystemExit('Set RPMBUILD to rpmbuild, or install RPM build tools in an isolated environment.')
    required = ['src/pro1x_nordic.py', 'src/service_runtime.py',
                'src/session_agent.py', 'docs/runtime-design.md']
    for relative in required:
        if not (ROOT / relative).is_file():
            raise SystemExit('Required source missing: ' + relative)
    env = os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.setdefault('SOURCE_DATE_EPOCH', '0')
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], cwd=ROOT, env=env)
    run([sys.executable, 'tests/check_layout.py'], cwd=ROOT, env=env)
    top = ROOT / '.build/rpm'
    output = ROOT / 'dist'
    for folder in ['BUILD', 'BUILDROOT', 'RPMS', 'SOURCES', 'SPECS', 'SRPMS']:
        (top / folder).mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=True)
    archive = top / 'SOURCES' / (NAME + '-' + VERSION + '.tar.gz')
    members = [ROOT/'LICENSE', ROOT/'README.md']
    for folder in ['src', 'tests', 'packaging', 'docs', 'tools']:
        members.extend(p for p in (ROOT/folder).rglob('*')
                       if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    with archive.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as tar:
                for path in sorted(set(members)):
                    if path.is_symlink():
                        raise RuntimeError('Source symlink refused: ' + str(path))
                    relative = path.relative_to(ROOT)
                    info = tarfile.TarInfo(NAME + '-' + VERSION + '/' + str(relative))
                    data = path.read_bytes()
                    info.size = len(data)
                    info.mode = 0o644
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = 'root'
                    tar.addfile(info, io.BytesIO(data))
    spec = top/'SPECS'/(NAME+'.spec')
    shutil.copyfile(ROOT/'packaging'/(NAME+'.spec'), spec)
    temp_root = Path(os.environ.get('TMPDIR', str(ROOT/'.build/tmp'))) / 'pro1x-rpm-build'
    temp_root.mkdir(parents=True, exist_ok=True)
    run([rpmbuild, '-ba', '--nodeps',
         '--define', '_tmppath '+str(temp_root),
         '--define', 'use_source_date_epoch_as_buildtime 1',
         '--define', 'build_mtime_policy clamp_to_source_date_epoch',
         '--define', '_topdir '+str(top),
         '--define', '_rpmdir '+str(output),
         '--define', '_srcrpmdir '+str(output),
         '--define', '_buildhost reproducible.invalid',
         '--define', '_rpmformat 4', str(spec)], cwd=ROOT, env=env)
    shutil.copyfile(archive, output/archive.name)
    artifacts = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
    sums = ''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(output))+'\n' for p in artifacts)
    (output/'SHA256SUMS').write_text(sums)
    print(sums, end='')
    print('Build complete. No installation, activation or publication performed.')


if __name__ == '__main__':
    main()
