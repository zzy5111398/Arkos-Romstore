#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/make-release.py —— 建 tag 并发布 GitHub Release（走 REST API）

为什么不用 `git push --tags` + `gh release create`：
本机到 github.com 的 git/https 协议时好时坏（代理 CONNECT 返回 502），
但 api.github.com 稳定。所以打 tag、建 release、传附件全部走 REST API。

用法
----
    python tools/make-release.py                 # 交互式：自动挑包，问几个问题
    python tools/make-release.py --yes           # 全用默认值，直接发
    python tools/make-release.py --dry-run       # 只打印要做什么，不改远端
    python tools/make-release.py --tag v0.4.2 --name "..." --notes notes.md

要点（都是踩过的坑）
--------------------
1. tag 要用 **带注释的 tag 对象**，不是轻量 tag。走 API 建 release 时，
   `target_commitish` 只是分支名，GitHub 会自己找 commit —— 所以本地
   `git tag -a` 只是为了让 `git describe` / 本地历史好看，真正生效的是
   API 里 POST /git/refs 传的 sha。
2. 建 ref 前必须先确认远端没有同名 tag，否则 422。
3. 附件要先删同名 asset 再传，否则 GitHub 返回 422 duplicate。
   （同版本反复重打时必踩）
4. release 创建后**不能改 tag 指向**，只能删了重建。所以 tag 要挑对。
5. 上传大文件用 `Content-Type: application/octet-stream`，不要 multipart ——
   分片实现容易被代理截断。
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.github.com'
UPLOAD = 'https://uploads.github.com'
REPO = 'zzy5111398/Arkos-Romstore'
GCM = (r'C:\Users\admin\.workbuddy\binaries\PortableGit\versions\1.2.0'
       r'\mingw64\bin\git-credential-manager.exe')
ROOT = pathlib.Path(__file__).resolve().parent.parent


def token():
    p = subprocess.run([GCM, 'get'], input='protocol=https\nhost=github.com\n\n',
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith('password='):
            return line.split('=', 1)[1].strip()
    raise SystemExit('取不到 GitHub token')


TOKEN = token()


def api(method, path, body=None, raw=None, ctype='application/json'):
    # ⚠️ 上传附件走 uploads.github.com，不是 api.github.com ——
    # 用 api.github.com 上传会 404 "Not Found"，很容易误判成权限问题。
    #
    # 只有「带 asset id 的上传」才换域：
    #   POST   /releases/{id}/assets?name=..   -> uploads
    #   GET    /releases/{id}/assets           -> api   （这是列表，不能换域）
    # 早期用 `'/assets' in path` 判断太宽，把列表查询也劫持到 uploads 域，
    # 结果 GET 返回 400 "Invalid name for request"。所以必须同时看 method 和 raw。
    is_upload = (method == 'POST' and raw is not None)
    base = UPLOAD if is_upload else API
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + TOKEN)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    req.add_header('User-Agent', 'romstore-release-tool')
    if data is not None:
        req.add_header('Content-Type', ctype)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:600]
        raise SystemExit('API %s %s 失败：HTTP %s\n%s' % (method, path, e.code, detail))
    except urllib.error.URLError as e:
        raise SystemExit('网络错误：%s\n（api.github.com 也不通？稍后重试）' % e.reason)


