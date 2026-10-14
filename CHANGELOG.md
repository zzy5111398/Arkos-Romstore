# 更新日志

## 0.6.0 — 2026-10-12

**三平台：同一份代码，ArkOS / AURKNIX·ROCKNIX / DarkOS 都能装，安装时自动区分**

后两台掌机的架构与老 R36S 都不同，不是「改几个路径」能解决的：
X35S（AURKNIX，ROCKNIX 的社区分支）**连根文件系统都是只读的 squashfs**，
而 DarkOS（dArkOS）看着像 ArkOS，**ES 却整套换了 Batocera 风格的系统名**。

| 项 | ArkOS（R36S） | AURKNIX / ROCKNIX（X35S） | DarkOS（dArkOS，R36S） |
|---|---|---|---|
| 根文件系统 | 可写 | **只读 squashfs**（`/flash/SYSTEM`） | 可写 |
| 持久分区 | `/`（全盘） | **只有 `/storage`**（ext4） | `/` |
| 登录用户 | `ark` uid=1002 | **只有 `root`**，且**没装 sudo** | `ark` uid=1002 |
| NAS 传输 | 内核 cifs，`vers=2.1` | **内核没有 cifs**，只能 rclone FUSE | 内核 cifs，`vers=2.1` |
| 显示栈 | SDL/KMSDRM 直出 | **Sway / Wayland** | SDL/KMSDRM 直出 |
| ES 配置 | `/etc/emulationstation` | `/storage/.config/emulationstation` | `/etc/emulationstation` |
| ES 入口 | Options → `/opt/system` | Ports → `/storage/roms/ports` | Ports → `/roms/ports` |
| ES 系统名 | 传统长名 | 传统长名 | **Batocera 短名**（无 `options`） |
| 自启单元 | `/etc/systemd/system` | `/storage/.config/system.d` + 软链 | `/etc/systemd/system` |
| 中文字体 | 系统自带 NotoCJK | **一个都没有**，要从 scummvm 主题借 | 系统自带 NotoCJK |
| `/roms` 自愈 | 装（exFAT 掉电只读） | 不适用（由固件 udev 挂载） | 装（同 ArkOS） |

本轮的改动分几块：①②③④ 是把 ROCKNIX（X35S）移植过来；
⑤~⑫ 是在 X35S 真机上试出来、一并做掉的；⑬ 是第三族 DarkOS；
⑭ 是把开机路径上的 NAS 挂载摘下来。

**① 新增 `romstore/platform.py`** —— 平台档案 + `detect()`。
安装期探测结论写成 `<STORE_DIR>/platform.json`，运行期统一从它取路径。
覆盖优先级：**环境变量 > platform.json > 本机特征探测 > ArkOS 默认值**。
老机器上 `platform.json` 不存在时，探测结论与 0.5.x 一个字节不差
（`tests/test_platform.py` 第 2 节把 22 个常量逐项锁死）。

**② NAS 挂载双后端** —— `nas-roms-mount.sh` 同一份脚本支持 `cifs` 与 `rclone`。

ROCKNIX 上踩的两件事：
- `/proc/filesystems` 里**没有 cifs**，`/lib/modules` 里**没有 cifs.ko**，
  `mount.cifs` 也不存在 → `mount -t cifs` 报 `No such device`。只能换 rclone。
- rclone 挂 FUSE 时**只找 `fusermount3`**，而 ROCKNIX 只带了 `fusermount`(v2)，
  直接失败：`fusermount: exec: "fusermount3": executable file not found in $PATH`。
  安装器在 `<STORE_DIR>/bin` 建一个同名软链桥接，并把它排到 PATH 最前。
  （实测挂上后 fstype 是 `fuse.rclone`，81 个顶层目录，元数据读取 0.3 秒。）

**③ `RomStore.sh` 的平台分支** —— 只有三处，都收在一个 `$PLATFORM` 判断里：
- 提权：ArkOS 用 sudo；ROCKNIX 只有 root，盲 exec sudo 会 `sudo: not found`
- 显示：ArkOS `SDL_VIDEODRIVER=kmsdrm` / mpv `--vo=drm`；
  ROCKNIX `wayland` / `--vo=gpu`，且要从 ES 环境继承 `XDG_RUNTIME_DIR`
- 收尾：**ROCKNIX 必须 `pkill emulationstatio`**（见下）

**④ 关于「装完的游戏怎么出现在 ES 里」** —— ArkOS/DarkOS 与 ROCKNIX 的机制完全不同：

- ArkOS：ES 启动时读一次 gamelist，由固件那套 wrapper 负责重载，我们不用管。
- ROCKNIX：Sway 是合成器，**ES 并没有被停掉，只是被压在商店窗口底下继续活着**。
  `runemu.sh` / `set_kill` 都不管这事（`set_kill` 只是给全局强杀热键登记名字，
  见 `/etc/profile.d/001-functions`）。真正让 ES 回来的是 `essway.service`
  的 `Restart=always` —— 杀掉 ES，systemd 立刻拉起一个全新的，顺带重读 gamelist。
  官方「在线商店」EmuDrop 结尾也是同一招，这里照搬。
  ⚠️ `pkill` 默认比 `/proc/<pid>/comm`，**只有 15 个字符**，必须写
  `emulationstatio`（少一个 n），写全名一个都匹配不到。

**其他**：
- `mapp.py` 增加 ROCKNIX 映射表。ROCKNIX 的系统名与 ArkOS 大面积不同
  （`psp` 而非 `ppsspp`、`neocd` 而非 `neogeocd`、`pokemini` 而非 `pokemonmini`、
  `pc` 而非 `msdos`、`mame` 而非 `mame2003`），且 `famicom`/`nes`、`sfc`/`snes`
  成对存在 —— 所以第 2 列改成「候选列表」，由 `_pick()` 挑本机 es_systems.cfg
  里真有的那个。ROCKNIX 有 `gamecube`/`wii`，一律标 `maybe` 让用户自己决定。
- `install.sh` / `uninstall.sh` 全面多平台化，各自有一节平台探测与分平台参数表（⑬ 起是三族）。
- `batchmedia.py` 的 `imagecache.db` / `es_settings.cfg` 不再写死 `/home/ark/`，
  改为按候选目录挑第一个真实存在的（ROCKNIX 上是 `/storage/.emulationstation/`，
  实为 `/storage/.config/emulationstation` 的软链）。
- 新增 `tests/test_platform.py`：平台探测、优先级、ROCKNIX 映射、
  **模板占位符与 install.sh 替换表的一致性**（漏一个占位符就是安装期炸）。

### 移植之外：0.6.0 一并做的四件事

**⑤ 右栏内嵌预览换成 mpv 帧服务**（在 X35S 上试出来的）

ROCKNIX 上**没有 ffmpeg 命令行** —— 全盘都没有，`/usr/bin` 里没有，
`busybox` 也没有。0.5.0 那条「ffmpeg 持续往管道吐 JPEG」的路在那台机器上
直接不成立。但它装了 **mpv 0.36**（内含 ffmpeg 6.0.1 的库 + rkvdec 硬解），
于是新增第二条后端 `MpvFramePlayer`，对外接口与老的 `VideoPlayer` 逐行等价：

| | ArkOS（R36S，RK3326） | AURKNIX（X35S，RK3566） |
|---|---|---|
| 解码器 | `ffmpeg -skip_frame nokey`（**只解 I 帧**） | `mpv --vo=image --hwdec=auto-safe`（**全帧 + 硬解**） |
| 为什么 | 内核没编 VPU 驱动，纯软解要 230% CPU，只能丢 B/P 帧 | 有真 VPU（rkvdec），不必再丢帧 |
| 取帧 | JPEG 裸流走管道 | mpv 把每帧写成 `<tmp>/romstore-prev/NNNNNNNN.jpg` |

