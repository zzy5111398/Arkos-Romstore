# -*- coding: utf-8 -*-
"""命令行入口（无头，用于部署验证与排障）

用法：
  python3 -m romstore.cli --validate        校验映射表与 ES 配置
  python3 -m romstore.cli --build [--force]  建 / 更新索引
  python3 -m romstore.cli --stats           索引概况
  python3 -m romstore.cli --systems         系统列表
  python3 -m romstore.cli --list FC [--page 1] [--size 30]
  python3 -m romstore.cli --search 马里奥 [--limit 20]
  python3 -m romstore.cli --detail <game_id>
  python3 -m romstore.cli --install <game_id> [--dry-run]

商店源（NAS 共享）：
  python3 -m romstore.cli --source-show          当前源配置 + 挂载状态
  python3 -m romstore.cli --source-set 10.168.1.100,Roms[,账号[,密码]]
  python3 -m romstore.cli --source-reset         恢复默认源
  python3 -m romstore.cli --scan-hosts [10.168.1]   扫描开 445 的主机
  python3 -m romstore.cli --mount [--force]      按当前配置挂载
  python3 -m romstore.cli --umount
"""
import argparse
import os
import sys
import time

from . import config
from . import index as idx
from . import install as inst
from . import mapp
from . import meta
from . import source as src


def _p(stage, cur, total, msg):
    if stage in ('scan',):
        if cur % 5 == 0 or cur == total:
            sys.stderr.write('\r  扫描 %d/%d  %-24s' % (cur, total, msg[:24]))
            sys.stderr.flush()
    else:
        sys.stderr.write('\r  %s %s/%s %-24s' % (stage, cur, total, msg[:24]))
        sys.stderr.flush()


def cmd_validate():
    print('=== 校验映射表 ===')
    print('es_systems.cfg: %s  %s'
          % (config.ES_SYSTEMS_CFG,
             '存在' if os.path.isfile(config.ES_SYSTEMS_CFG) else '不存在'))
    syslist = mapp.load_es_systems()
    print('ES 系统数: %d' % len(syslist))
    print('映射表条目: %d（ok %d / maybe %d）'
          % (len(mapp.MAP),
             sum(1 for m in mapp.MAP if m[3] == 'ok'),
             sum(1 for m in mapp.MAP if m[3] == 'maybe')))
    print('明确不支持: %d' % len(mapp.UNSUPPORTED))
    probs = mapp.validate()
    if probs:
        print('!! 发现问题 %d 个:' % len(probs))
        for a, b in probs:
            print('   %-22s %s' % (a, b))
    else:
        print('映射表与 ES 配置完全一致，无问题')

    # NAS 侧核对
    dirs, err = idx.scan_nas()
    if err:
        print('!! 读取 NAS 失败: %s' % err)
        return 1
    print('NAS 有 metadata 的目录: %d' % len(dirs))
    mapped = [d for d, _m, _s in dirs if mapp.lookup(d)]
    unsup = [d for d, _m, _s in dirs if not mapp.lookup(d)]
    print('  可映射: %d   未映射: %d -> %s' % (len(mapped), len(unsup), unsup))
    return 0


def cmd_build(force):
    t0 = time.time()
    r = idx.build(progress=_p)
    sys.stderr.write('\n')
    if not r['ok']:
        print('建索引失败: %s' % r['error'])
        return 1
    print('建索引完成：耗时 %.3f 秒' % r['seconds'])
    print('  重建 %d 个系统 / 跳过 %d 个（指纹未变）/ NAS 目录共 %d'
          % (r['built'], r['skipped'], r['total_dirs']))
    print('  去重 %d 条' % r['dup'])
    if r['failed']:
        print('  失败 %d 个: %s' % (len(r['failed']), r['failed'][:5]))
    conn = idx.connect()
    st = idx.stats(conn)
    print('  索引内游戏 %d 条（去重后）| 系统 ok=%d maybe=%d'
          % (st['games'], st['sys_ok'], st['sys_maybe']))
    conn.close()
    return 0


def cmd_stats():
    conn = idx.connect()
    st = idx.stats(conn)
    print('游戏条目（去重后）: %d' % st['games'])
    print('被去重条目:        %d' % st['dup'])
    print('系统 ok / maybe:   %d / %d' % (st['sys_ok'], st['sys_maybe']))
    print('索引文件: %s  (%s)'
          % (config.DB_PATH,
             inst.human(os.path.getsize(config.DB_PATH)) if os.path.exists(config.DB_PATH) else '不存在'))
    conn.close()
    return 0


