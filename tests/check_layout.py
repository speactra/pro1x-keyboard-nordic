import ctypes as C
from pathlib import Path
X = C.CDLL('libxkbcommon.so.0')
PTR = C.c_void_p
U = C.c_uint

def func(name, args, result):
    f = getattr(X, name)
    f.argtypes = args
    f.restype = result
    return f

ctxnew = func('xkb_context_new', [U], PTR)
clear = func('xkb_context_include_path_clear', [PTR], None)
append = func('xkb_context_include_path_append', [PTR, C.c_char_p], C.c_int)
class Names(C.Structure):
    _fields_ = [(k, C.c_char_p) for k in ['rules','model','layout','variant','options']]
kmnew = func('xkb_keymap_new_from_names', [PTR, C.POINTER(Names), U], PTR)
stnew = func('xkb_state_new', [PTR], PTR)
update = func('xkb_state_update_key', [PTR,U,C.c_int], U)
utf8 = func('xkb_state_key_get_utf8', [PTR,U,C.c_char_p,C.c_size_t], C.c_int)
stfree = func('xkb_state_unref', [PTR], None)
kmfree = func('xkb_keymap_unref', [PTR], None)
ctxfree = func('xkb_context_unref', [PTR], None)
root = Path(__file__).resolve().parents[1]
ctx = ctxnew(0)
clear(ctx)
assert append(ctx, str(root/'src/xkb').encode())
import os
assert append(ctx, os.environ.get('XKB_CONFIG_ROOT','/usr/share/X11/xkb').encode())

def make(layout):
    names = Names(b'evdev',b'pc105',layout.encode(),b'',b'')
    km = kmnew(ctx,C.byref(names),0)
    assert km, 'Cannot compile '+layout
    return km

def key(km, modifiers, code):
    state = stnew(km)
    try:
        for m in modifiers:
            update(state,m+8,1)
        update(state,code+8,1)
        out = C.create_string_buffer(64)
        utf8(state,code+8,out,64)
        return out.value.decode()
    finally:
        stfree(state)

km = make('pro1x_nordic')
tests = [
 ('ö', [54], 53, 'ö'), ('Ö', [42,54], 53, 'Ö'),
 ('å', [],26,'å'), ('Å',[42],26,'Å'), ('ä',[],40,'ä'), ('Ä',[42],40,'Ä'),
 ('@ on W',[100],17,'@'), ('section on Q',[100],16,'§'),
 ('$ on E',[100],18,'$'), ('degree on R',[100],19,'°'),
 ('brace on U',[100],22,'{'), ('bracket on I',[100],23,'['),
 ('bracket on O',[100],24,']'), ('brace on P',[100],25,'}'),
 ('backslash on å',[100],26,chr(92)), ('asterisk on K',[100],37,'*'),
 ('apostrophe on L',[100],38,"'"), ('oslash',[100,54],53,'ø'),
 ('Oslash',[100,54,42],53,'Ø'), ('ae',[100],40,'æ'), ('AE',[100,42],40,'Æ'),
 ('less',[],41,'<'), ('greater with Shift',[42],41,'>'), ('greater with AltGr',[100],41,'>'),
 ('bar on Z',[100],44,'|'), ('tilde on N',[100],49,'~'),
 ('semicolon on comma',[100],51,';'), ('colon on period',[100],52,':'),
 ('underscore on minus',[100],53,'_'), ('minus',[],53,'-'),
 ('plus',[],12,'+'), ('question',[100],12,'?'),
 ('normal a',[],30,'a'), ('normal A',[42],30,'A'), ('a while ö held',[54],30,'a')]
for code,char in zip(range(2,12),'!"#€%&/()='):
    tests.append(('Shift digit '+str(code),[42],code,char))
    tests.append(('AltGr digit '+str(code),[100],code,char))
for label,mods,code,expected in tests:
    actual=key(km,mods,code)
    print(('PASS' if actual==expected else 'FAIL')+': '+label+' -> '+repr(actual))
    assert actual==expected,(label,actual,expected)
sym=func('xkb_state_key_get_one_sym',[PTR,U],U)
symname=func('xkb_keysym_get_name',[U,C.c_char_p,C.c_size_t],C.c_int)
for label,mods,code,expected in [('acute',[],13,'acute'),('grave',[100],13,'grave'),('diaeresis',[],27,'diaeresis'),('circumflex',[100],27,'asciicircum')]:
    state=stnew(km)
    for m in mods:update(state,m+8,1)
    update(state,code+8,1)
    buf=C.create_string_buffer(64);symname(sym(state,code+8),buf,64)
    assert buf.value.decode()==expected,(label,buf.value,expected)
    print('PASS: '+label+' -> '+expected);stfree(state)
print('PRINTED-LEGEND TESTS PASSED:',len(tests)+4)
kmfree(km);ctxfree(ctx)