两条后端由 `vplayer.create()` 按平台与可用性挑，两个都没有时给 `NullPlayer`
（右栏退回只显示封面，不再假报「正在播放预览…」）。

取帧方案踩过的坑，全是实测：
- **`--vo=image` 是全速解码的**：5 秒素材 2 秒吐完 150 帧，画面变快进。
  必须靠 `--video-sync=audio` + `--ao=<声卡>,null` 让 null 声卡提供实时时钟
  （实测 5 秒素材耗时 6 秒、16 秒出 189 帧 = 正好 12fps 实时）。`--untimed=no` 无效。
- mpv 的输出容器只支持 `adts/asf/ipod/latm/mov/mpegts/spdif` 六种，
  **`--of=image2pipe` 不存在**，所以只能写文件、不能走管道。
- **`--hwdec=auto-safe` 不能写成 `auto`**：拿不到 `/dev/dri`（比如 SSH 会话里）
  时 `auto` 会硬解失败并直接放弃，报 `Could not open codec` 且一个字都不出。

真机实测：25 秒收到 226 帧（约 9 fps），界面进程 CPU 78%、mpv 自身 39%，4 核；
关闭后帧目录清空、mpv 进程无残留。

**⑥ 卡片网格放大到 4 列 × 4 行 / 卡宽 91px**（去掉卡片下方的标题文字）

用户反馈两条：卡片下方不要用标题遮挡封面；去掉标题后封面要大一点。

- `CARD_TEXT_H = 0`，卡片上只剩封面 + 角标 + 选中边框；**没有封面**的游戏
  才在卡片中间写名字兜底（三行居中，实测正常）。
- 卡片尺寸不再「先钉死 5 列」，改成**搜索**：枚举 `cols ∈ 2..6` × `rows`，
  取「宽度允许」与「高度允许」的较小值，在 `cols*rows ≥ 16` 的方案里挑卡宽
  最大的那个。老写法先钉 5 列，会把去掉标题腾出来的高度全浪费掉。
  顶栏 52→44、底栏 44→38，可用高 378→392，于是从 5×4 / 71px 变成
  **4×4 / 91px**（占用宽 388/388、高 388/392）。
- 「第 N/M 款」的位置指示并进顶栏 —— 大网格之后它原来待的右下角
  正好压在最后一行卡片上。

**⑦ 进度页新增「复制速度」**

ROM 都是几十 MB 到几 GB，从 NAS 经 SMB / rclone 读、再写 SD 卡。原来只有一条
百分比进度条，用户分不清「还要等 10 秒」和「已经卡死了」。现在复制阶段显示：

```
速度 35.57 MB/s        已用 3 秒        剩余 3 秒
▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▂▃▄▅▆▇█▇▆▅▄▃▂▁   ← 最近 60 个瞬时速度
```

几个不那么显然的决定：

- **速度在渲染时算，不在采样时算。** 分母用「现在 − 窗口起点」，所以卡住时
  分子不变、分母一直涨，速度自然衰减到 0。反过来若在采样时算好存着，界面会
  一直挂着最后一个好看的数字骗人。
- **不能一停就报 0**：512KB 一个回调，200 KB/s 时本来就 2.5 秒才来一个样本，
  所以只有超过 2 秒确实没有任何样本才认定为停顿。
- **停顿显示「— 等待数据…」而不是 0 KB/s** —— 0 读起来像"坏了"，
  这里要表达的是"等着呢"。
- 采样放在 UI 侧（`ui.App.speed_sample`），**不改 `install.install()` 的
  回调签名**（`cli.py` 也在用），而且任何阶段的字节流都自动覆盖。
- 只在 `phase == 'copy'` 画：其它阶段的 `done/total` 不是字节流，
  摆一个 0 KB/s 在那儿只会让人以为传输卡了。
- 柱状图纵轴按**窗口峰值归一化**并留 15% 余量（SMB 上速度很稳，不留余量
  每根柱子都顶格，整条图变成一块实心砖）；横向**从左边开始长**，满 60 格前
  右边留白，一眼看出"才刚开始"。

真机实测（198.36 MB 的 PS1 `.chd`）：冷读 **2.55 MB/s**（77.89 秒），
rclone VFS 缓存命中 **31.73 MB/s**（6.25 秒）—— 两种极端下读数与剩余时间都正常。

**⑧ 收掉孤儿 mpv（真机上残留过 3 个）**

界面进程正常退出时会连带收掉 mpv（`shutdown → _kill`）。但下面两种情况收不掉，
mpv 被 init 收养（`ppid=1`）继续跑：界面进程吃到 SIGKILL；或者 0.6.0 之前的
版本吃到 SIGTERM。

三个后果，都是实测到的：

1. 残留 10 分钟就在 `/tmp`（488MB tmpfs）里攒了 **2741 个 jpg / 22MB**；
2. 它**仍然占着声卡**，新起的预览会拿到 `Device or resource busy` 变成静音；
3. 父进程被杀 3 次就残留 3 个，三个 mpv 各自从 1 开始编号写进**同一个帧目录**，
   读帧的一方拿到的是三份互相覆盖的乱序画面。

三层处理：
- **`run.py` 装信号处理器**，把 SIGTERM/SIGINT/SIGHUP 变成「请主循环正常收尾」
  （只置 `app.stop()` 的位，不抛异常 —— 信号可能在任意两条字节码之间到达，
  抛异常会从 SDL 调用中间穿出去）。默认动作是立即终止，`finally` 根本不执行 ——
  这就是根因。
- **`vplayer.sweep_stale()`** 扫 `/proc/*/cmdline` 找 `--vo-image-outdir=` 且
  目录是 `romstore-prev`/`cache/frames` 的进程收掉。用 `/proc` 而不用 `pkill`：
  BusyBox 的 `pkill -f` 在这台机器上行为不稳（同一个模式，匹配 mpv 的参数有效，
  匹配 `python3 /storage/.romstore/run.py` 却毫无反应）。
  ⚠️ 只在**第一次起自己的 mpv 之前**扫一次，不能放在 `__init__` 里 ——
  无头模式（`run.py --script` 导出界面图）也会构造播放器对象，那时真机上
  可能正开着一个商店在跑，在 `__init__` 里扫就会把人家正在用的 mpv 杀掉。
- `RomStore.sh` 的收尾里调一次 `run.py --sweep`，兜「界面进程被 SIGKILL」
  和「脚本自己被强杀后又被拉起」。

顺带修掉两个真 bug：
- `VideoPlayer._loop()` 里 `sig = self._sig` 之后再判 `sig != self._sig` **永远为假**，
  `set_sound()` 实际上要等下次切游戏才生效。
- 缺解码器时 worker 每 50ms 重试一次 `Popen`（忙等），现在失败后退避 ≥0.5 秒。

**⑨ 新增 `tests/test_speed.py`** —— 速度计的守门测试（离线、不建 SDL 显示）。
它逼出了一个真问题：样本窗口的裁剪是靠「头部样本够老」推进的，若时间戳几乎
不前进（极快的本地复制、NTP 拨表），头部永远不"够老"，列表会无限涨 ——
于是补了一条硬上限（`_SP_MAX = 400`）。

**⑩ 网格改成「只纵向滚动 + 左右不跨行」**（0.6.0 用户反馈）

用户的原话是四条，其实同一个根因：

> 网格应该只进行纵向滚动 / 现在从第三行就开始滚动了，应该改成第四行开始滚动 /
> 第三行第一列向右只会向下换行 / 网格会出现横向滚动，不够直观

根因：视口以前是**居中跟随** —— `top = game_sel - per_page // 2`。
4 列布局下 `per_page // 2 = 8`，所以选中项一到第 9 个（也就是**第 3 行**）
视口就跟着一格一格地滚，光标被钉在第 3 行不动 —— 按右键看到的就是
"网格往下换了一行"，而且**任何按键都会带着整屏滚动**。