def cmd_systems():
    conn = idx.connect()
    rows = idx.systems(conn)
    print('%-22s %-16s %-20s %6s  %s' % ('NAS目录', 'ES名', 'ES路径', '游戏', '备注'))
    tot = 0
    for r in rows:
        tot += r['avail']
        mark = '' if r['conf'] == 'ok' else '⚠'
        print('%-22s %-16s %-20s %6d  %s%s' % (r['nas_dir'], r['es_name'],
                                               r['es_path'], r['avail'], mark, r['note'] or ''))
    print('-' * 70)
    print('合计 %d 个系统 / %d 款游戏' % (len(rows), tot))
    conn.close()
    return 0


def cmd_list(nas_dir, page, size):
    conn = idx.connect()
    total = idx.count_games(conn, nas_dir)
    if not total:
        print('没有找到 %s（可能未映射或需要先 --build）' % nas_dir)
        conn.close()
        return 1
    off = max(0, (page - 1) * size)
    rows = idx.games_of(conn, nas_dir, off, size)
    print('%s  共 %d 款  第 %d 页（每页 %d）' % (nas_dir, total, page, size))
    for r in rows:
        flags = []
        if r['is_multi']:
            flags.append('多碟')
        if r['has_boxart']:
            flags.append('有封面')
        if r['core']:
            flags.append(r['core'])
        print('  #%-7d %-40s %s' % (r['id'], r['title'][:40], ' '.join(flags)))
    conn.close()
    return 0


def cmd_search(kw, limit):
    conn = idx.connect()
    rows = conn.execute(
        'SELECT id,title,es_name,nas_dir,is_multi FROM games'
        ' WHERE dup=0 AND title LIKE ? ORDER BY title LIMIT ?',
        ('%' + kw + '%', limit)).fetchall()
    print('搜索 "%s" 命中 %d 条（最多显示 %d）' % (kw, len(rows), limit))
    for r in rows:
        print('  #%-7d %-38s [%s]' % (r['id'], r['title'][:38], r['nas_dir']))
    conn.close()
    return 0


def cmd_detail(gid):
    conn = idx.connect()
    g = idx.get_game(conn, gid)
    if not g:
        print('没有 id=%s 的游戏' % gid)
        conn.close()
        return 1
    print('id       : %d' % g['id'])
    print('标题     : %s' % g['title'])
    print('系统     : %s (%s) -> %s' % (g['es_name'], g['nas_dir'], g['es_path']))
    print('core     : %s' % (g['core'] or '-'))
    print('多碟     : %s' % ('是' if g['is_multi'] else '否'))
    print('media目录: %s' % (g['media_dir'] or '-'))
    print('有封面   : %s' % ('是' if g['has_boxart'] else '否'))

    srcs, total = inst.rom_sources(conn, g)
    print('ROM 文件 : %d 个, 合计 %s' % (len(srcs), inst.human(total)))
    for s, n, sz in srcs[:6]:
        print('    %-50s %s  存在=%s' % (n[:50], inst.human(sz), os.path.exists(s)))

    # 描述（从 NAS 现取）
    mp = os.path.join(config.NAS_ROOT, g['nas_dir'], config.METADATA_NAME)
    t0 = time.time()
    ds = meta.parse_descriptions(mp, {g['title']})
    dt = time.time() - t0
    d = ds.get(g['title'], '')
    print('描述     : %d 字（CIFS 读取耗时 %.3f 秒）' % (len(d), dt))
    print('---')
    print(d[:400])
    conn.close()
    return 0


