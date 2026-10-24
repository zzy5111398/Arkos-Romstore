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
  python3 -m romstore.cli --gaps                    哪些系统缺封面
  python3 -m romstore.cli --import-media GBA [--dry-run] [--media-limit N]
                                                    批量补某个系统的封面

商店源（NAS 共享）：
  python3 -m romstore.cli --source-show          当前源配置 + 挂载状态
  python3 -m romstore.cli --source-set 10.168.1.110,Roms[,账号[,密码]]
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
from . import batchmedia
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


def cmd_install(gid, dry, media=True):
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

    r = inst.install(conn, g, progress=cb, dry_run=dry, copy_media=media)
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
        art = r.get('art') or {}
        if art.get('ok'):
            print('  封面: %s (%s)' % (r.get('image'), inst.human(art.get('bytes'))))
        elif media:
            print('  封面: 未导入（%s）' % (art.get('reason') or '未知原因'))
        print('  gamelist: %s（新增 %d / 更新 %d）'
              % (r['gamelist'], r['gamelist_added'], r['gamelist_updated']))
    conn.close()
    return 0


def cmd_gaps():
    """体检：哪些系统已经装了游戏、但掌机上缺封面"""
    rows = batchmedia.systems_with_gaps()
    if not rows:
        print('没有发现缺封面的系统（或 NAS 未挂载）')
        return 0
    print('%-14s %-16s %8s %8s %8s %10s' %
          ('NAS目录', 'ES名', '已装', '缺图', '已有', '需传输'))
    tot_b = 0
    for r in rows:
        tot_b += r['bytes']
        print('%-14s %-16s %8d %8d %8d %10s' %
              (r['nas_dir'], r['es_name'], r['installed'],
               r['missing'], r['have'], inst.human(r['bytes'])))
    print('-' * 70)
    print('合计 %d 个系统，需传输 %s' % (len(rows), inst.human(tot_b)))
    print('（用 --import-media <NAS目录> 逐个补）')
    return 0


def cmd_import_media(nas_dir, dry, limit, update_gamelist=True):
    print('批量导入封面: %s' % nas_dir)
    t0 = time.time()
    last = ['']

    def cb(phase, done, total, msg):
        s = '\r  %-9s %d/%d  %s' % (phase, done, total, msg[:30])
        if s != last[0]:
            sys.stderr.write(s)
            sys.stderr.flush()
            last[0] = s

    r = batchmedia.run(nas_dir, progress=cb, dry_run=dry, limit=limit,
                       update_gamelist=update_gamelist)
    sys.stderr.write('\n')
    if not r['ok']:
        print('失败: %s' % r['error'])
        return 1
    if r.get('dry_run'):
        p = r['plan']
        print('干跑：ES 目录 %s' % p['es_path'])
        print('  布局: %s / %s' % (p['media_dir'], p['layout']))
        print('  待导入 %d 个 / %s（跳过已有 %d，无封面 %d）'
              % (r['todo'], inst.human(r['bytes']), p['counts']['skip'],
                 p['counts']['nocover']))
        if p['items']:
            ex = next((i for i in p['items'] if i['action'] == 'copy'), None)
            if ex:
                print('  样例: %s' % ex['title'])
                print('    %s' % ex['src'])
                print('    -> %s' % ex['dst'])
                print('    gamelist: <image>%s</image>' % ex['gl_path'])
        print('  gamelist: %s' % ('会更新' if r['will_gamelist'] else '不动'))
        return 0
    print('完成：耗时 %.1f 秒' % (time.time() - t0))
    print('  导入 %d 个封面 / %s' % (r['copied'], inst.human(r['bytes'])))
    print('  跳过已有 %d 个，NAS 无封面 %d 个，失败 %d 个'
          % (r['skipped'], r['nocover'], r['failed']))
    print('  布局: %s / %s' % (r['media_dir'], r['layout']))
    print('  gamelist: 新增 %d / 更新 %d' % (r['gamelist_added'],
                                             r['gamelist_updated']))
    if r.get('case_fixed'):
        print('  顺带修正大小写路径 %d 条（NAS 与掌机文件名大小写不一致）'
              % r['case_fixed'])
    return 0