改成两条规则：

| 键 | 现在的作用 |
|---|---|
| 左 / 右 | **只在本行内移动**，到行首/行尾停住（不再 sel±1 跨行） |
| 上 / 下 | 上下一行；**滚动只由这两个键触发** |
| L1 / R1 | 翻**一整页**（原来是半页，`cols*2`） |

- 视口改成**粘性状态** + **最小滚动**：只在光标要跑出可见区时才动，一次一行。
  4 行可见时前 3 行完全不滚，从第 4 行再往下才滚 —— 就是用户要的
  「第四行开始滚动」。视口由 `_grid_viewport()` 单独算，不再每帧从选中项反推
  （反推就必然回到"跟随"）。
- 左右不跨行：4 列全都在屏上，不存在按不到"看不见的列"，所以不跨行不会漏游戏。
  用户抱怨的正是跨行 + 跟滚叠加出来的"向下换行"。
- `goto_game()`（含 `run.py --resume <id>` 看预览回来）仍把目标摆在**中间一行** ——
  那是"一次性定位"，和"按键导航"是两件事，所以在那里显式给视口初值，
  而不是让渲染去反推居中。

真机实测（GBA 465 款 / 640x480 实算 4 列 x 4 行）：

```
场景 A  连按"下"：top 变化序列 0 0 0 0→4   ← 拐点正好在第 4 次
场景 B  第 3 行第 1 列按右键：sel 8→9→10→11，top 始终 0，屏上行始终第 3 行
场景 C  64 个位置全扫一遍按右键：引起纵向滚动的次数 = 0
场景 E  最后一款：top=452（整行对齐，不露空白行）
```

**⑪ 新增 `tests/test_nav.py`** —— 上面那条改动的守门测试。
第 4 节把「右键绝不引起纵向滚动」写成了**全量断言**（对每一个格子都试一遍），
这是这个 bug 最不容易再犯的写法。它自己也吃过一次教训：视口更新发生在
**渲染帧**里，所以 `press()` 必须"按键 + 再跑一次视口"，否则测的是半个流程。

**⑫ 卸载/重装的 gamelist 往返：修掉"每往返一次多一个空行"**

`uninstall.sh` 的完整往返以前**从没在真机跑过**，这次补上了 —— 一跑就发现
`ports/gamelist.xml` 的 md5 变了：

```
 	</game>
+
   <game>
     <path>./RomStore.sh</path>
```

`unregister` 删除条目时只回退了**行首缩进**，没管块前面那个 `'\n'`，于是原地
留下一个空行；而 `register` 是把条目插在 `</gameList>` 前面 —— 两者叠加的结果是
**每跑一次 install/uninstall 往返，gamelist 里就多攒一个空行**。

语义上无害（XML 里空行没有意义），但它破坏了「往返后字节复原」这条承诺，
而且是个**单调累积**的量：装得越多越脏。修法是删除时连块前那个换行一起删
（`\n` 前面若还有 `\r` 也算）。

新增 `tests/test_gamelist_roundtrip.py` 把这条承诺钉住：5 种文件形态
（LF / CRLF / 带 BOM / 空骨架 / 末尾无换行）× **连跑 3 次往返**，每次都要求
与原文**逐字节相同**；另外覆盖重复注册幂等、二次撤销不改文件、块在文件中间、
没有条目时优雅返回、文件不存在时不炸。

真机实测（X35S，`/storage/roms/ports/gamelist.xml`）：

```
起点        edb108fe…      ← 已注册
uninstall 1 64d80069…      ← 删干净
install   1 edb108fe…      ← 逐字节复原 ✅
```

⚠️ 这一项也踩了个坑：第一次验证只在掌机上热更了 `cli.py`，结果**第 2 轮往返
又坏了** —— 因为 `install.sh` 会把包里的 `cli.py` 拷回去，热更被覆盖。
**改过任何进包的文件，必须重跑 `make-dist.py` 再装。**

**⑬ 第三族平台：DarkOS（dArkOS）** —— 一台 R36S 上的 DarkOS，
`hostname=darkosre-r36`。这台最阴的地方是**认不出来**：

- `/etc/os-release` 自报的是 **`Debian GNU/Linux`**（内核 4.4.189 的重打包版），
  既没有 ROCKNIX 字样，也没有自己的标记 → 只能靠 hostname
  （`darkos` / `dark os` / `darkosre`）兜底。探测顺序里 **ROCKNIX 必须排在
  DarkOS 前面**：DarkOS 也有 `/opt/system` 那个 ArkOS 特征，先判 ROCKNIX 更保守。
- 文件系统布局与 ArkOS **几乎逐项相同**（根可写、`/home/ark` 属主是 `ark`、
  `/opt/system` 在、`/roms` 也是带 `errors=remount-ro` 的 exFAT），
  所以 `platform.py` 里的 `DARKOS` 档案复用 `ARKOS` 的绝大部分取值。
- **唯一的实质差异是 ES 系统名**：`es_systems.cfg` 是 Batocera 风味短名，
  共 140 个 system，**没有 `options` 系统** → 入口只能走 `ports`
  （`/roms/ports/`）。于是新增 `MAP_DARKOS_RAW`（70 条）：
  - `PS1` → `psx`、`PSP` → `ppsspp`（**不是 ROCKNIX 那个裸 `psp`**；
    这台的 `ppsspp` 路径是 `/roms/psp/`，且没有 `psp` 系统）
  - `Atomiswave` 的 ES 名**带空格**（`Sega Atomiswave`），路径却是 `/roms/atomiswave`
  - `snes` 在 cfg 里**出现两次**（先 `/roms/sufami/`、后 `/roms/snes/`）——
    解析时按后者，别被带偏
  - `mame2003`(/roms/mame2003) 与 `mame2010`(/roms/mame) 并存、`msdos` 在
  - ⚠️ 顺带修了 `_by_es_name()`：它原来只登记 `_pick()` 挑中的那一个候选名，
    于是 `Sega Atomiswave` 查不到（`NAOMI` 挑了排前面的 `naomi`）。改为把
    「本机 cfg 里确实存在的其它候选」也登记成别名，判据必须 `c in names`，
    否则 126 上不存在的 `psp` 会被误当成 PSP 别名。
- `install.sh` 里 20 处 `[ "$PLATFORM" = "arkos" ]` 全部改成
  `[ "$PLATFORM" != "rocknix" ]`（依赖补装 / 字体 / `es_cache_db` /
  `roms_fs_selfheal` / 自愈脚本 / 备份 …）。唯一保留 `= arkos` 的是 ES 崩溃
  保护（只适配 ArkOS 的 `emulationstation.service`）。
- 这轮还暴露了 3 个**只有 126 才踩得到**的真 bug，一并修掉：
  1. **`ark` 的 PATH 不含 `/usr/sbin`** —— `mount.cifs` / `fsck.exfat` /
     `mountpoint` 都在那儿，安装器会对着**已经装好**的 cifs-utils 报「未安装」。
     已在 `install.sh`（**必须放在所有 `need_cmd` 之前**）、
     `src/nas-roms-mount.sh`、`src/RomStore.sh`、`src/systemd/roms-fs-selfheal.sh`
     四处补 sbin。
  2. **`sudo -- "$0"` 在相对路径下报 command not found** —— `bash install.sh`
     时 `$0` 就是 `install.sh`。`install.sh` / `uninstall.sh` 改用 `$HERE`
     拼绝对路径并在无可执行位时显式 `sudo /bin/bash`；`src/RomStore.sh`
     改用 `readlink -f "$0"`。
  3. **`fsck.exfat` 可能缺失** → 安装器在非 ROCKNIX 平台补装 `exfatprogs`。
