# -*- coding: utf-8 -*-
"""商店源（NAS 共享）：配置读写 + 局域网扫描 + 挂载/卸载

职责边界：
  1. ``source.json`` 是**全系统唯一的一份商店源真相**
     * 商店客户端读它来知道去哪挂载；
     * 开机挂载脚本 ``nas-roms-mount.sh`` **也读它**，
       所以「改了商店源」和「开机自动挂载」永远指向同一个共享，不会各说各话。
     * 文件不存在 / 损坏时，脚本回落到内置默认值 —— 保证开机挂载
       绝不会因为配置缺失而失败（这是硬要求，见 nas-roms-mount.sh 注释）。
  2. 局域网扫描：找出开了 445 的主机。掌机没有键盘，这是最主要的输入方式。
  3. 挂载/卸载：**只调用挂载脚本**，复用同一把 flock
     （/run/nas-roms.lock），避免和 systemd / 网络管理器抢锁。

## 两种挂载后端（0.6.0 起）

    cifs    ArkOS：内核 cifs + mount.cifs，挂载源是 //host/share
    rclone  AURKNIX/ROCKNIX：**内核没有 cifs**，用 rclone 的 FUSE 挂载

后端由 ``platform.json`` 决定（config.MOUNT_BACKEND），本模块只负责
「按配置调脚本 + 读回状态」，不自己拼挂载参数。

⚠️ rclone 挂载在 ``/proc/mounts`` 里的源名是 ``:smb{xxxx}:Roms`` ——
**认不出主机**。所以「挂的还是不是我配置的那个共享」不能像 cifs 那样
比对源字符串，只能读挂载脚本写的状态文件 ``<STORE_DIR>/.mount-state``。
（挂载脚本与客户端**必须**同时升级，老客户端 + 新脚本会判不出「源一致」。）

实测约束（改之前先看这里）：
  * ArkOS 内核 cifs 只认 vers=2.1；vers=2.0 返回 EOPNOTSUPP(95)
  * 挂载点由平台档案决定（ArkOS=/mnt/Roms，ROCKNIX=/storage/nas-roms）
  * cifs 的 -o 是逗号分隔，所以账号/密码里**不能有逗号**（软键盘也不提供逗号）
  * ROCKNIX 上 rclone 1.71 只找 fusermount3，而机器只有 fusermount(v2) ——
    安装器会在 STORE_DIR/bin 放同名桥接，PATH 由挂载脚本自己加
"""
import json
import os
import re
import socket
import subprocess
import tempfile
import threading

from . import config

SOURCE_PATH = os.environ.get('ROMSTORE_SOURCE', config.SOURCE_PATH)
MOUNT_SCRIPT = os.environ.get('ROMSTORE_MOUNT_SCRIPT', config.MOUNT_SCRIPT)
MOUNT_POINT = os.environ.get('ROMSTORE_MOUNT', config.NAS_ROOT)
MOUNT_STATE = os.environ.get('ROMSTORE_MOUNT_STATE', config.MOUNT_STATE_PATH)
BACKEND = config.MOUNT_BACKEND
LOCK_PATH = '/run/nas-roms.lock'

SMB_PORT = 445
HOST_DEFAULT = '10.168.1.110'
SHARE_DEFAULT = 'Roms'
SMB_USER_DEFAULT = 'smbshare'
# ⚠️ 默认密码**故意不写死**：本仓库是公开的，写进去等于把 NAS 口令公开。
#    真密码只落两处：掌机上的 source.json（权限 600）、或安装时 --smbpass。
#    需要把默认值「烤」进本地打包时，用环境变量 ROMSTORE_SMB_PASS 注入即可
#    （make-dist 打出来的包里会有，但仓库里没有）。
SMB_PASS_DEFAULT = os.environ.get('ROMSTORE_SMB_PASS', '')
VER_DEFAULT = config.SAMBA_VERS or '2.1'

# cifs 选项是逗号分隔，且控制字符无意义 —— 一并剔除
_BAD_CHARS = re.compile(r'[,\x00-\x1f\x7f]')

_UID = getattr(os, 'getuid', lambda: 1002)()
_GID = getattr(os, 'getgid', lambda: 1002)()


def need_sudo():
    """本机是否需要 sudo 前缀。

    ArkOS 上是 ark 用户跑商店 → 需要 sudo -n；
    ROCKNIX 上只有一个 root 用户，而且**没装 sudo** —— 传了会直接报
    「sudo: not found」，看着像权限问题，其实是多余的。所以按 uid 判。
    """
    if _UID == 0:
        return False
    try:
        return subprocess.call(['sh', '-c', 'command -v sudo >/dev/null 2>&1']) == 0
    except Exception:
        return False


def _priv(args):
    """给命令加 sudo 前缀（需要时才加）"""
    return (['sudo', '-n'] + list(args)) if need_sudo() else list(args)


