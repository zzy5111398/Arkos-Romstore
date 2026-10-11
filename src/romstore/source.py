# -*- coding: utf-8 -*-
"""商店源（NAS 共享）：配置读写 + 局域网扫描 + 挂载/卸载

职责边界：
  1. ``source.json`` 是**全系统唯一的一份商店源真相**
     * 商店客户端读它来知道去哪挂载；
     * 开机挂载脚本 /usr/local/sbin/nas-roms-mount.sh **也读它**，
       所以「改了商店源」和「开机自动挂载」永远指向同一个共享，不会各说各话。
     * 文件不存在 / 损坏时，脚本回落到内置默认值 —— 保证开机挂载
       绝不会因为配置缺失而失败（这是硬要求，见 nas-roms-mount.sh 注释）。
  2. 局域网扫描：找出开了 445 的主机。掌机没有键盘，这是最主要的输入方式。
  3. 挂载/卸载：**只调用 nas-roms-mount.sh**，复用同一把 flock
     （/run/nas-roms.lock），避免和 systemd / NetworkManager dispatcher 抢锁。

实测约束（改之前先看这里）：
  * 内核 cifs 只认 vers=2.1；vers=2.0 返回 EOPNOTSUPP(95)
  * ark 用户 uid/gid = 1002
  * 挂载点固定 /mnt/Roms —— es_systems.cfg 里所有 <path> 都指向它，改了全盘对不上
  * cifs 的 -o 是逗号分隔，所以账号/密码里**不能有逗号**（软键盘也不提供逗号）
"""
import json
import os
import re
import socket
import subprocess
import tempfile
import threading

from . import config

SOURCE_PATH = os.environ.get('ROMSTORE_SOURCE',
                             os.path.join(config.STORE_DIR, 'source.json'))
MOUNT_SCRIPT = os.environ.get('ROMSTORE_MOUNT_SCRIPT',
                              '/usr/local/sbin/nas-roms-mount.sh')
MOUNT_POINT = os.environ.get('ROMSTORE_MOUNT', config.NAS_ROOT)
LOCK_PATH = '/run/nas-roms.lock'

SMB_PORT = 445
HOST_DEFAULT = '10.168.1.100'
SHARE_DEFAULT = 'Roms'
VER_DEFAULT = '2.1'

# cifs 选项是逗号分隔，且控制字符无意义 —— 一并剔除
_BAD_CHARS = re.compile(r'[,\x00-\x1f\x7f]')

# ark 用户是 uid/gid 1002（不是 1000）。Windows 上没有 os.getuid，
# 这里兜底成 1002，保证模块在开发机上也能导入（真机取值一致）。
_UID = getattr(os, 'getuid', lambda: 1002)()
_GID = getattr(os, 'getgid', lambda: 1002)()


# ------------------------------------------------------------------ 配置
def sanitize(s):
    """去掉会破坏 cifs 选项串的字符（逗号）与控制字符"""
    return _BAD_CHARS.sub('', s or '')


def defaults():
    return {'host': HOST_DEFAULT, 'share': SHARE_DEFAULT,
            'user': '', 'password': '', 'vers': VER_DEFAULT}


def normalize(cfg):
    out = defaults()
    if isinstance(cfg, dict):
        for k in out:
            v = cfg.get(k)
            if v is not None:
                out[k] = sanitize(str(v))
    out['host'] = out['host'].strip()
    out['share'] = out['share'].strip().strip('/')
    out['user'] = out['user'].strip()
    out['vers'] = out['vers'].strip() or VER_DEFAULT
    if not out['host']:
        out['host'] = HOST_DEFAULT
    if not out['share']:
        out['share'] = SHARE_DEFAULT
    return out


def load():
    """读 source.json；任何异常都静默回落到默认值（不能让开机挂载挂掉）"""
    try:
        with open(SOURCE_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = None
    return normalize(d)


def save(cfg):
    """原子写 + 0600（含密码）"""
    cfg = normalize(cfg)
    d = os.path.dirname(SOURCE_PATH) or '.'
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.source-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, SOURCE_PATH)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return cfg


def unc_path(cfg=None):
    cfg = cfg or load()
    return '//%s/%s' % (cfg['host'], cfg['share'])


def auth_label(cfg=None):
    cfg = cfg or load()
    return ('账号 %s' % cfg['user']) if cfg.get('user') else '匿名'


def describe(cfg=None):
    cfg = cfg or load()
    return '%s（%s）' % (unc_path(cfg), auth_label(cfg))