- 真机验证（126）：探测 `hostname=darkosre-r36 → darkos`；索引 78 个 NAS 目录
  → 68 个系统 / 8358 款（去重 1522 条），耗时 75 秒；安装 #3905 用时 3.27 秒
  （2 个 ROM / 25.38 MB、自动生成 `.m3u`、封面 765 KB、gamelist 新增 1 条指向
  `.m3u`）；卸载后 gamelist **尾部字节级回到 `</gameList>`，无残留空行**。

**⑭ 开机不再被 NAS 挂载拖慢 2 分半（把 `nas-roms.service` 从开机路径上摘下来）**

用户在真机上量到 `multi-user.target @2min38s`。根因不在配置，而在
**`Type=oneshot` + enable 的副作用**：只要 unit 被 enable 到
`multi-user.target.wants/`，systemd 就会**自动**给它加
`Before=multi-user.target` —— 于是整个 `multi-user.target` 都必须等它跑完。
NAS 不在线时 rclone 要重试到超时（153 秒），开机就这么被拖住。

同一台机上的隔离实验（新建一个不 enable 的 oneshot 做对照）：

```
unit 未 enable（无软链）  →  Before=shutdown.target
unit 已 enable（有软链）  →  Before=multi-user.target shutdown.target
```

改法：新增 `systemd/nas-roms.timer`，在**开机后 20s / 90s / 5min** 各触发一次
（都用 `OnBootSec`），service 不再挂在 `multi-user.target` 上，
`Before=multi-user.target` 随之消失 —— 开机零等待，网络就绪后再补挂
（X35S 上 WiFi 实测开机 9 秒就关联上了）。两个 service 文件都去掉了 `[Install]` 段
（那正是软链的来源），`install.sh` 改装 timer 并顺手清理旧版遗留的
`multi-user.target.wants/nas-roms.service`，`uninstall.sh` 同步停/删 timer，
自检也从「查 service 是否 enabled」改成「查 timer 是否 enabled」+ 反向校验
旧软链不在。

⚠️ 顺手记一个反直觉点：**timer 不能用 `OnUnitActiveSec` 做重试**。这个
service 成功后长期停在 `active`（`RemainAfterExit=yes`），不产生新的激活时间戳，
实测 `systemctl list-timers` 的 NEXT 直接变成 `-`，根本不排期；失败时停在
`failed`（不经过 inactive），`OnUnitInactiveSec` 同样不计时。
**重试只能用多个 `OnBootSec`** 这种绝对时间点。

## 0.5.0 — 2026-10-12

**改版：列表换成卡片网格，选中即详情**

以前是「列表 → 按 A 进详情页 → 再装 / 再播」，来回两段。0.5.0 改成**一屏看完**：

```
┌───────────────────────────────┬──────────────────┐
│ 封面卡片 x 15（5 列 x 3 行）    │ 游戏标题          │
│ 选中卡片有高亮边框              │ 已安装 / 未安装    │
│ 已安装的右上角有绿点            │ 视频预览位        │
│                               │ 简介正文          │
└───────────────────────────────┴──────────────────┘
```

| 项 | 0.4.2 | 0.5.0 |
|---|---|---|
| 浏览 | 文字列表，一行一个 | **封面卡片网格**，一屏 15 款 |
| 详情 | 要按 `A` 进详情页 | **右栏常驻**，选中即所见 |
| 安装 | 列表按 X / 详情页按 A | **统一按 `A`**（哪一层都一样） |
| 播预览 | 详情页按 X | **统一按 `X`**（哪一层都一样） |
| 移动 | 仅上下 | **上下左右二维**，到边停住不绕回 |

**为什么这次改动必须有后台加载器**：卡片要画封面，而每张封面都得从 NAS 现读
（实测单张 0.1~0.3 秒：读 300 KB + JPEG 解码 + 纯 Python 缩放）。
一屏 15 张同步加载就是 1~4 秒**完全冻住**，用户会以为死机。
新增 `cover.py`：

- `CoverLoader` —— worker 线程解码缩放，主线程只 blit，卡片**渐进填满**不卡顿。
  顺带把「这个游戏有没有预览视频」一起查了（同一次 `listdir`）。
- `DescLoader` —— 整份简介**一次**后台读完。0.4.2 是每换一款游戏就重读几 MB
  metadata，在 SMB 上是秒级卡顿；现在是进系统读一次，之后切换 O(1) 查表。
- 两个加载器都带世代号，切换系统时旧任务的结果会被丢弃，不会贴错图。

**线程安全的两条硬约束**（写这个模块前必读，注释也写在 `cover.py` 顶部）：
1. SDL surface 可跨线程传递（加锁交接即可），但 **TTF 字体渲染不是线程安全的**
   → 加载器里一行画字的代码都没有。
2. surface 交给主线程后**所有权必须转移** → `ImageCache` 新增 `make_many()`
   （解码一次、不进缓存），走 `thumb()` 会被二次 `SDL_FreeSurface`，那是段错误。

- 删除 `render_detail()` 与整个 `detail` 页面状态；`goto_game()` 改为对齐网格光标。

**右栏视频预览**（0.4.2 的详情页视频搬进右栏，新增 `vplayer.py`）：

- 片源是 NAS 的 `media/<ROM去扩展名>/video.mp4`，**直接串流播，不落地**（实测覆盖率近 100%）。
- 仍用 0.4.2 的**接力机制**：界面进程退出（`rc=42`）→ mpv 播 → 播完 `run.py --resume <id>`
  精确回到原处。因为界面进程占着 KMS/DRM，mpv 要用同一块屏，两者不能并存。
- 引擎 `mpv --vo=drm --ao=alsa --hwdec=auto-safe`。
  ⚠️ **必须 `auto-safe` 不能用 `auto`**：拿不到 `/dev/dri` 时 `auto` 会报
  `Could not open codec`，一个字都不出。
- **视频与音频拆成两个 ffmpeg 进程**：原先合成一路时 ALSA 会握住句柄把视频管道堵死
  （表现是有声无画）。视频走 `image2pipe`（`-skip_frame nokey` + 低帧率低分辨率，
  降低 RK3326 软解负担），音频单独写 ALSA。
- `X` 键仍是全屏预览。右栏视频框实测 `218x123`，与 ffmpeg 输出尺寸完全一致。

**修 bug：一个 ES 系统被拆到两个 NAS 目录时，素材全都找不到**

`SS` / `SS plus`、`PS1` / `PS1 hack`、`DC` / `DC hack` 这几组共用**同一份
`metadata.pegasus.txt`**（SHA256 逐字节相同），但 ROM 文件被拆到了两个目录里互相补位。
索引原来按 `rom_rel` 去重，于是 `SS plus` 的 509 款被整批判成重复，
`PS1`/`DC` 也有几十款判重。后果是这一大批游戏：

- 点得着，但**装不了**（找不到源文件）
- **不显示文件大小**
- **没有封面、没有预览视频**（素材跟着 ROM 走，在兄弟目录那边）

修法：`games` 表加 `src_dir` 列（`SCHEMA_VERSION` 1 → 2），记录 ROM **真实所在**的目录；
索引时先在本目录找，找不到就在同一 `es_path` 的兄弟目录按优先级回退。
安装、文件大小、**封面与视频**三条链路统一改用 `src_dir`。

| 系统 | 跨目录款数 | 修复前封面 | 修复后 |
|---|---|---|---|
| PS1 / PS1 hack | 60 / 216 | 78.3% | **100%** |
| DC / DC hack | 36 / 104 | 74.3% | **100%** |
| SS | 509 | 21.3% | **89.1%** |