def cmd_install(gid, dry):
    conn = idx.connect()
    g = idx.get_game(conn, gid)
    if not g:
        print('没有 id=%s 的游戏' % gid)
        conn.close()
        return 1
    print('安装 #%d %s -> %s' % (g['id'], g['title'], g['es_path']))
    t0 = time.time()
    last = ['']

    def cb(phase, done, total, msg):
        pct = (100.0 * done / total) if total else 0
        s = '\r  %-9s %5.1f%%  %s' % (phase, pct, msg[:36])
        if s != last[0]:
            sys.stderr.write(s)
            sys.stderr.flush()
            last[0] = s

    r = inst.install(conn, g, progress=cb, dry_run=dry)
    sys.stderr.write('\n')
    print('耗时 %.3f 秒' % (time.time() - t0))
    if not r['ok']:
        print('失败: %s' % r['error'])
        conn.close()
        return 1
    if r.get('dry_run'):
        print('干跑通过: 目标 %s, %d 个文件, %s' % (r['dest'], r['files'], inst.human(r['bytes'])))
    else:
        print('成功: 目标 %s' % r['dest'])
        print('  ROM %d 个 / %s' % (r['files'], inst.human(r['bytes'])))
        if r.get('m3u'):
            print('  m3u: %s' % r['m3u'])
        print('  gamelist: %s（新增 %d / 更新 %d）'
              % (r['gamelist'], r['gamelist_added'], r['gamelist_updated']))
    conn.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='romstore.cli', add_help=True)
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--systems', action='store_true')
    ap.add_argument('--list', metavar='NAS_DIR')
    ap.add_argument('--search', metavar='KW')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--detail', type=int, metavar='GID')
    ap.add_argument('--install', type=int, metavar='GID')
    ap.add_argument('--remove', type=int, metavar='GID')
    ap.add_argument('--purge', action='store_true', help='卸载时直接删除，不进回收站')
    ap.add_argument('--register', action='store_true', help='注册 ES 入口到 Options 菜单')
    ap.add_argument('--unregister', action='store_true', help='撤销 ES 入口注册')
    ap.add_argument('--source-show', action='store_true', help='显示商店源配置与挂载状态')
    ap.add_argument('--source-set', metavar='HOST,SHARE[,USER[,PASS]]')
    ap.add_argument('--source-reset', action='store_true', help='恢复默认商店源')
    ap.add_argument('--scan-hosts', nargs='?', const='', metavar='SUBNET',
                    help='扫描开 445 的主机（默认取本机网段）')
    ap.add_argument('--probe-host', metavar='HOST')
    ap.add_argument('--mount', action='store_true', help='按当前源挂载')
    ap.add_argument('--umount', action='store_true', help='卸载挂载点')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--page', type=int, default=1)
    ap.add_argument('--size', type=int, default=30)
    a = ap.parse_args(argv)

    if a.validate:
        return cmd_validate()
    if a.build:
        return cmd_build(a.force)
    if a.stats:
        return cmd_stats()
    if a.systems:
        return cmd_systems()
    if a.list:
        return cmd_list(a.list, a.page, a.size)
    if a.search:
        return cmd_search(a.search, a.limit)
    if a.detail is not None:
        return cmd_detail(a.detail)
    if a.install is not None:
        return cmd_install(a.install, a.dry_run)
    if a.remove is not None:
        return cmd_remove(a.remove, a.purge, a.dry_run)
    if a.register:
        return cmd_register(False)
    if a.unregister:
        return cmd_register(True)
    if a.source_show:
        return cmd_source_show()
    if a.source_set is not None:
        return cmd_source_set(a.source_set)
    if a.source_reset:
        return cmd_source_reset()
    if a.scan_hosts is not None:
        return cmd_scan_hosts(a.scan_hosts)
    if a.probe_host:
        return cmd_probe_host(a.probe_host)
    if a.mount:
        return cmd_mount(a.force)
    if a.umount:
        return cmd_umount()
    ap.print_help()
    return 0


# ---------------------------------------------------------------- 商店源
def cmd_source_show():
    cfg = src.load()
    print('配置文件   : %s  %s'
          % (src.SOURCE_PATH,
             '存在' if os.path.exists(src.SOURCE_PATH) else '不存在（回落默认值）'))
    print('共享       : %s' % src.unc_path(cfg))
    print('账号       : %s' % (cfg['user'] or '（匿名 guest）'))
    print('密码       : %s' % ('*' * len(cfg['password']) if cfg['password'] else '（无）'))
    print('vers       : %s' % cfg['vers'])
    print('挂载点     : %s' % src.MOUNT_POINT)
    print('挂载状态   : %s' % src.status_text(cfg))
    print('挂载选项   : %s' % src.mount_opts(cfg))
    print('挂载脚本   : %s  %s'
          % (src.MOUNT_SCRIPT,
             '存在' if os.path.exists(src.MOUNT_SCRIPT) else '缺失'))
    _ok, why = src.probe_host(cfg['host'])
    print('主机探活   : %s' % why)
    return 0


def cmd_source_set(vals):
    cfg = src.load()
    parts = [p.strip() for p in (vals or '').split(',')]
    if parts and parts[0]:
        cfg['host'] = parts[0]
    if len(parts) > 1 and parts[1]:
        cfg['share'] = parts[1]
    if len(parts) > 2:
        cfg['user'] = parts[2]
    if len(parts) > 3:
        cfg['password'] = parts[3]
    cfg = src.save(cfg)
    print('已写入 %s' % src.SOURCE_PATH)
    print('  共享 = %s' % src.unc_path(cfg))
    print('  账号 = %s' % (cfg['user'] or '（匿名）'))
    return 0


def cmd_source_reset():
    cfg = src.save(src.defaults())
    print('已恢复默认源: %s' % src.unc_path(cfg))
    return 0