def mount_opts(cfg=None):
    cfg = cfg or load()
    parts = []
    if cfg.get('user'):
        parts.append('username=%s' % cfg['user'])
        parts.append('password=%s' % cfg.get('password', ''))
    else:
        parts.append('guest')
    parts += ['vers=%s' % cfg.get('vers', VER_DEFAULT),
              'iocharset=utf8',
              'uid=%d' % _UID, 'gid=%d' % _GID,
              'file_mode=0664', 'dir_mode=0775',
              'nounix', 'cache=loose', 'actimeo=60', '_netdev']
    return ','.join(parts)


# ------------------------------------------------------------------ 挂载状态
def _unescape(s):
    """还原 /proc/mounts 里的 \\040 等转义"""
    try:
        return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), s)
    except Exception:
        return s


def mounted_info(mp=None):
    """返回 (source, fstype) 或 None —— 直接读 /proc/mounts，不依赖 findmnt"""
    mp = mp or MOUNT_POINT
    try:
        with open('/proc/mounts', 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                p = line.split()
                if len(p) >= 3 and _unescape(p[1]) == mp:
                    return (p[0], p[2])
    except Exception:
        pass
    return None


def is_mounted(mp=None):
    return mounted_info(mp) is not None


def matches(cfg=None, mp=None):
    """当前挂载的是不是就是配置里的那个共享"""
    info = mounted_info(mp)
    if not info:
        return False
    return info[0].rstrip('/') == unc_path(cfg).rstrip('/')


def status_text(cfg=None):
    info = mounted_info()
    if not info:
        return '未挂载'
    src, fstype = info
    tag = '' if matches(cfg) else '（与当前源不一致）'
    return '已挂载 %s %s%s' % (src, fstype, tag)


def ping_port(host, port=SMB_PORT, timeout=1.5):
    """非阻塞探活：远端不可达时连接会挂很久，所以先探端口再动手"""
    if not host:
        return False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        rc = s.connect_ex((host, port))
        s.close()
        return rc == 0
    except Exception:
        return False


# ------------------------------------------------------------------ 挂载
# mount.cifs / 内核的英文诊断 -> 中文提示。掌机上没有控制台，
# 640x480 的屏幕上就只能靠这一行告诉用户"到底哪里错了"。
_HINTS = [
    ('no such file or directory', '共享名不存在（大小写敏感），或该主机没有这个共享'),
    ('permission denied', '权限被拒 —— 多半需要账号密码，或该共享不允许匿名访问'),
    ('access denied', '权限被拒 —— 账号/密码不对，或该共享不允许匿名访问'),
    ('connection refused', '对方拒绝连接 —— 445 端口没在监听'),
    ('connection timed out', '连接超时 —— 主机地址不对，或不在同一网段'),
    ('host is down', '主机不可达 —— 检查 NAS 是否开机'),
    ('no route to host', '网络不可达 —— 掌机与 NAS 不在同一网段'),
    ('already mounted', '挂载点已被占用'),
    ('invalid argument', '挂载参数不被支持（本机内核 cifs 只认 vers=2.1）'),
    ('operation not permitted', '操作被拒绝 —— 检查 sudo 免密是否仍然有效'),
    ('helper program', '缺 mount.cifs —— 在掌机上执行：apt-get install cifs-utils'),
    ('eopnotsupp', '内核不支持该挂载参数（本机 cifs 只认 vers=2.1）'),
]

_CODE_HINTS = {
    32: '内核拒绝了这次挂载 —— 多半是共享名不存在，或该共享不允许匿名访问',
    16: '设备忙 —— 挂载点已被占用',
    13: '权限不足',
    111: '连接被拒 —— 445 端口没在监听',
    112: '主机不可达',
    1: '脚本执行出错',
}


def hint_for(text, rc=None):
    low = (text or '').lower()
    for pat, tip in _HINTS:
        if pat in low:
            return tip
    if rc is not None:
        return _CODE_HINTS.get(rc, '')
    return ''


def mount(cfg=None, force=True, wait=220):
    """调用 nas-roms-mount.sh（脚本自己会读 source.json）

    force=True 时传 --force：即使已挂载也先卸载再重挂（换源时必须）。
    """
    cfg = cfg or load()
    host = cfg['host']
    if not os.path.exists(MOUNT_SCRIPT):
        return {'ok': False, 'error': '挂载脚本不存在：%s' % MOUNT_SCRIPT}
    if not ping_port(host):
        return {'ok': False, 'stage': 'precheck',
                'error': '连不上 %s:%d' % (host, SMB_PORT),
                'hint': '检查主机地址、NAS 是否开机、共享是否开启'}

    args = ['sudo', '-n', MOUNT_SCRIPT]
    if force:
        args.append('--force')
    env = dict(os.environ)
    env.setdefault('PATH', '/usr/local/sbin:/usr/local/bin:/usr/sbin:'
                           '/usr/bin:/sbin:/bin')
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=wait, env=env)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': '挂载超时（%d 秒）' % wait,
                'hint': 'NAS 无响应，检查网络'}
    except Exception as e:
        return {'ok': False, 'error': '调用挂载脚本失败：%s' % e}

    raw = (p.stdout or b'').decode('utf-8', 'replace').strip()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    # 脚本自己的汇总行（"挂载失败：…"）与 mount 的英文诊断分开
    summary = next((l for l in lines if l.startswith('挂载失败')), '')
    diag = [l for l in lines if l and not l.startswith('挂载')
            and not l.startswith('重挂') and not l.startswith('已挂载')]
    # 摘要优先取 "mount error(N): ..." 那一行 —— 它信息量最大；
    # 末行往往只是 "Refer to the mount.cifs(8) manual page" 这种废话
    err = ''
    for l in diag:
        if re.search(r'mount error\(\d+\)', l):
            err = l
            break
    if not err and diag:
        err = ' / '.join(diag[:2])
    err = err or summary or ('挂载失败（退出码 %d）' % p.returncode)
    hint = hint_for(raw, p.returncode)

    info = mounted_info()
    want = unc_path(cfg)
    if p.returncode == 0 and info and info[0].rstrip('/') == want.rstrip('/'):
        return {'ok': True, 'unc': info[0], 'fstype': info[1], 'output': raw}
    if info:
        return {'ok': False, 'mounted': info[0], 'request': want, 'hint': hint,
                'error': err or ('挂载未生效（当前挂载 %s）' % info[0]),
                'output': raw}
    return {'ok': False, 'error': err, 'hint': hint, 'output': raw}


