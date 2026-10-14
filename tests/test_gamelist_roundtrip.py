# -*- coding: utf-8 -*-
"""gamelist 注册/撤销的**字节级往返**自测（离线，不碰真机）

起因：真机上跑 `install.sh` → `uninstall.sh` 往返后，ports/gamelist.xml
的 md5 变了 —— 多了**一个空行**。语义上无害（XML 里空行没意义），但
那个空行会**每往返一次多攒一个**，因为 unregister 只回退了行首缩进，
没管块前面那个 '\\n'。这不是 bug 报告，是"幂等安装"这条承诺的破口。

本测试把这条承诺钉住：upsert → unregister 之后，文件必须与原文**逐字节
相同**；且反复往返不得累积任何东西。
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from romstore import cli          # noqa: E402
from romstore import config       # noqa: E402
from romstore import install as inst   # noqa: E402

FAIL = []


def check(name, cond, extra=''):
    print(('  [OK] ' if cond else '  [FAIL] ') + name
          + ('  ' + str(extra) if extra else ''))
    if not cond:
        FAIL.append(name)


# cli.cmd_register 内部硬编码的条目，测试里要对齐
ENTRY = [{'rom_name': 'RomStore.sh', 'title': 'ROM 商店',
          'desc': '从局域网 NAS 浏览并安装游戏到本机'}]

_HEAD = '<?xml version="1.0"?>\n<gameList>\n'

_G1 = ('  <game>\n'
       '    <path>./PortMaster.sh</path>\n'
       '    <name>PortMaster</name>\n'
       '  </game>\n')

_G2 = ('  <game>\n'
       '    <path>./[中]小丑牌-金博之友4.sh</path>\n'
       '    <name>[中]小丑牌-金博之友4</name>\n'
       '    <image>./images/[中]小丑牌-金博之友4.png</image>\n'
       '  </game>\n')

CASES = [
    ('ports 目录：LF、两个已有游戏',
     _HEAD + _G1 + _G2 + '</gameList>\n', b''),
    ('CRLF 换行风格',
     (_HEAD + _G1 + _G2 + '</gameList>\n').replace('\n', '\r\n'), b''),
    ('带 UTF-8 BOM',
     _HEAD + _G1 + _G2 + '</gameList>\n', b'\xef\xbb\xbf'),
    ('空骨架（没有任何 game）',
     _HEAD + '</gameList>\n', b''),
    ('骨架末尾没有换行',
     _HEAD + _G1 + '</gameList>', b''),
]


def roundtrip(raw, n=3):
    """跑 n 次 upsert + unregister，返回每轮之后的 bytes"""
    d = tempfile.mkdtemp(prefix='romstore-gl-')
    gl = os.path.join(d, 'gamelist.xml')
    try:
        out = []
        for _ in range(n):
            with open(gl, 'wb') as f:
                f.write(raw)
            old = config.ES_OPTIONS_DIR
            config.ES_OPTIONS_DIR = d
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    inst.upsert_gamelist(d, ENTRY)
                    cli.cmd_register(True)
            finally:
                config.ES_OPTIONS_DIR = old
            with open(gl, 'rb') as f:
                out.append(f.read())
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


print('== 1. 往返必须逐字节复原（连跑 3 次，防累积）==')
for name, body, bom in CASES:
    raw = bom + body.encode('utf-8')
    outs = roundtrip(raw)
    for i, got in enumerate(outs, 1):
        if got != raw:
            check('%s —— 第 %d 次往返' % (name, i), False,
                  '多出/丢失 %d 字节；尾部=%r vs %r'
                  % (len(got) - len(raw), got[-24:], raw[-24:]))
            break
    else:
        check('%s —— 3 次往返都逐字节复原' % name, True)

print('\n== 2. 注册后条目确实在文件里（别为了复原而没注册）==')
d = tempfile.mkdtemp(prefix='romstore-gl-')
try:
    gl = os.path.join(d, 'gamelist.xml')
    with open(gl, 'wb') as f:
        f.write((_HEAD + _G1 + '</gameList>\n').encode('utf-8'))
    old = config.ES_OPTIONS_DIR
    config.ES_OPTIONS_DIR = d
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            inst.upsert_gamelist(d, ENTRY)
        after = open(gl, 'rb').read().decode('utf-8')
        check('注册后含 ./RomStore.sh', './RomStore.sh' in after)
        check('注册后含中文名「ROM 商店」', 'ROM 商店' in after)
        check('注册后原有条目未被破坏', './PortMaster.sh' in after)
        # 幂等：再注册一次不该新增
        with contextlib.redirect_stdout(io.StringIO()):
            added, updated, _ = inst.upsert_gamelist(d, ENTRY)
        check('重复注册是幂等更新（added=0）', added == 0, 'added=%d' % added)
    finally:
        config.ES_OPTIONS_DIR = old
finally:
    shutil.rmtree(d, ignore_errors=True)

print('\n== 3. 撤销是幂等的：第二次撤销不改文件 ==')
d = tempfile.mkdtemp(prefix='romstore-gl-')
try:
    gl = os.path.join(d, 'gamelist.xml')
    base = _HEAD + _G1 + _G2 + '</gameList>\n'
    with open(gl, 'wb') as f:
        f.write(base.encode('utf-8'))
    old = config.ES_OPTIONS_DIR
    config.ES_OPTIONS_DIR = d
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            inst.upsert_gamelist(d, ENTRY)
            cli.cmd_register(True)
        once = open(gl, 'rb').read()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_register(True)
        twice = open(gl, 'rb').read()
        check('首次撤销后复原', once == base.encode('utf-8'))
        check('二次撤销不再改动文件', twice == once)
    finally:
        config.ES_OPTIONS_DIR = old
finally:
    shutil.rmtree(d, ignore_errors=True)

print('\n== 4. 块在文件中间时也能干净删除 ==')
d = tempfile.mkdtemp(prefix='romstore-gl-')
try:
    gl = os.path.join(d, 'gamelist.xml')
    # 手工摆一个 RomStore 条目在中间（前后都有别的游戏）
    body = (_HEAD + _G1
            + '  <game>\n    <path>./RomStore.sh</path>\n'
              '    <name>ROM 商店</name>\n  </game>\n'
            + _G2 + '</gameList>\n')
    want = _HEAD + _G1 + _G2 + '</gameList>\n'
    with open(gl, 'wb') as f:
        f.write(body.encode('utf-8'))
    old = config.ES_OPTIONS_DIR
    config.ES_OPTIONS_DIR = d
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_register(True)
    finally:
        config.ES_OPTIONS_DIR = old
    got = open(gl, 'rb').read().decode('utf-8')
    check('中间条目被删除', './RomStore.sh' not in got)
    check('删除后与"从未注册过"的文件逐字节相同', got == want)
finally:
    shutil.rmtree(d, ignore_errors=True)

print('\n== 5. 没有条目时撤销：报"无需撤销"且不改文件 ==')
d = tempfile.mkdtemp(prefix='romstore-gl-')
try:
    gl = os.path.join(d, 'gamelist.xml')
    base = _HEAD + _G1 + '</gameList>\n'
    with open(gl, 'wb') as f:
        f.write(base.encode('utf-8'))
    old = config.ES_OPTIONS_DIR
    config.ES_OPTIONS_DIR = d
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_register(True)
    finally:
        config.ES_OPTIONS_DIR = old
    check('返回 0', rc == 0, rc)
    check('报告无需撤销', '无需撤销' in buf.getvalue(), buf.getvalue().strip())
    check('文件未被改动', open(gl, 'rb').read().decode('utf-8') == base)
finally:
    shutil.rmtree(d, ignore_errors=True)

print('\n== 6. gamelist 不存在时撤销不炸 ==')
d = tempfile.mkdtemp(prefix='romstore-gl-')
try:
    old = config.ES_OPTIONS_DIR
    config.ES_OPTIONS_DIR = d
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_register(True)
        ok = (rc == 0 and '没有 gamelist' in buf.getvalue())
    except Exception as e:
        ok = False
        buf = 'exception: %s' % e
    finally:
        config.ES_OPTIONS_DIR = old
    check('缺文件时优雅返回', ok, buf if not ok else '')
finally:
    shutil.rmtree(d, ignore_errors=True)

print('\n' + ('全部通过 ✅' if not FAIL else '失败项 = %d: %s' % (len(FAIL), FAIL)))
sys.exit(1 if FAIL else 0)