SS 剩下的 82 款是 `SS` 与 `SS plus` 两边都没有文件，属数据缺失，不是代码问题。
`FC hack` / `SFC hack` 的 metadata 与原版**不同**，是独立清单，本就无此问题。

改动落在 `index.py`（schema + 回退）、`install.py`（`rom_sources` / `copy_cover` / `remove`）、
`cover.py`（`CoverLoader` / `SizeLoader`）、`batchmedia.py`。

**右栏另外两处调整**：状态标签放回标题下方，色块高度用 `f_small.line_h`
（16 号字实际渲染高 24px，按字号画会溢出）；标签右侧显示 ROM 文件大小
（`SizeLoader` 后台 `os.walk` 建大小表，因为 `rom_bytes` 只在安装时写入，扫库大多是 `None`）。

**已知未修**：NAOMI 有 53 个 `gdl-xxxx.chd` 没进索引，是根目录同名 `.zip` 的重复收录；
GBA 有 3 个汉化版 zip 没有封面。都不是 bug。

## 0.4.2 — 2026-10-12

**新增：商店详情页里直接预览游戏视频**

NAS 上每个游戏的 `media/<游戏>/video.mp4` 其实一直都在（实测 FC 520/520、GBA 465/465、
SFC 355/355、PSP 471/471、DC 104/104、GB 108/108、N64 70/70 —— **基本 100% 覆盖**），
但以前只有封面能看。现在**在 ROM 商店的详情页按 `X`** 就能原地看这段预览片 ——
装之前先确认要找的是不是这个版本。

| 项 | 做法 |
|---|---|
| 入口位置 | 商店详情页（`A` 安装、**`X` 播放预览**、`Y` 卸载） |
| 片源 | NAS 上的 `media/<ROM 去扩展名>/video.*`，**不复制到掌机**（不占 SD 空间、不占下载时间） |
| 播放引擎 | `mpv --vo=drm --ao=alsa --hwdec=auto-safe`（本机无 X11/Wayland，只能 DRM 直出） |
| 没有片源 | 底栏直接显示「无预览」，按 X 只提示一句，不影响安装 |

**为什么要在进程外绕一圈**：商店界面自己占着 KMS/DRM 显示，而 mpv 也要用同一块屏，
两者不能并存；掌机又没有 Python 的视频解码绑定，界面进程自己解不了视频。
所以走接力：

1. 界面写好请求文件（第 1 行视频路径、第 2 行游戏 id）后**以退出码 42 退出**，让出显示
2. 外层 `RomStore.sh` 见到 42，用 mpv 播那个文件（CIFS 挂载点直读；实测 2 MB / 29 秒 /
   h264 320x240 + aac，2.4G WiFi 流播完全流畅，硬解 `h264_v4l2m2m` 可用）
3. 播完 `RomStore.sh` 用 `run.py --resume <游戏 id>` 把界面拉回来，
   **精确落回刚才那一款游戏的详情页**（列表光标也对齐，按 B 退回正好停在它上面）

- `media.py` 新增 `find_video()`：与封面同一套路径推导 + 大小写不敏感 + 扩展名兜底。
- `run.py` 新增 `--resume <游戏id>`；`ui.goto_game()` 由私有工具函数提升为正式接口。
- 离线自测台新增 7 条断言（精确命中 / 大小写 / 非 video 命名 / preview.webm / 目录不存在）。
- 曾短暂做过一个独立的「视频播放器」Options 入口，**已撤销**（改为详情页内预览）；
  `install.sh` 升级时会自动清掉旧入口，`uninstall.sh` 也仍会清理它。

**新增：`/roms` 只读自愈（针对掉电导致的 FAT 表截断）**

装游戏时如果报 `Errno 30 Read-only file system`，**不是商店的 bug，是 TF 卡的 exFAT
分区被写坏了**。成因是非正常关机（掉电 / 拔卡）把 FAT 表截断，损坏的目录表一旦被读到，
内核就会按 `errors=remount-ro` 把整个分区转只读：

```
exFAT-fs (mmcblk0p3): Volume was not properly unmounted. Some data may be corrupt.
exFAT-fs (mmcblk0p3): error, found bogus dentry(...) beyond unused empty group(...)
exFAT-fs (mmcblk0p3): Filesystem has been set read-only
```

现在装好 ROM 商店就带一个开机自愈脚本（`roms-fs-selfheal.service`），
下次再遇到这种情况**开机就自动修好，不用 SSH 排查**：

```
检测到 /roms 为 ro → 停 ES 和 filebrowser → 卸载（Tools 与 roms 成对）
→ fsck.exfat -y → sync → 重新挂载 → 补挂 /opt/system/Tools
→ 真实写入验证 → 恢复服务
```

设计上有一条硬规矩：**自愈失败绝不能阻塞开机**。修不好就带着只读分区进桌面，
而不是卡在黑屏。实测健康状态下 1 秒就返回，完全不打扰使用。

**这是「善后」不是「预防」。** exFAT 本身没有 journal，掉电写坏 FAT 表是它的固有弱点。
根治要么换文件系统（ext4 有 journal，但会失去 Windows 插卡直读），
要么 —— 至少 —— **关机走正常流程，别直接断电拔卡**。

## 0.4.1 — 2026-10-11

**新增：封面三层体检（`--covers`）**

用户报「GBA 封面看不到」，查了三层才发现只看一层会误判：

| 层 | 问题形态 | 查什么 |
|---|---|---|
| ① 引用层 | gamelist 的 `<image>` 指向的文件不在 | 逐条比对路径与磁盘 |
| ② 内容层 | 文件在，但内容已被 exFAT 截断写坏 | 查图片文件头（PNG/JPEG/GIF/BMP 魔数）|
| ③ 生效层 | 文件都好，但 **ES 根本没重读** | 比 gamelist 与 `imagecache.db` 的 mtime |

```bash
python3 -m romstore.cli --covers               # 全系统体检
python3 -m romstore.cli --covers gba           # 单系统（ES 名或 NAS 目录名都认）
python3 -m romstore.cli --covers --check-content --cover-sample 30
python3 -m romstore.cli --covers --verbose     # 打印缺失样例
```

- 新函数 `batchmedia.audit()`；`_iter_games()` 文本级切 `<game>` 块（不重新序列化 XML）、
  `_looks_like_image()` 查文件头（能抓出 exFAT 截断留下的全零/垃圾图）
- **生效层是关键**：ES 把解码过的图记在 `/home/ark/.emulationstation/imagecache.db`
  （纯文本，格式 `路径|字节|宽|高`）。**能写出宽高 = ES 真解码成功了**。
  判断「ES 有没有重读」优先看 **ES 进程启动时间**（读 `/proc/<pid>/stat`，
  比缓存 mtime 可靠 —— **ES 是懒写缓存的**，只在切系统/退出时才落盘）；
  拿不到进程信息才退回比缓存 mtime。判定为「没重读」时直接给出修复命令
- **`mapp.resolve()`（新增）**：宽容解析系统名 —— 用户看到的 ES 名（`gba`）与
  内部用的 NAS 目录名（`GBA`）经常不同（`nes` ← `FC`、`ppsspp` ← `PSP`），
  两种写法、任意大小写都认。`--covers` / `--import-media` 都受益

**修复：封面幂等判断在大小写不敏感的文件系统上失效**

`--import-media` 的幂等判据是「目标文件大小 + 首尾 4KB 相同就跳过」。但
**NAS 上的封面文件名大小写不统一**（`boxFront.png` 与 `boxfront.png` 并存），
而我们算出的目标名统一是小写 `boxfront.<ext>`。在 exFAT/Windows 这类
**大小写不敏感**的文件系统上这不是问题（同一个文件），但判据本身不够稳。