def cmd_covers(nas_dir, check_content, sample, verbose):
    """封面三层体检：引用层 / 内容层 / 生效层"""
    import sys as _sys
    last = ['']
    multi = not nas_dir

    def _cb(done, total, name):
        if not multi:
            return
        s = '\r  体检中 %d/%d  %-28s' % (done, total, name[:28])
        if s != last[0]:
            _sys.stderr.write(s)
            _sys.stderr.flush()
            last[0] = s

    r = batchmedia.audit(nas_dir, check_content=check_content,
                         content_sample=sample, progress=_cb)
    if multi and last[0]:
        _sys.stderr.write('\n')
    if r.get('error'):
        print('失败: %s' % r['error'])
        return 1
    rows = r['systems']
    if not rows:
        print('没有可检查的系统（NAS 挂了吗？gamelist 都没有？）')
        return 1
    print('%-12s %-14s %7s %7s %7s %7s %7s' %
          ('NAS目录', 'ES名', '游戏', '图引用', '存在', '缺失', '内容坏'))
    print('-' * 66)
    for s in rows:
        print('%-12s %-14s %7d %7d %7d %7d %7d' %
              (s['nas_dir'], s['es_name'], s['games'], s['img_refs'],
               s['img_ok'], s['img_missing'], s['bad_content']))
    t = r['totals']
    print('-' * 66)
    print('合计 %d 个系统 / %d 个 game：图引用 %d，存在 %d，缺失 %d，内容坏 %d'
          % (t['systems'], t['games'], t['img_refs'], t['img_ok'],
             t['img_missing'], t['bad_content']))
    if t['img_missing'] == 0 and t['bad_content'] == 0:
        print('✅ 引用层与内容层全部正常')
    else:
        print('⚠️  有缺失或内容损坏，见下')

    if verbose:
        for s in rows:
            if s['broken']:
                print()
                print('[%s] 缺失样例:' % s['nas_dir'])
                for b in s['broken']:
                    print('  游戏 %s' % b['rom'])
                    print('    引用 %s' % b['image'])

    # 生效层
    es = r.get('es') or {}
    print()
    print('— ES 生效层 —')
    if es.get('exists'):
        import time as _t
        if es.get('proc_start'):
            print('  ES 进程启动:          %s'
                  % _t.strftime('%Y-%m-%d %H:%M:%S',
                                _t.localtime(es['proc_start'])))
        print('  imagecache.db 最后更新: %s'
              % _t.strftime('%Y-%m-%d %H:%M:%S',
                            _t.localtime(es.get('mtime') or 0)))
        print('  缓存条目 %d（其中已解码 %d）'
              % (es.get('entries', 0), es.get('decoded', 0)))
        if es.get('gamelist_newest'):
            print('  gamelist 最后更新:    %s'
                  % _t.strftime('%Y-%m-%d %H:%M:%S',
                                _t.localtime(es['gamelist_newest'])))
        if es.get('stale'):
            print('  ⚠️  gamelist 比 ES 重读时间新 —— 封面不会显示！')
            if config.PLATFORM == 'rocknix':
                # ROCKNIX：essway.service 带 Restart=always，杀掉即自动拉起
                print('     修：pkill emulationstatio'
                      '   （只写 15 个字母，pkill 比的是 comm 名）')
            else:
                print('     修：touch /tmp/es-restart && kill $(pidof emulationstation)')
        else:
            print('  ✅ ES 已在 gamelist 修改后重读过（启动时间/缓存不旧）')
        print('  注：imagecache 只缓存「当前浏览到」的系统，条目少属正常')
    else:
        print('  没找到 %s（这台不是掌机？或 ES 没跑过？）' % es.get('cache_path'))
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
    ap.add_argument('--import-media', metavar='NAS_DIR',
                    help='批量补某个系统已装游戏的封面')
    ap.add_argument('--media-limit', type=int, default=0, metavar='N',
                    help='配合 --import-media：只导前 N 个（0 = 不限，默认）')
    ap.add_argument('--no-gamelist', action='store_true',
                    help='配合 --import-media：只搬图，不改 gamelist')
    ap.add_argument('--gaps', action='store_true',
                    help='列出缺封面的系统')
    ap.add_argument('--covers', nargs='?', const='', metavar='NAS_DIR',
                    help='封面三层体检（引用/内容/生效）；可指定单个 NAS 目录')
    ap.add_argument('--check-content', action='store_true',
                    help='配合 --covers：查图片文件头，抓 exFAT 截断留下的坏图')
    ap.add_argument('--cover-sample', type=int, default=0, metavar='N',
                    help='配合 --covers --check-content：每系统抽查 N 个，0=全查')
    ap.add_argument('--verbose', action='store_true',
                    help='配合 --covers：打印缺失样例')
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
    ap.add_argument('--no-media', action='store_true',
                    help='只装 ROM，不导入封面（封面缺失/想省空间时用）')
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
        return cmd_install(a.install, a.dry_run, media=not a.no_media)
    if a.gaps:
        return cmd_gaps()
    if a.covers is not None:
        return cmd_covers(a.covers or None, a.check_content,
                          a.cover_sample, a.verbose)
    if a.import_media is not None:
        return cmd_import_media(a.import_media, a.dry_run, a.media_limit or None,
                                update_gamelist=not a.no_gamelist)
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
    print('挂载选项   : %s' % src.mount_opts(cfg, mask=True))
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
        art = r.get('art_moved') or []
        if art:
            print('  清理 %d 个封面 -> %s' % (len(art), r['trash'] or '已直接删除'))
            for p in art:
                print('     %s' % p)
        print('  gamelist 条目已移除: %s' % (r['rom_name'] or '-'))
    conn.close()
    return 0