# ------------------------------------------------------------------ 配置
def sanitize(s):
    """去掉会破坏 cifs 选项串的字符（逗号）与控制字符"""
    return _BAD_CHARS.sub('', s or '')


def defaults():
    return {'host': HOST_DEFAULT, 'share': SHARE_DEFAULT,
            'user': SMB_USER_DEFAULT, 'password': SMB_PASS_DEFAULT,
            'vers': VER_DEFAULT}


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


def mount_opts(cfg=None, mask=False):
    """拼 cifs 的 ``-o`` 串。

    ⚠️ ``mask=True`` 只给**打印/日志**用：把 password 换成等长星号。
    真挂载必须走默认的 ``mask=False``，否则挂不上。
    （2026-10-25：``--source-show`` 原来会把口令明文打进「挂载选项」那行，
      随手截图/贴日志就等于把 NAS 密码交出去了。）
    """
    cfg = cfg or load()
    parts = []
    if cfg.get('user'):
        parts.append('username=%s' % cfg['user'])
        pw = cfg.get('password', '') or ''
        parts.append('password=%s' % (('*' * len(pw)) if (mask and pw) else pw))
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


def mount_state():
    """读挂载脚本写的状态文件（KEY=VALUE 行）

    rclone 的挂载源名认不出主机（`:smb{hash}:Roms`），所以「挂的是不是我
    配置里的那个共享」只能靠这个文件。脚本还没升级 / 文件没写出来时返回空字典，
    调用方会自动退回到「比对 /proc/mounts 源名」的老办法。
    """
    d = {}
    try:
        with open(MOUNT_STATE, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                d[k.strip()] = v.strip()
    except Exception:
        return {}
    return d


def matches(cfg=None, mp=None):
    """当前挂载的是不是就是配置里的那个共享"""
    info = mounted_info(mp)
    if not info:
        return False
    cfg = cfg or load()
    st = mount_state()
    if st.get('HOST'):
        if st.get('HOST') != cfg['host'] or st.get('SHARE') != cfg['share']:
            return False
        # 后端也要一致：换平台时同一个挂载点下可能是另一种后端残留的挂载
        return (st.get('BACKEND') or 'cifs') == BACKEND
    # 老版脚本没写状态文件 —— cifs 只能靠源名比对（rclone 的源名认不出主机，
    # 此时只能认为「挂上了且是 fuse.rclone」就算一致）
    if BACKEND == 'rclone':
        return info[1].startswith('fuse.rclone')
    return info[0].rstrip('/') == unc_path(cfg).rstrip('/')


def status_text(cfg=None):
    info = mounted_info()
    if not info:
        return '未挂载'
    src, fstype = info
    tag = '' if matches(cfg) else '（与当前源不一致）'
    st = mount_state()
    who = ''
    if st.get('HOST'):
        who = ' -> //%s/%s' % (st['HOST'], st.get('SHARE', ''))
    return '已挂载 %s %s%s%s' % (src, fstype, who, tag)


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
    ('invalid argument', '挂载参数不被支持（ArkOS 内核 cifs 只认 vers=2.1）'),
    ('operation not permitted', '操作被拒绝 —— 检查权限（ROCKNIX 上是 root，不该出现）'),
    ('helper program', '缺 mount.cifs —— 在掌机上执行：apt-get install cifs-utils'),
    ('eopnotsupp', '内核不支持该挂载参数（ArkOS 上 cifs 只认 vers=2.1）'),
    # ---- rclone/FUSE 后端（ROCKNIX）----
    ('no such device', '内核不支持这个文件系统类型 —— 这台机器的内核没有 cifs，'
                       '请用 rclone 后端（platform.json 的 mount_backend）'),
    ('fusermount', '缺 fusermount —— rclone 挂 FUSE 需要它；'
                   '这台只有 fusermount(v2)，安装器会做 fusermount3 名字桥接'),
    ('transport endpoint is not connected', 'FUSE 挂载已失效（rclone 进程退过）。'
                                            '先卸载再重挂即可'),
    ('input/output error', 'FUSE 读取失败 —— 多半 rclone 进程已退出，重挂即可'),
    ('rclone', 'rclone 报错 —— 上面那行是它的原话；最常见是 NAS 未开机或凭据不对'),
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

    args = _priv([MOUNT_SCRIPT])
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
    if p.returncode == 0 and info and matches(cfg):
        return {'ok': True, 'unc': info[0], 'fstype': info[1],
                'backend': BACKEND, 'output': raw}
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
    # rclone 的 FUSE 挂载用 fusermount 卸更干净（umount 常常 EBUSY）
    tries = []
    if BACKEND == 'rclone':
        tries.append(_priv(['fusermount', '-u', '-z', mp]))
    tries.append(_priv(['umount', mp]))
    tries.append(_priv(['umount', '-l', mp]))
    for c in tries:
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