新增 `_existing_cover(dst)`：**大小写不敏感地**在目标目录里找已存在的封面，
用真实路径去比对。附带 `run()` 里加「大小写修正」——磁盘真实名与标准名不同时，
把 gamelist 更新成**磁盘上的真实名字**（对将来迁到大小写敏感的文件系统是保险）。

实测收益：GBA 的 437 个已装游戏里，有 9 个 NAS 源已被更新过（内容与掌机不同），
修复后这 9 个被正确识别并更新，**其余 428 个正常跳过**，
再跑一次 `--import-media gba` 待导入数为 **0**（幂等成立）。

**新增：批量补封面（`--gaps` / `--import-media`）**

0.4.0 的封面只在**装 ROM 时**顺带导入。装机时跳过封面（`--no-media`）、
或 0.4.0 之前就装好的老游戏，在 ES 里就一直是无图方块，只能一个个重装。

本版补上批量补图入口，**只补封面，不碰 ROM**：

```bash
python3 -m romstore.cli --gaps                     # 体检：哪些系统缺封面、缺几张
python3 -m romstore.cli --import-media gba         # 补 GBA 的全部缺失封面
python3 -m romstore.cli --import-media gba --dry-run
python3 -m romstore.cli --import-media gba --media-limit 10
python3 -m romstore.cli --import-media gba --no-gamelist   # 只放图，不改 gamelist
```

- 新文件 `src/romstore/batchmedia.py`：
  - `systems_with_gaps()` —— 遍历已装游戏，按系统统计「已装的 / 有封面的 / 缺的」
  - `plan()` —— 给出待补清单（默认只列缺的），复用 `media.find_asset` 找图
  - `run()` —— 复制素材 + `install.upsert_gamelist` 补 `<image>` 条目
- **幂等**：`size + 首尾 4KB` 比对判断目标是否已一致，一致即 `skipped`，
  不重复走 CIFS 全量传输（GB 级素材上这很关键）
- 按探测到的**布局**落图，flat / subdir 都对（依赖下面的布局修复）
- `--media-limit N` 而非复用 `--limit`：`--limit` 已被 `--search` 占用（默认 20），
  复用会让批量导入莫名只处理 20 个
- 返回统计：`copied / failed / skipped / nocover / bytes / gamelist_added / gamelist_updated`

**修复：GBA / PSP 这类「每游戏一子目录」的封面布局装不对**

用户反馈「GBA 封面没了」。查下来**不是封面丢了**——GBA 的 462 个盒绘子目录
一个都没少，gamelist 里 469 条 `<image>` 也全部有效。真正的 bug 在 0.4.0 的
`media.py` 里：

> 它只认一种掌机布局 —— 散文件式 `<媒体目录>/<基准名>.<扩展名>`。
> 但掌机上其实**并存两套**：

| 系统 | 布局 | 实例 |
|---|---|---|
| `nes` | 散文件 | `img/1943.png` |
| `gba` / `psp` | 每游戏一子目录 | `media/黄金太阳/boxfront.png` |

0.4.0 的 `pick_media_dir()` 只看目录**名**（`img` / `media`），不看布局，
所以给 GBA 新装游戏时会把封面写成 `media/黄金太阳.jpg` —— 一个散文件，
而同一系统里另外 462 个游戏都是 `media/黄金太阳/boxfront.png`。
既跟邻居长得不一样，还有可能撞上已存在的同名**目录**。

### 修复内容

1. **`pick_media_dir()` 现在返回 `(目录名, 布局)` 二元组**，布局取
   `'flat'`（散文件）或 `'subdir'`（每游戏一子目录）。判据两条，
   先证据强的：
   - 从 gamelist 已有的 `<image>` 路径反推 —— 这是 ES 实际在读的东西，
     `./media/X/boxfront.png` 三段即 subdir，`./img/1943.png` 两段即 flat
   - 再探测文件系统：某候选目录里若「有图子目录数 > 0 且散图片数 == 0」
     判 subdir，反之判 flat（实测数据很干净：GBA 462/0，nes 0/928）
   - 都探不到才用默认 `('img', 'flat')`

2. **新增 `target_image_rel(media_dir, layout, rom_name, ext)`**，按布局算出
   相对路径与逐级目录片段：
   - flat → `img/1943.jpg`
   - subdir → `media/黄金太阳/boxfront.png`

3. **`_install_cover()` 用新 API**；subdir 布局下若同名**文件**挡路
   （历史遗留的散文件）会给出清楚原因，而不是让 `makedirs` 抛个看不懂的错。

4. **卸载也认 subdir 布局**：新增 `_find_dir_ci()`（大小写不敏感找子目录，
   且**不做前缀匹配**，`黄金太阳` 不会误伤 `黄金太阳2`）与 `_images_in_dir()`。
   清理时只删图片；**目录里若还有 `video.mp4` 之类的非图片则保留目录**，
   避免顺手删掉用户其它素材。

5. **离线自测从 27 条加到 60 条**，新增覆盖：gamelist 反推两种布局、
   磁盘探测两种布局、`target_image_rel` 两套路径、子目录卸载清理、
   `黄金太阳` vs `黄金太阳2` 不误伤、有 video 时不删目录、
   `audit()` 的三层解析、`mapp.resolve()` 的宽容命名、
   `_existing_cover()` 的大小写不敏感匹配，以及 `_es_proc_start()` 的健壮性。

### 实机验证附带发现（0.4.1）

- **ES 重载 gamelist 的正确姿势不是 `systemctl restart`**，而是 ArkOS 的 wrapper 机制：
  `touch /tmp/es-restart && kill <ES主进程PID>`，wrapper 会自动拉起。
- **gamelist 真身不在 `~/.emulationstation/gamelists/`（那里是空的）**，
  而是在 **`/roms/<系统>/gamelist.xml`**。
- **ES 日志默认关闭**（`es_settings.cfg` 里 `LogLevel = disabled`），排查时看不到报错。
- **中文名统计别用 `find`**：`find -name "boxfront*"` 少算了一百多个，
  Python `os.listdir` 才是准的。
- **`/proc/<pid>/comm` 最多 15 字符**：`emulationstation` 会被截成 `emulationstatio`，
  用全名相等判断永远匹配不上，要用**前缀匹配**；且 comm 可能含非 UTF-8
  字节，读取必须带 `errors=replace`。
- **exFAT 大小写不敏感**：`boxFront.png` 与 `boxfront.png` 是同一个文件
  （`/tmp` 这种 ext4 上是两个）。覆盖写入时会**保留原有名字的大小写**。
- **`--gaps` 的性能瓶颈在 NAS**：每个已装游戏都要走一次 `find_asset`（CIFS 往返）。
  GBA（437 已装）要 31 秒、FC 要 24 秒，而 PSP（3 已装）只要 0.45 秒。

---

## 0.4.0 — 2026-10-11

**刮削素材（封面）导入**（本次）

0.3.x 只送 ROM，装完的游戏在 EmulationStation 里是**一格子没图的方块** ——
NAS 上明明有整目录的 `boxFront.jpg` / `logo.png`，但一个字节都没过来。
本次补上封面导入。

### 新增

1. **安装时自动导入封面**（`media.py` + `install.py`）。
   安装一个游戏现在做四件事：复制 ROM → **复制封面** → 生成 m3u（多碟）→
   写 gamelist（含 `<image>` 路径）。

2. **NAS 素材路径直接推导，不枚举目录。**
   实测规律：`media/<ROM 文件名去扩展名>/`，如
   `/mnt/Roms/FC/1943.zip` → `/mnt/Roms/FC/media/1943/boxFront.jpg`。
   这条关系让素材路径可以**直接算出来**。对照：枚举 NAS 上 28,000 个
   media 子目录要 **24.8 秒**，推导是 **0 秒**。

