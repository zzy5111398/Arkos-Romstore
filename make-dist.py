#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 ark-romstore 打成可分发的 tar.gz 与 zip。

    python3 make-dist.py                    # 输出到项目上一级目录
    python3 make-dist.py -o /tmp/dist       # 指定输出目录
    python3 make-dist.py --list             # 只看会打进哪些文件，不写盘

为什么不用 tar / zip 命令：
  1. Windows 上 Git Bash 的 zip 未必有，且各发行版 tar 的 --transform 写法不一；
  2. **权限位**是本项目最容易翻车的点 —— Windows 文件系统上 `install.sh`
     没有可执行位，直接 `tar` 打出来包内就是 0666，掌机上 `./install.sh`
     会 Permission denied。这里对每个条目**显式写死 mode**，与宿主无关；
  3. 需要排除 __pycache__ 与输出包自身，tar 的 --exclude 在跨平台上不统一。

只用标准库，Python 3.5+ 的任何平台都能跑。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import pathlib
import sys
import tarfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent

# --------------------------------------------------------------- 打包规则

#: 一律排除的目录名（任意层级）
SKIP_DIRS = {
    '__pycache__', '.git', '.svn', '.hg', '.idea', '.vscode',
    '.pytest_cache', '.mypy_cache', 'node_modules',
    # 开发期自测，不分发给装机的人
    'tests',
    # 项目元数据（工作日志 / 长期笔记）—— 是开发过程的产物，不分发给装机的人
    '.workbuddy',
    # 验收截图（0.5.0 起）：本机无头渲染的 PNG，纯开发过程产物
    '.shots',
    # docs/ 只为 GitHub 上的 README 服务（截图在仓库里能显示），
    # 装机的人拿到的是 tar.gz，README 里的图引不到也没意义，白占 200 KB
    'docs',
}

#: 一律排除的后缀
SKIP_SUFFIX = {'.pyc', '.pyo', '.pyd', '.orig', '.rej', '.swp'}

#: 一律排除的文件名
SKIP_NAMES = {'.DS_Store', 'Thumbs.db', 'desktop.ini'}

#: 开发期自测脚本（下划线开头），不分发给装机的人
SKIP_PREFIX = '_'

#: 打包脚本自己产出的东西，不能进包
SKIP_GLOBS = ('*.tar.gz', '*.tgz', '*.zip', '*.tar')


def mode_for(rel: pathlib.Path) -> int:
    """决定包装进压缩包里的 Unix 权限位。

    规则（只有三种）：
      * 目录里的 `.sh`            -> 755   （install.sh / uninstall.sh / src/*.sh）
      * 打包脚本本身              -> 755
      * NetworkManager dispatcher -> 755   （它是被 exec 调用的脚本）
      * 其余                      -> 644
    """
    if rel.suffix == '.sh':
        return 0o755
    if rel.name == 'make-dist.py':
        return 0o755
    if rel.parts[:1] == ('systemd',) and rel.name.startswith('50-'):
        return 0o755
    return 0o644