def unmount(mp=None):
    mp = mp or MOUNT_POINT
    if not is_mounted(mp):
        return {'ok': True, 'msg': '本来就没挂载'}
    last = ''
    for c in (['sudo', '-n', 'umount', mp], ['sudo', '-n', 'umount', '-l', mp]):
        try:
            p = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30)
        except Exception as e:
            last = str(e)
            continue
        if not is_mounted(mp):
            return {'ok': True, 'msg': '已卸载'}
        last = (p.stdout or b'').decode('utf-8', 'replace').strip()
    return {'ok': not is_mounted(mp), 'error': last or '卸载失败'}


# ------------------------------------------------------------------ 局域网扫描
def local_ip():
    """拿本机在默认路由上的地址（不是 127.0.0.1）"""
    try:
        p = subprocess.run(['ip', '-4', 'route', 'get', '1.1.1.1'],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=5)
        m = re.search(r'src (\d+\.\d+\.\d+\.\d+)',
                      (p.stdout or b'').decode('utf-8', 'replace'))
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('1.1.1.1', 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ''


def local_subnet():
    """返回 '10.168.1' 这样的前三段"""
    ip = local_ip()
    if ip and ip.count('.') == 3:
        return ip.rsplit('.', 1)[0]
    return ''


def scan_hosts(subnet=None, port=SMB_PORT, timeout=0.45, workers=64, progress=None):
    """扫描 /24 网段上开了 445 的主机，按末段数字排序返回 IP 列表"""
    from concurrent.futures import ThreadPoolExecutor
    net = (subnet or local_subnet()).strip().rstrip('.')
    if not net:
        return []
    hosts = ['%s.%d' % (net, i) for i in range(1, 255)]
    found = []
    done = [0]
    lock = threading.Lock()

    def probe(ip):
        ok = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            ok = (s.connect_ex((ip, port)) == 0)
            s.close()
        except Exception:
            ok = False
        with lock:
            done[0] += 1
            if ok:
                found.append(ip)
            if progress:
                try:
                    progress(done[0], len(hosts), ip)
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(probe, hosts))
    found.sort(key=lambda x: int(x.rsplit('.', 1)[-1]))
    return found


def probe_host(host, port=SMB_PORT, timeout=1.5):
    """单个主机探活，返回 (ok, 说明文字)"""
    if not host:
        return False, '未填写主机'
    ok = ping_port(host, port, timeout)
    if ok:
        return True, '%s 可达（%d 开放）' % (host, port)
    return False, '连不上 %s:%d' % (host, port)