3. **写入显式 `<image>` 路径，不猜掌机约定。**
   EmulationStation 读的是 gamelist 里 `<image>` 的**显式路径**，不是按
   `<game>` 名推导。所以写 `./img/1943.png` + 把文件放到 `img/1943.png`
   就一定生效。而**目录名要沿用该系统已有的习惯**——实测 `nes` 用 `img/`
   （926 条）、`gba`/`psp` 用 `media/`，两套并存，硬编码一个名字会打架。
   `pick_media_dir()` 先从 gamelist 里已用的前缀学，再退到磁盘上已存在的
   候选目录，最后才用默认 `img/`。
   （**注：0.4.1 起此函数同时学「布局」，见上文**）

4. **大小写不敏感的封面查找。** 实测 GBA 里 `boxFront.jpg` 与
   `boxfront.jpg` 并存，FC 全是 jpg、ATARI2600 全是 png、3DO/GBA 混用。
   硬拼 `boxFront.png` 会在 CIFS（大小写敏感）上静默读不到，
   所以是"列目录 → 按小写名匹配"。

5. **三级回退**：精确名（boxFront/box/cover/front/folder/…+jpg/png/webp）
   → 目录里像封面的第一张图 → 目录里唯一的图片（`logo` 请求不参与这一级，
   免得把随便一张图当 logo）。

6. **详情页右侧封面预览**（`sdl.py` 新增 `ImageCache`）。
   装之前就能看到 NAS 上的封面长什么样，确认刮削对不对。
   按 (路径, 尺寸) 缓存已缩放 surface，LRU 上限 8 张（掌机只有 1 GB 内存）；
   等比缩放不变形；加载失败按"无封面"处理，**一张坏图不会把 UI 拖崩**。

7. **卸载时一并清理封面。** 按 `<基准名>.<图片扩展名>` 精确匹配
   （`1943.png`、`1943.jpg` 都清），且只认图片扩展名 ——
   不会把同名 ROM（`1943.zip`）或长名文件（`1943X.png`）误删。

8. **CLI 与 UI 都显示封面状态**：`romstore.cli --install N` 打印
   `封面: ./img/1943.png (118 KB)` 或 `封面: 未导入（NAS 上没有封面）`；
   UI 的安装成功提示与卸载提示同样带上。
   新增 `--no-media`（只装 ROM，不导封面）。

### 修复（真机验证时发现，都很关键）

1. **`SDL_BlitScaled` 在这台掌机的 libSDL2 里不存在，会拖崩整个程序。**
   声明 `argtypes` 时 ctypes 立刻抛 `AttributeError: undefined symbol:
   SDL_BlitScaled` —— 而这是在 `Bind.__init__` 里，于是**连主界面都起不来**，
   不只是没封面。现在**先探测符号再声明**，缺了就置 `None`，
   并备了一条**纯 Python 最近邻缩放**路径（只读像素、写像素，不依赖任何
   可选符号）。实测纯 Python 路径能把封面正确缩放出来。
   教训：SDL 版本号（这台报 2.32.10）**不能**用来判断符号在不在 ——
   发行版会改 lib 版本号，API 却可能缺。

2. **JPEG 解出来是 24 位 BGR（alpha=0），照搬会得到全透明像素。**
   实测 `IMG_Load` 出的封面是 `BytesPerPixel=3`、`Amask=0`；纯 Python 路径
   原本给第 4 字节补 `0x00` → 整张图 alpha=0 → **画出来完全透明，
   看着就是"没封面"**。改为先 `SDL_ConvertSurfaceFormat` 统一成 ARGB8888
   （颜色顺序与 alpha 一起就对了）；没有该符号时，纯 Python 路径补 `0xFF`。

3. **`SDL_Surface.format` 事后改 `_fields_` 不生效。**
   早先把 `format` 声明成 `c_void_p`、类定义完再改回
   `POINTER(SDL_PixelFormat)` —— ctypes 在首次访问字段后就锁定布局，
   改不生效，读出来是个 int，缩放时报
   `'int' object has no attribute 'contents'`。改为**把 SDL_PixelFormat
   定义在 SDL_Surface 之前**，`format` 直接声明为指针。

4. **详情页封面与简介文字重叠。** 简介原来按整屏宽度折行，会从封面图后面
   穿过去。现在简介**从封面底边之后开始**，左栏信息按像素宽度截断。

5. **`run.py --probe` 新增封面相关两行**：SDL_image 能否解码、
   本机用哪种缩放（`SDL_BlitScaled` 原生 / 纯 Python）。
   以后一眼就能看出这台机器走的是哪条路。

### 说明

- **只做图片，不做视频。** `video.mp4` 单张 2 MB，而掌机 SD 写速只有
  **4.8 MB/s** —— 下 20 个游戏多花一分半，还会吃掉本就不多的 `/roms` 空间
  （实测剩 13 G）。视频留待后续版本按需选装。
- NAS 上没有封面时**静默跳过，不算安装失败** —— 很多游戏本来就没刮削。
- **已在真机（r36s, 10.168.1.129）完整验证**，含一次真实游戏
  （`公路之星` = Rad Racer）的 安装 → 封面导入 → 详情页预览 → 卸载 往返：
  封面字节与 NAS 源 `cmp` 一致、gamelist BOM/CRLF 无损、卸载不误删同名邻居
  封面、无 `.part` 残留、重复安装只更新不新增。详见 RELEASE-NOTES 第四节。
- 离线自测在 `tests/test_media.py`（**不随分发包发出**），27 项断言：
  `python3 tests/test_media.py`。

## 0.3.1 — 2026-10-11

**首次真机安装后的修正**（在 `r36s`，10.168.1.129 上跑完一次完整的全新安装）

0.3.0 的安装器逻辑没错，但它没在真机上跑过 —— 真跑一遍才发现几个
「会让人卡住」和「会让人误判」的地方。

### 修复

1. **`mount.cifs` 缺失时安装器只会甩手停下。**
   真机上中招：旧文档写着"装过 cifs-utils"，实际系统里没有（用户态包未持久化），
   `--check` 直接标 ✘ 退出，要人自己 SSH 上去敲 apt。现在安装器**自动补装**：
   把缺的包攒起来一次 `apt-get install -y`，装完复检，仍缺才算失败。
   `--no-auto-apt` 可关掉；`--check`（干跑）一律不补装 —— 它承诺不动系统。

2. **`--check` 只查用户态 helper，不查内核 cifs 支持。**
   `mount.cifs` 装不装得上是一回事，内核支不支持 cifs 是另一回事，混在一起
   会导致"依赖全过但挂载必失败"。现在单独探测：`/proc/filesystems` 有 cifs
   → 内建；否则 `modprobe cifs` → 可加载；都不行才判死并说明"换内核/固件"。
   （实机是**内建**的，`/lib/modules/.../kernel/fs/cifs/` 目录其实不存在。）

3. **装完 `nas-roms.service` 显示 `inactive`，但挂载其实是好的** —— 容易误判成故障。
   根因：安装器第 ⑩ 步直接调挂载脚本，绕过了 systemd，于是「服务状态」和
   「实际挂载」对不上。改为经 `systemctl start nas-roms.service` 建立挂载，
   并在之后对齐一次状态；两者一致时额外确认一行。服务是 `RemainAfterExit=yes`，
   幂等无副作用。

4. **`--es-dir` 校验太弱。** 原来只 `grep "<path>$ES_DIR/"`，
   但真机 `es_systems.cfg` 有多份副本（`.dual` / `.rk3326` / `.bak`），
   别的系统的 path 也可能长得一样。改为**抽出 `name=options` 那个块里的
   `<path>`** 再比对；不一致就报错并直接给出正确的 `--es-dir` 值。
   另外：用户没显式指定时，安装器会**自动从 `es_systems.cfg` 探测** options 目录，
   不再依赖硬编码默认值。