def git(*a):
    return subprocess.run(['git'] + list(a), cwd=str(ROOT),
                          capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout


def find_assets(version):
    """找打包产物。make-dist.py 输出到项目**上一级**目录。"""
    parent = ROOT.parent
    found = []
    for pat in ('ark-romstore-%s.tar.gz' % version,
                'ark-romstore-%s.zip' % version):
        p = parent / pat
        if p.is_file():
            found.append(p)
    sums = parent / 'SHA256SUMS'
    if sums.is_file():
        found.append(sums)
    return found


def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag')
    ap.add_argument('--name')
    ap.add_argument('--notes')
    ap.add_argument('--notes-file', default='RELEASE-NOTES.md')
    ap.add_argument('--draft', action='store_true')
    ap.add_argument('--prerelease', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    tag = args.tag or ('v' + version)
    head = git('rev-parse', 'HEAD').strip()

    # 远端 main 未必等于本地 HEAD —— 上一次可能也走了 API 推送，
    # 那时两边 tree 相同但 commit hash 不同。
    # tag 必须指向**远端真实存在**的那个 commit，否则
    # "Published releases must have a valid tag" 会 422。
    remote_main = api('GET', '/repos/%s/git/ref/heads/main'
                      % REPO)['object']['sha']
    if remote_main == head:
        target = head
    else:
        local_tree = git('rev-parse', 'HEAD^{tree}').strip()
        remote_tree = api('GET', '/repos/%s/git/commits/%s'
                          % (REPO, remote_main))['tree']['sha']
        if local_tree == remote_tree:
            print('注意：本地 HEAD 与远端 main 哈希不同但内容一致'
                  '（历史上用 API 推送所致），tag 将指向远端 commit')
            target = remote_main
        else:
            raise SystemExit(
                '本地与远端内容不同，拒绝发布。\n'
                '  本地 %s / 远端 %s\n'
                '  请先 git fetch 并确认要发布哪一边。'
                % (local_tree[:12], remote_tree[:12]))

    print('=' * 62)
    print(' 版本   : %s' % version)
    print(' tag    : %s' % tag)
    print(' commit : %s' % target[:12] + ('  (远端)' if target != head else ''))
    print('=' * 62)

    # ---------------------------------------------------------- 现状检查
    print('\n=== 1) 检查远端现状 ===')
    existing = None
    try:
        existing = api('GET', '/repos/%s/releases/tags/%s' % (REPO, tag))
        print('  ⚠️ 远端已存在同名 release：%s' % existing.get('html_url'))
    except SystemExit:
        pass  # 404 = 没有，是正常路径
    print('  远端没有 %s 这个 release' % tag if not existing else '')

    assets = find_assets(version)
    if not assets:
        raise SystemExit('找不到打包产物。先运行 python3 make-dist.py'
                         '（产物在项目上一级目录）')
    print('\n=== 2) 待上传附件 ===')
    for a in assets:
        print('  %-34s %8.1f KB' % (a.name, a.stat().st_size / 1024))

    if args.dry_run:
        print('\n[dry-run] 到此为止，未改动远端')
        return

    # ---------------------------------------------------------- 本地 tag
    print('\n=== 3) 打本地 tag（让 git describe / 历史好看）===')
    local_target = head if target == head else None
    if local_target is None:
        print('  跳过本地 tag：该 commit 只存在于远端（API 推送所致）')
        r = None
    else:
        r = subprocess.run(['git', 'tag', '-a', tag, '-m',
                            'release %s' % version, local_target],
                           cwd=str(ROOT), capture_output=True, text=True)
    if r is not None:
        if r.returncode == 0:
            print('  已创建带注释 tag %s' % tag)
        else:
            print('  跳过：%s' % (r.stderr.strip() or '已存在'))

    # ---------------------------------------------------------- 远端 ref
    print('\n=== 4) 在 GitHub 上创建 tag ref ===')
    # 注意：这步失败必须**原样抛出**，不能吞。
    # 之前用 try/except SystemExit 一律打「已存在，跳过」，
    # 结果把真实的 422/500 当成「已存在」误报，本地却以为成功了。
    # 先 GET 确认，再 POST 才是对的顺序。
    try:
        api('GET', '/repos/%s/git/ref/tags/%s' % (REPO, tag))
        print('  tag ref 已存在，跳过创建')
    except SystemExit:
        api('POST', '/repos/%s/git/refs' % REPO,
            {'ref': 'refs/tags/%s' % tag, 'sha': target})
        print('  refs/tags/%s -> %s' % (tag, target[:12]))

    # ---------------------------------------------------------- release
    notes_path = ROOT / args.notes_file
    notes = args.notes or (notes_path.read_text(encoding='utf-8')
                           if notes_path.is_file() else '')
    name = args.name or ('ark-romstore %s' % version)

    print('\n=== 5) 创建 release ===')
    # target_commitish 只能是**分支名或已存在的 tag 名**，不能是 SHA ——
    # 传 SHA 会 422 "target_commitish invalid"。
    # tag ref 上一步已经建好，这里给 tag 名即可。
    payload = {'tag_name': tag, 'name': name, 'body': notes,
               'draft': args.draft, 'prerelease': args.prerelease}
    if existing:
        # 已存在就更新正文与开关，不动 tag
        rel = api('PATCH', '/repos/%s/releases/%s' % (REPO, existing['id']),
                  {'name': name, 'body': notes,
                   'draft': args.draft, 'prerelease': args.prerelease})
        print('  已更新现有 release：%s' % rel['html_url'])
    else:
        rel = api('POST', '/repos/%s/releases' % REPO, payload)
        print('  已创建：%s' % rel['html_url'])

    # ---------------------------------------------------------- 附件
    print('\n=== 6) 上传附件 ===')
    old_assets = api('GET', '/repos/%s/releases/%s/assets' % (REPO, rel['id']))
    have = {a['name']: a['id'] for a in old_assets}
    for p in assets:
        if p.name in have:
            print('  删除同名旧附件 %s' % p.name)
            api('DELETE', '/repos/%s/releases/assets/%s' % (REPO, have[p.name]))
        print('  上传 %-34s sha256=%s' % (p.name, sha256_of(p)[:16]))
        # name 必须放查询参数里；放请求头或 body 都会被忽略，
        # 结果附件名变成 asset 或空，下载时很难认。
        api('POST', '/repos/%s/releases/%s/assets?name=%s'
            % (REPO, rel['id'], urllib.parse.quote(p.name)),
            raw=p.read_bytes(), ctype='application/octet-stream')

    print('\n=== 完成 ===')
    print('  %s' % rel['html_url'])
    print('  tag    %s  ->  %s' % (tag, target[:12]))
    print('  附件   %d 个' % len(assets))
    if args.draft:
        print('  状态   draft（需在网页上点 Publish）')


if __name__ == '__main__':
    main()