def collect(root: pathlib.Path):
    """返回 [(绝对路径, 包内相对路径, mode)]，按包内路径排序。"""
    items = []
    for p in sorted(root.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(root)

        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in SKIP_SUFFIX or p.name in SKIP_NAMES:
            continue
        if p.name.startswith(SKIP_PREFIX):
            continue
        # 开发期的 "_" 目录（_covers / _out / _stage …）也要整棵跳过。
        # ⚠️ 必须按**目录名**判，且排掉 `__*` —— 只看文件名的 SKIP_PREFIX
        #    拦不住 `_out/multiplayer-games.md` 这类文件；而不排 `__` 又会把
        #    `src/romstore/__init__.py` 一起误杀（缺了它 import 直接崩）。
        #    （2026-10-15 实测：0.6.0 的包被这三个目录撑到 11.7 MB / 120 个文件）
        if any(part.startswith(SKIP_PREFIX) and not part.startswith('__')
               for part in rel.parts[:-1]):
            continue
        if any(rel.match(g) for g in SKIP_GLOBS):
            continue

        items.append((p, rel, mode_for(rel)))

    items.sort(key=lambda t: str(t[1]))
    return items


# --------------------------------------------------------------- 写盘

def write_tar_gz(items, out_path: pathlib.Path, prefix: str, epoch=None):
    """写 tar.gz。

    注意：`--epoch` 必须同时固定 **两层** 时间戳，否则不是可复现构建 ——
      1. tar 里每个条目的 mtime
      2. **gzip 容器头部的 mtime** —— 这一层最容易漏。`tarfile.open(mode='w:gz')`
         不接受 mtime 参数，它内部用 `time.time()`；两次构建只要落到不同的秒，
         md5 就不同（很容易被误判成「可复现」——背靠背跑两次往往是同一秒）。
         所以要自己套一层 GzipFile 并把 mtime 传进去。
    """
    if epoch is None:
        with tarfile.open(str(out_path), 'w:gz') as tf:
            for src, rel, mode in items:
                _add_tar_member(tf, src, '%s/%s' % (prefix, rel.as_posix()),
                                mode, None)
        return

    # 显式控制 gzip 层：filename='' 避免把宿主路径写进头部
    with open(str(out_path), 'wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw,
                           mtime=epoch) as gz:
            with tarfile.open(fileobj=gz, mode='w') as tf:
                for src, rel, mode in items:
                    _add_tar_member(tf, src,
                                    '%s/%s' % (prefix, rel.as_posix()),
                                    mode, epoch)


def _add_tar_member(tf, src: pathlib.Path, arcname: str, mode: int, epoch):
    ti = tf.gettarinfo(str(src), arcname=arcname)
    ti.mode = mode
    ti.uid = 0
    ti.gid = 0
    ti.uname = ''
    ti.gname = ''
    if epoch is not None:
        ti.mtime = epoch
    with open(str(src), 'rb') as fh:
        tf.addfile(ti, fh)


def write_zip(items, out_path: pathlib.Path, prefix: str, epoch=None):
    with zipfile.ZipFile(str(out_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for src, rel, mode in items:
            arcname = '%s/%s' % (prefix, rel.as_posix())

            zi = zipfile.ZipInfo.from_file(str(src), arcname)
            # create_system=3 (Unix) 才会让解压方尊重 external_attr 里的权限位
            zi.create_system = 3
            zi.external_attr = (mode & 0xFFFF) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            if epoch is not None:
                zi.date_time = _epoch_to_dostime(epoch)

            with open(str(src), 'rb') as fh:
                zf.writestr(zi, fh.read())


def _epoch_to_dostime(epoch: int):
    import time
    t = time.gmtime(epoch)
    year = max(1980, t.tm_year)
    return (year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


# --------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='打包 ark-romstore 为 tar.gz / zip',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('-o', '--outdir', default=str(HERE.parent),
                    help='输出目录（默认：项目上一级）')
    ap.add_argument('--list', action='store_true',
                    help='只列出会打进包的文件，不写盘')
    ap.add_argument('--epoch', type=int,
                    default=(int(os.environ['SOURCE_DATE_EPOCH'])
                             if os.environ.get('SOURCE_DATE_EPOCH') else None),
                    help='把所有条目 mtime 固定为该 Unix 时间戳（可复现构建）')
    args = ap.parse_args(argv)

    vfile = HERE / 'VERSION'
    if not vfile.is_file():
        print('错误：找不到 %s' % vfile, file=sys.stderr)
        return 2
    version = vfile.read_text(encoding='utf-8').strip()
    if not version:
        print('错误：%s 是空的' % vfile, file=sys.stderr)
        return 2

    prefix = 'ark-romstore-%s' % version
    items = collect(HERE)

    if not items:
        print('错误：没有可打包的文件', file=sys.stderr)
        return 1

    # 打之前先把关：必备文件在不在
    need = ['install.sh', 'uninstall.sh', 'README.md', 'RELEASE-NOTES.md',
            'CHANGELOG.md', 'VERSION',
            'src/run.py', 'src/RomStore.sh', 'src/nas-roms-mount.sh',
            'src/romstore/ui.py', 'src/romstore/media.py',
            'src/romstore/cover.py', 'src/romstore/batchmedia.py',
            # ui.py 里 `from . import vplayer`，缺了整包装完直接起不来
            'src/romstore/vplayer.py',
            # 0.6.0 起 config.py 的一切路径都从 platform.py 取，缺了 import 就崩
            'src/romstore/platform.py',
            'src/romstore/config.py',
            'src/romstore/mapp.py',
            'systemd/nas-roms.service',
            # ROCKNIX 用 rclone 版单元（内核没有 cifs），与上面那份按平台二选一
            'systemd/nas-roms-rocknix.service',
            # 挂载改由 timer 在开机后异步触发，避免 oneshot 挂 multi-user.target
            # 导致开机被 NAS 超时拖住（见 nas-roms.timer 顶部注释）
            'systemd/nas-roms.timer',
            'systemd/roms-fs-selfheal.service',
            'src/systemd/roms-fs-selfheal.sh',
            'systemd/roms-fs-watch.service',
            'src/systemd/roms-fs-watch.sh',
            # mpv 起不来时修 Mali 抢占 libgbm.so.1（Mali 掌机上按 X 无反应就是它）
            'src/systemd/mpv-libgbm-fix.sh']
    have = {rel.as_posix() for _, rel, _ in items}
    missing = [n for n in need if n not in have]
    if missing:
        print('错误：安装包缺少必备文件：%s' % ', '.join(missing), file=sys.stderr)
        return 1

    # 拆开来的两个模板文件必须带占位符，否则 install.sh 的模板逻辑是死代码
    for tpl in ('src/RomStore.sh', 'src/nas-roms-mount.sh'):
        txt = (HERE / tpl).read_text(encoding='utf-8', errors='replace')
        if '@@STORE_DIR@@' not in txt:
            print('警告：%s 里没有 @@STORE_DIR@@ 占位符，确认它没被误渲染？' % tpl,
                  file=sys.stderr)

    total = sum(p.stat().st_size for p, _, _ in items)

    print('版本       %s' % version)
    print('包内前缀   %s/' % prefix)
    print('文件数     %d' % len(items))
    print('原始大小   %.1f KB' % (total / 1024.0))
    print('')

    if args.list:
        for _, rel, mode in items:
            print('  %s  %s' % (oct(mode)[2:].rjust(3, '0'), rel.as_posix()))
        return 0

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    tgz = outdir / ('%s.tar.gz' % prefix)
    zp = outdir / ('%s.zip' % prefix)

    write_tar_gz(items, tgz, prefix, epoch=args.epoch)
    write_zip(items, zp, prefix, epoch=args.epoch)

    for f in (tgz, zp):
        print('  %-32s %8.1f KB' % (f.name, f.stat().st_size / 1024.0))

    # 校验和落在包外（不能写进包内 —— 那样哈希会自我引用，永远对不上）
    # 必须用二进制写：Path.write_text() 在 Windows 上会把 \n 翻成 \r\n，
    # 而 sha256sum -c 在 Linux 侧读到行尾的 \r 会直接报 "No such file or directory"。
    sha = outdir / 'SHA256SUMS'
    lines = []
    for f in (tgz, zp):
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append('%s  %s' % (h, f.name))
    with open(str(sha), 'wb') as fh:
        fh.write(('\n'.join(lines) + '\n').encode('ascii'))
    print('')
    print('  %s' % sha.name)
    for ln in lines:
        print('    %s' % ln)

    execs = [rel.as_posix() for _, rel, mode in items if mode & 0o111]
    print('')
    print('可执行位   %s' % ', '.join(execs))
    print('')
    print('校验：tar tzvf %s' % tgz.name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
