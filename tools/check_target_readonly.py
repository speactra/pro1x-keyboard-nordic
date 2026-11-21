#!/usr/bin/env python3
"""Read-only native XKB checks over existing, host-key-verified SSH access."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import runpy
import shlex
import subprocess

REMOTE = 'import ctypes as C,sys,json\npayload=json.load(sys.stdin)\nx=C.CDLL(\'libxkbcommon.so.0\'); P=C.c_void_p; U=C.c_uint\nfor name,args,result in [(\'xkb_context_new\',[U],P),(\'xkb_keymap_new_from_string\',[P,C.c_char_p,C.c_int,C.c_int],P),(\'xkb_state_new\',[P],P),(\'xkb_state_update_key\',[P,U,C.c_int],U),(\'xkb_state_key_get_utf8\',[P,U,C.c_char_p,C.c_size_t],C.c_int),(\'xkb_state_unref\',[P],None),(\'xkb_keymap_unref\',[P],None),(\'xkb_context_unref\',[P],None)]:\n f=getattr(x,name);f.argtypes=args;f.restype=result\nbody=payload[\'symbol\'].split(\'{\',1)[1].rsplit(\'}\',1)[0]\ntext=\'xkb_keymap { xkb_keycodes { include "evdev+aliases(qwerty)" }; xkb_types { include "complete" }; xkb_compatibility { include "complete" }; xkb_symbols { include "pc" \'+body+\' }; };\'\nctx=x.xkb_context_new(0);km=x.xkb_keymap_new_from_string(ctx,text.encode(),1,0)\nassert km, \'native compilation failed\'\nresults=[]\nfor label,mods,code,expected in payload[\'cases\']:\n state=x.xkb_state_new(km)\n for mod in mods:x.xkb_state_update_key(state,mod+8,1)\n x.xkb_state_update_key(state,code+8,1)\n buf=C.create_string_buffer(64);x.xkb_state_key_get_utf8(state,code+8,buf,64)\n actual=buf.value.decode();results.append([label,actual,expected,actual==expected]);x.xkb_state_unref(state)\nx.xkb_keymap_unref(km);x.xkb_context_unref(ctx)\nprint(json.dumps({\'native_cases\':len(results),\'passed\':sum(r[3] for r in results),\'failures\':[r for r in results if not r[3]]},ensure_ascii=False))\nassert all(r[3] for r in results)\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True,help='Existing trusted SSH user@host')
    parser.add_argument('--identity',help='Existing SSH private-key path; never copied')
    args=parser.parse_args()
    if args.host.startswith('-') or any(c.isspace() for c in args.host):
        parser.error('Invalid SSH destination')
    root=Path(__file__).resolve().parents[1]
    with contextlib.redirect_stdout(io.StringIO()):
        fixture=runpy.run_path(str(root/'tests/check_layout.py'))
    cases=fixture['tests']+[('acute',[],13,'´'),('grave',[100],13,'`'),
                            ('diaeresis',[],27,'¨'),('circumflex',[100],27,'^')]
    command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',
             '-o','StrictHostKeyChecking=yes']
    if args.identity:
        command += ['-i',args.identity,'-o','IdentitiesOnly=yes']
    command += ['--',args.host,'python3 -c '+shlex.quote(REMOTE)]
    payload={'symbol':(root/'src/xkb/symbols/pro1x_nordic').read_text(),'cases':cases}
    result=subprocess.run(command,input=json.dumps(payload),universal_newlines=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
    if result.stderr:
        print(result.stderr,end='')
    print(result.stdout,end='')
    result.check_returncode()
    report=json.loads(result.stdout)
    if report['passed']!=len(cases) or report['native_cases']!=len(cases):
        raise SystemExit('Incomplete or failed target test')
    print('Native library verified only: no files, settings, input maps or services changed.')


if __name__=='__main__':
    main()