5. **挂载脚本缺 helper 时的报错指向不明。** `mount -t cifs` 在缺 `mount.cifs`
   时报的是 `bad option; ... you might need a /sbin/mount.<type> helper program`
   —— 看着像参数写错。脚本现在先探一次并给出"装 cifs-utils"的提示
   （仍照常尝试，因为内建 cifs 的某些配置不带 helper 也能挂）。
   `source.py` 的中文提示表也加了 `helper program` → `apt-get install cifs-utils`。

6. **全新安装时第 ④ 步「备份会被覆盖的文件」下面一片空白**，
   看着像漏做了。现在会明确说"全新安装，没有需要备份的旧文件"，并清理空目录。

### 新增

- **安装后自动自检（第 ⑬ 步）** —— 装完立刻复核 7 件事：程序本体、入口脚本
  可执行、gamelist 条目、`source.json`、NAS 挂载、索引可读、`systemd` 自启，
  外加一次无头探针（SDL 视频初始化 / 手柄数量 / 中文字体）。
  实机教训是「装完看起来全绿」不等于真能用，这一节把该为真的都验一遍，
  最后给出「全部通过」或「有 N 项未通过」的一行结论。
- **卸载后自动自检** —— 复核无残留文件、无残留挂载、无残留菜单条目。
- `install.sh --no-auto-apt`。

### 说明

- 本次 0.3.1 的改动**已在一台真机（r36s, 10.168.1.129）上完成一次全新安装验证**；
  0.3.0 遗留的「卸载 → 重装往返未验证」这一条**仍然没测**，照实保留。
- 真机实测：`sha256sum` 一致、`--check` 全过、安装无报错、
  索引 8,210 款（21.4 秒）、ES 入口注册成功、探针全绿。

## 0.3.0 — 2026-10-11

**打包与分发**（本次）

- 新增 `install.sh`：一键安装 / 幂等升级。含前置检查（python3 / mount.cifs /
  flock / nc / SDL2 / SDL2_ttf / Noto CJK 字体 / ES options 目录），
  装错环境会在动手之前就停下。
- 新增 `uninstall.sh`：反向卸载，可 `--keep-mount`（保留 NAS 挂载）或
  `--purge`（连索引/配置/备份一起删）。
- 部署路径**模板化**：`@@STORE_DIR@@` / `@@ES_DIR@@` / `@@UID@@` / `@@GID@@`
  由 `install.sh` 替换，替换不干净会直接报错退出 —— 支持非 `ark` 用户、
  非 `/opt/system` 的 ES 目录。
- **修掉一个真 bug**：`cli.py --register` 写的入口路径是 `Tools/RomStore.sh`，
  而 `es_systems.cfg` 里 options 系统的 `<path>` 是 `/opt/system/`，
  入口脚本实际放在 `/opt/system/RomStore.sh`。结果 `--register` 会写出一个
  **指向不存在文件**的入口，`--unregister` 也找不到条目。已改为 `RomStore.sh`。
- `config.py` 新增 `ES_OPTIONS_DIR`（可用 `ROMSTORE_ES_OPTIONS` 覆盖）。
- 安装时会把 ES 崩溃循环保护作为可选组件（`--with-es-guard`）一并带上。
- 新增 `make-dist.py`：把项目打成 `ark-romstore-<版本>.tar.gz` 与 `.zip`，
  统一 `ark-romstore-<版本>/` 前缀、规则化权限位、排除 `__pycache__`，
  并在打包前自检必备文件与模板占位符。同时生成 `SHA256SUMS`（落在包外，
  避免哈希自我引用）。
- 新增 `RELEASE-NOTES.md` 版本说明：面向装机的人 —— 能力清单、版本演进、
  实测数据、运行环境要求、已知限制、验证状态（含「install.sh 未真机往返」的
  明确披露）。
- **修掉四个 bug**（前两个是「装了但装不上」，后两个是「包看着对其实不对」）：
  1. 两个模板脚本的**注释**里写有「不应残留任何 `@@` 占位符」，这句
     本身含字面 `@@`，而 `install.sh` 用 `grep -q '@@'` 判断替换结果 ——
     于是**每次安装都会误判「替换未完成」并中止**。注释改用词，并把
     检查收紧为 `@@[A-Z_]\{1,\}@@`，只认真正的占位符。
  2. 包内文件权限位取的是宿主文件系统（Windows 下为 `0666`），
     `install.sh` 落地后**没有可执行位**，`./install.sh` 直接
     `Permission denied`。改为在打包时对每个条目**显式写死 mode**，
     与宿主无关。
  3. `make-dist.py --epoch` 只固定了 tar 条目的 mtime，**漏了 gzip 容器
     头部的时间戳**（`tarfile.open(mode='w:gz')` 内部用 `time.time()`，
     且不接受 `mtime` 参数）—— 「可复现构建」是假象：背靠背跑两次常常
     落在同一秒才碰巧 md5 一致。改为自己套一层 `GzipFile` 显式传
     `mtime`，**跨秒连打三次**复验通过。
  4. `SHA256SUMS` 用 `Path.write_text()` 写，Windows 上 `\n` 被翻译成
     `\r\n`，Linux 侧 `sha256sum -c` 读到行尾 `\r` 报
     `No such file or directory`。改为二进制写入，并在 Linux 侧复核通过。

## 0.2.0 — 2026-10-11

**掌机上直接改商店源**

- 新增 `source.json` 作为商店源的**唯一真相**，开机挂载脚本与商店共用一份，
  不再各说各话；改造 `/usr/local/sbin/nas-roms-mount.sh` 读它。
- 配置丢失（文件不存在 / 坏 JSON / 无 python3）时回落内置默认值，
  **保证开机永远挂得上**。
- Start 菜单新增「修改商店源」：主机 / 共享 / 账号 / 密码四个字段，
  带**手柄软键盘**（数字 / 小写 / 大写 / 符号四页，故意不含逗号 ——
  cifs 的 `-o` 是逗号分隔的）。
- 内置 `/24` 网段扫描（探 445 端口）列候选主机，1.97 秒出结果。
- 挂载失败时把 `mount` 的诊断**原样带出**并映射中文提示；
  主机不可达时**预检拦截，不碰现有挂载**。
- 新增 CLI：`--source-show / --source-set / --source-reset / --scan-hosts /
  --probe-host / --mount / --umount`。

## 0.1.0 — 2026-10-11

**P1 建店，跑通**

- `index.py`：L1 SQLite 索引，按 `(es_path, basename)` 去重，
  指纹增量（mtime+size）跳过未变的 NAS 目录。冷建 10.745 秒、
  增量 1.008 秒、8,210 款游戏、`index.db` 1.76 MB。
- `mapp.py`：NAS 目录 → ES 系统映射表（68 条，含置信度），
  并在解析 `es_systems.cfg` 前修复其中的裸 `&`（`2>&1` 使该文件不是合法 XML，
  ElementTree 会直接解析出 **0 个系统**）。
- `meta.py`：Pegasus `metadata.pegasus.txt` 解析（含多碟 `files:`、core 推导）。
- `install.py`：**文本级 gamelist upsert** —— 原有条目带 BOM 且换行风格不一
  （`nes` 全 CRLF、`psp` 全 LF），重新序列化会破坏格式，因此按字节插入，
  并逐文件探测换行风格。安装→卸载一个来回后 md5 回到原值。
- `sdl.py` / `ui.py`：SDL2 + SDL2_ttf（Python ctypes，零 pip 依赖）的图形界面。
  **dialog 路线被实测推翻**：掌机 `consolefonts/` 最大只有 14.6 KB，无 CJK 字形，
  中文全变空白。
- 卸载进回收站 `/roms/.romstore-trash/`；未安装的游戏不会谎报「已卸载」。
- 入口注册进 ES 的 Options 菜单。
