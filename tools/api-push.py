#!/usr/bin/env python3
"""在 github.com:443 不通、但 api.github.com 通的机器上，用 GitHub REST API 推送分支。

背景：本机 DNS（OrayBox 10.168.1.1）把 github.com 解析到 20.205.243.166，
那个地址 TCP 443 连不上，而同一网段的 api.github.com（.168）正常。
所以 git 协议走不通，只能把 git 对象用 REST API 逐个建上去。

流程（全部走 api.github.com）：
  1. 建 blob（每个文件一个）
  2. 建 tree（复用远端父 tree 的其余条目，只换本次改动的路径）
  3. 建 commit（parent = 远端当前 HEAD）
  4. PATCH ref

只在「本地领先远端一个提交」这种简单线性历史时用它；历史分叉时会失败。
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = 'https://api.github.com'
REPO = 'zzy5111398/Arkos-Romstore'
BRANCH = 'main'
GCM = r'C:\Users\admin\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin\git-credential-manager.exe'


def token():
    p = subprocess.run([GCM, 'get'], input='protocol=https\nhost=github.com\n\n',
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith('password='):
            return line.split('=', 1)[1].strip()
    raise SystemExit('取不到 GitHub token')


TOKEN = token()


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + TOKEN)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    req.add_header('User-Agent', 'ark-romstore-apipush')
    if data:
        req.add_header('Content-Type', 'application/json')
    # 关键：代理在这个环境里对 GitHub 返回 502，这里显式不走代理
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as r:
            return json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:500]
        raise SystemExit('API %s %s 失败 HTTP %s: %s' % (method, path, e.code, detail))


def git(*args, binary=False):
    r = subprocess.run(['git'] + list(args), capture_output=True)
    if r.returncode != 0:
        raise SystemExit('git %s 失败: %s' % (' '.join(args),
                                             r.stderr.decode('utf-8', 'replace')[:400]))
    return r.stdout if binary else r.stdout.decode('utf-8')


def main():
    branch_sha = git('rev-parse', 'HEAD').strip()
    remote_sha = api('GET', '/repos/%s/git/ref/heads/%s' % (REPO, BRANCH))['object']['sha']
    print('本地 HEAD   : %s' % branch_sha[:12])
    print('远端 %-6s: %s' % (BRANCH, remote_sha[:12]))
    if remote_sha == branch_sha:
        print('远端已是最新，无需推送')
        return

    local_tree = git('rev-parse', branch_sha + '^{tree}').strip()
    remote_tree_sha = api('GET', '/repos/%s/git/commits/%s'
                          % (REPO, remote_sha))['tree']['sha']
    print('本地 tree   : %s' % local_tree[:12])
    print('远端 tree   : %s' % remote_tree_sha[:12])
    if local_tree == remote_tree_sha:
        print('内容相同但提交不同（多半是上一次 API 推送留下的）—— 直接把 ref 对齐到本地')
        api('PATCH', '/repos/%s/git/refs/heads/%s' % (REPO, BRANCH),
            {'sha': branch_sha, 'force': True})
        print('已对齐，远端 HEAD = %s' % branch_sha[:12])
        print('\n注意：这是强推（force）。若别人已基于旧提交开发，其历史会被改写。')
        return

    msg = git('log', '-1', '--format=%B', branch_sha)
    aname, aemail = git('log', '-1', '--format=%an%x00%ae', branch_sha).strip().split('\x00')
    when = git('log', '-1', '--format=%aI', branch_sha).strip()

    # 远端那条提交的内容 = 远端 tree；要把它变成本地 HEAD 的内容，
    # 就是把「远端有、本地没有的路径」删掉、「本地有内容变化的」替换掉。
    # 因为拿不到远端提交对象（不在本地库里），只能用 tree 逐条比对。
    print('\n=== 0) 比对远端与本地的文件清单 ===')
    rt = api('GET', '/repos/%s/git/trees/%s?recursive=1' % (REPO, remote_tree_sha))
    remote_files = {e['path']: e for e in rt['tree'] if e['type'] == 'blob'}
    local_files = [f for f in git('ls-tree', '-r', '--name-only', branch_sha).split('\n')
                   if f.strip()]
    local_set = set(local_files)
    to_delete = [p for p in remote_files if p not in local_set]
    changed = [p for p in local_files
               if p not in remote_files
               or git('rev-parse', '%s:%s' % (branch_sha, p)).strip()
               != remote_files[p]['sha']]
    print('  远端 %d 个文件 / 本地 %d 个文件' % (len(remote_files), len(local_files)))
    print('  需删除 %d 个、需更新 %d 个' % (len(to_delete), len(changed)))

    print('\n=== 1) 建 blob ===')
    entries = []
    for path in changed:
        blob = git('cat-file', '-p', branch_sha + ':' + path, binary=True)
        r = api('POST', '/repos/%s/git/blobs' % REPO, {
            'content': base64.b64encode(blob).decode(),
            'encoding': 'base64'})
        # 权限位必须跟 git 里一致，否则 .sh 会从 755 掉成 644
        mode = git('ls-tree', branch_sha, '--', path).split()[0]
        entries.append({'path': path, 'mode': mode, 'type': 'blob',
                        'sha': r['sha']})
        print('  %-28s %s -> %s' % (path, mode, r['sha'][:12]))

    print('\n=== 2) 建 tree ===')
    # 用远端 tree 当 base，只提交「更新的路径」+「显式要删的路径」两类条目。
    #
    # ⚠️ 删除必须靠 {"path": p, "mode": "100644", "type": "blob", "sha": null}
    #    显式声明 —— tree API 的语义是「不列的路径一律从 base_tree 继承」，
    #    只把删掉的路径从提交列表里去掉，文件会原封不动留在远端。
    tree_entries = list(entries)
    for path in to_delete:
        tree_entries.append({'path': path, 'mode': '100644',
                             'type': 'blob', 'sha': None})
        print('  删除 %s（显式 sha=null）' % path)
    print('  远端 %d 个文件，本次提交 %d 个条目' % (len(remote_files),
                                              len(tree_entries)))
    tree = api('POST', '/repos/%s/git/trees' % REPO, {'base_tree': remote_tree_sha,
                                                     'tree': tree_entries})
    print('  tree sha = %s' % tree['sha'][:12])
    if tree['sha'] != local_tree:
        print('  ⚠️ 生成的 tree 与本地 tree 不一致！')
        print('     本地 %s' % local_tree)
        raise SystemExit('tree 不匹配，拒绝推送（否则远端内容会与本地不同）')

    print('\n=== 3) 建 commit ===')
    c = api('POST', '/repos/%s/git/commits' % REPO, {
        'message': msg,
        'tree': tree['sha'],
        'parents': [remote_sha],
        'author': {'name': aname, 'email': aemail, 'date': when},
        'committer': {'name': aname, 'email': aemail, 'date': when}})
    print('  commit sha = %s' % c['sha'][:12])

    print('\n=== 4) 更新 ref ===')
    api('PATCH', '/repos/%s/git/refs/heads/%s' % (REPO, BRANCH),
        {'sha': c['sha'], 'force': False})
    print('  %s -> %s' % (BRANCH, c['sha'][:12]))
    print('\n推送完成。新提交 %s' % c['sha'])


if __name__ == '__main__':
    main()