def cmd_scan_hosts(subnet):
    print('本机地址   : %s' % (src.local_ip() or '未知'))
    sub = subnet or src.local_subnet()
    print('扫描网段   : %s.* : %d' % (sub or '?', src.SMB_PORT))
    last = [0]

    def prog(cur, total, _ip):
        if cur - last[0] >= 40 or cur == total:
            last[0] = cur
            sys.stderr.write('\r  已探测 %d/%d' % (cur, total))
            sys.stderr.flush()

    t0 = time.time()
    hs = src.scan_hosts(subnet=sub, progress=prog)
    sys.stderr.write('\n')
    print('耗时 %.2f 秒，发现 %d 台开放 %d 的主机：'
          % (time.time() - t0, len(hs), src.SMB_PORT))
    for h in hs:
        print('  %s' % h)
    return 0


def cmd_probe_host(host):
    ok, why = src.probe_host(host)
    print(why)
    return 0 if ok else 1


def cmd_mount(force):
    cfg = src.load()
    print('挂载 %s -> %s%s'
          % (src.unc_path(cfg), src.MOUNT_POINT, '（--force 重挂）' if force else ''))
    t0 = time.time()
    r = src.mount(cfg, force=force)
    print('耗时 %.2f 秒' % (time.time() - t0))
    if r.get('ok'):
        print('成功: %s (%s)' % (r.get('unc'), r.get('fstype')))
        if r.get('output'):
            print('  脚本输出: %s' % r['output'].replace('\n', ' | '))
        return 0
    print('失败: %s' % r.get('error'))
    if r.get('hint'):
        print('  提示: %s' % r['hint'])
    if r.get('output'):
        print('  --- 脚本原始输出 ---')
        for ln in r['output'].splitlines():
            print('    %s' % ln)
    return 1


def cmd_umount():
    print('卸载 %s' % src.MOUNT_POINT)
    r = src.unmount()
    if r.get('ok'):
        print(r.get('msg') or '已卸载')
        return 0
    print('失败: %s' % r.get('error'))
    return 1


def cmd_remove(gid, purge, dry):
    conn = idx.connect()
    g = idx.get_game(conn, gid)
    if not g:
        print('没有 id=%s 的游戏' % gid)
        conn.close()
        return 1
    print('卸载 #%d %s（%s）' % (g['id'], g['title'], g['es_path']))
    r = inst.remove(conn, g, to_trash=not purge, dry_run=dry)
    if not r['ok']:
        print('  失败: %s' % r.get('error'))
    elif r.get('nothing'):
        print('  本地没有这个文件（未安装），已清理可能的 gamelist 残留条目')
    else:
        print('  移动 %d 个文件 -> %s' % (len(r['moved']), r['trash'] or '已直接删除'))
        for p in r['moved']:
            print('     %s' % p)
        print('  gamelist 条目已移除: %s' % (r['rom_name'] or '-'))
    conn.close()
    return 0


def cmd_register(undo=False):
    """把入口注册进 EmulationStation 的 options 系统游戏列表（显示中文名）

    路径必须是 './RomStore.sh' 而不是 './Tools/RomStore.sh'：
    es_systems.cfg 里 options 系统的 <path> 是 /opt/system/，
    RomStore.sh 就直接放在 /opt/system/ 下（rootfs，换 SD 卡不丢）。
    """
    es_path = config.ES_OPTIONS_DIR
    gl = os.path.join(es_path, 'gamelist.xml')
    entry = {'rom_name': 'RomStore.sh', 'title': 'ROM 商店',
             'desc': '从局域网 NAS 浏览并安装游戏到本机'}
    bak = gl + '.romstore.bak'
    if not os.path.exists(bak) and os.path.exists(gl):
        import shutil
        shutil.copy2(gl, bak)
        print('已备份原 gamelist: %s' % bak)

    if undo:
        if not os.path.isfile(gl):
            print('没有 gamelist.xml')
            return 0
        with open(gl, 'rb') as f:
            raw = f.read()
        bom = raw.startswith(b'\xef\xbb\xbf')
        text = raw.decode('utf-8-sig', 'replace')
        hit = inst._find_block(text, entry['rom_name'])
        if not hit:
            print('未找到已注册的条目，无需撤销')
            return 0
        s, en, _b = hit
        while s > 0 and text[s - 1] in ' \t':
            s -= 1
        new = text[:s] + text[en:]
        with open(gl, 'wb') as f:
            if bom:
                f.write(b'\xef\xbb\xbf')
            f.write(new.encode('utf-8'))
        print('已撤销注册条目')
        return 0

    added, updated, path = inst.upsert_gamelist(es_path, [entry])
    print('注册完成: %s（新增 %d / 更新 %d）' % (path, added, updated))
    with open(path, 'rb') as f:
        print(f.read().decode('utf-8-sig'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