def cmd_register(undo=False):
    """把入口注册进 EmulationStation 的游戏列表（显示中文名）

    入口目录与所在系统随平台而变（都取自平台档案，别再写死）：

      ArkOS    options 系统，<path> = /opt/system/          （在 rootfs）
      ROCKNIX  ports   系统，<path> = /storage/roms/ports/  （在 /storage）

    两边都要求 gamelist 里的 path 是 './RomStore.sh'（即与入口脚本同目录），
    不要写成 './Tools/RomStore.sh' —— 那会让 ES 找不到文件。

    注：0.4.2 曾短暂加过一个独立的 VideoPlayer.sh 入口，已撤掉 ——
    播放预览视频改在商店详情页里（见 ui.request_play 与 RomStore.sh 的接力循环）。
    entries 保留成列表是为了以后加入口时不用改撤销逻辑。
    """
    es_path = config.ES_OPTIONS_DIR
    gl = os.path.join(es_path, 'gamelist.xml')

    entries = [
        {'rom_name': 'RomStore.sh', 'title': 'ROM 商店',
         'desc': '从局域网 NAS 浏览并安装游戏到本机'},
    ]

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
        removed = 0
        for entry in entries:
            hit = inst._find_block(text, entry['rom_name'])
            if not hit:
                continue
            s, en, _b = hit
            while s > 0 and text[s - 1] in ' \t':
                s -= 1
            # 连块前面那个换行一起删掉。
            # 只回退缩进是不够的：块前的 '\n' 会原地留成一行空行，而
            # register 是把条目插在 '</gameList>' 前面 —— 于是每跑一次
            # install/uninstall 往返，gamelist 里就多攒一个空行。
            # 真机往返实测：第一次卸载就多一个空行，md5 与原文件不再一致
            # （语义无害，但破坏了"往返后字节复原"这条承诺）。
            if s > 0 and text[s - 1] == '\n':
                s -= 1
                if s > 0 and text[s - 1] == '\r':
                    s -= 1
            text = text[:s] + text[en:]
            removed += 1
        if not removed:
            print('未找到已注册的条目，无需撤销')
            return 0
        with open(gl, 'wb') as f:
            if bom:
                f.write(b'\xef\xbb\xbf')
            f.write(text.encode('utf-8'))
        print('已撤销 %d 个注册条目' % removed)
        return 0

    added, updated, path = inst.upsert_gamelist(es_path, entries)
    print('注册完成: %s（新增 %d / 更新 %d）' % (path, added, updated))
    with open(path, 'rb') as f:
        print(f.read().decode('utf-8-sig'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
