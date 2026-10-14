# ROM 商店 —— 掌机上的局域网游戏商店

在掌机上直接浏览 NAS 上的游戏库，选中就装到本机，装完即出现在
EmulationStation 里。全中文界面，**不需要电脑中转、不需要手机 App**。

- 数据源：NAS 上一个 SMB 共享，**不需要在 NAS 上装任何服务**
- 客户端：掌机自己是唯一客户端，元数据缓存 + 索引都在掌机上
- 界面：SDL2 图形界面，640×480，手柄操作
- 依赖：系统自带的 Python 3 + SDL2，**零 pip 包**

---

## 支持的平台

**同一个安装包，安装时自动识别。** 三族固件用法完全一样，只有底层路径与传输方式不同。

|  | ArkOS4Clone | AURKNIX / ROCKNIX | DarkOS（dArkOS） |
|---|---|---|---|
| 机型 | R36S / R36 Ultra 等 | Powkiddy X35S 等 RK3566 | R36S 等 aarch64 掌机 |
| 架构 | aarch64，根文件系统可写 | aarch64，**根是只读 squashfs** | aarch64，根文件系统可写 |
| 持久分区 | `/` | **只有 `/storage`** | `/` |
| 用户 | `ark`（用 sudo 提权） | `root`（**没装 sudo**） | `ark`（用 sudo 提权） |
| NAS 传输 | 内核 cifs，`vers=2.1` | **内核没有 cifs → rclone FUSE** | 内核 cifs，`vers=2.1` |
| 显示 | SDL / KMSDRM 直出 | SDL / **Wayland（Sway）** | SDL / KMSDRM 直出 |
| ES 入口 | Options → `/opt/system` | Ports → `/storage/roms/ports` | Ports → `/roms/ports` |
| 程序目录 | `/home/ark/.romstore` | `/storage/.romstore` | `/home/ark/.romstore` |
| 挂载点 | `/mnt/Roms` | `/storage/nas-roms` | `/mnt/Roms` |
| 自启 | `/etc/systemd/system` | `/storage/.config/system.d` | `/etc/systemd/system` |
| 中文字体 | 系统自带 NotoCJK | 装的时候从 scummvm 主题借一份 | 系统自带 NotoCJK |
| `/roms` 自愈 | 装（exFAT 掉电会整盘只读） | 不适用（分区由固件 udev 挂载） | 装（同 ArkOS） |

**DarkOS 只差一张映射表。** 它的文件系统布局与 ArkOS 几乎逐项相同
（根可写、`/home/ark` 属主是 `ark`、`/opt/system` 在、`/roms` 也是带
`errors=remount-ro` 的 exFAT），唯一的实质差异是 **ES 的系统名走 Batocera 风格**：
**没有 `options` 系统**（入口只能走 `ports`），`PSP` 只有 `ppsspp` / `pspminis`
（没有裸 `psp`），`Atomiswave` 在 cfg 里写作 `Sega Atomiswave` 却在 `/roms/atomiswave`
……所以代码里它复用 ArkOS 的绝大部分分支，只换了入口系统名与映射表。

识别顺序：`ROMSTORE_PLATFORM` 环境变量 → `/etc/os-release` 自报 → hostname
→ 结构性特征（有没有 `/storage/.config/system.d`、`/storage/roms`、`/opt/system`），
结论写进 `<程序目录>/platform.json`；运行期所有路径都从那里读。
⚠️ **ROCKNIX 的判据必须排在 DarkOS 前面** —— DarkOS 也有 `/opt/system`
那个 ArkOS 特征，先判 ROCKNIX 更保守。

> DarkOS 是三族里最难认的一个：它的 `/etc/os-release` **自报 `Debian GNU/Linux`**，
> 既没有 ROCKNIX 字样、也没有自己的标记，只能靠 hostname（实测 `darkosre-r36`）。
> 万一没认出来，强制指定即可：`sudo ./install.sh --platform darkos`。

---

## 界面长什么样

以下截图**全部是从真机（`r36s`）上直接抓帧导出的**，不是设计稿。
640×480 就是掌机的原生分辨率。

### 系统页：68 个系统，按游戏数量排序

![系统页](docs/screenshots/01-systems.jpg)

### 游戏页：左边封面卡片网格，右边选中游戏的详情

一屏 4 列 × 4 行共 16 款。右边常驻显示标题、安装状态、**ROM 文件大小**、
**视频预览**和简介 —— **不用按 `A` 进详情页**。

![卡片网格与右栏详情](docs/screenshots/02-grid.jpg)

- 卡片上**只有封面**（0.6.0 去掉了下方那行标题，腾出的高度全给封面，
  单张从 71px 放大到 91px）；没有封面的游戏才在卡片中间写名字兜底
- 选中卡片是**边框高亮**（不是蓝底色块），已安装的右上角有绿点
- 卡片封面、视频、简介、文件大小都是**后台慢慢加载**的，进系统时可能还空着，一两秒填满
- 视频直接**串流播 NAS 上的源文件**，不复制到掌机；`X` 键全屏预览
- 右栏的内嵌预览**按机器上有什么工具**选引擎，而不是写死平台：有 `ffmpeg`
  命令行就走 ffmpeg 管道（ArkOS / DarkOS），只有 `mpv` 就走 mpv 帧服务
  （AURKNIX/ROCKNIX 没装 ffmpeg 命令行，但有自带 rkvdec 硬解的 mpv 0.36），
  两个都没有则只显示封面 —— 详见 `romstore/vplayer.py` 的 `create()`

### 视频加载中的样子

刚切到某个游戏、视频还在拉流时，右栏显示占位提示；封面已全部就位：

![视频加载中](docs/screenshots/03-video-loading.jpg)

---

## 一、前置条件

### 掌机

三族固件任一即可（见开头的平台表）：

- **ArkOS4Clone**（或同源的 aarch64 ArkOS 分支），用户 `ark`
- **AURKNIX / ROCKNIX**（Powkiddy X35S 等 RK3566），用户 `root`
- **DarkOS / dArkOS**（R36S 等），用户 `ark`

另外还需要：

- 有 SSH（安装时要用），或能用 U 盘把安装包拷进去
- 与 NAS 在同一局域网

ROCKNIX 侧的两个额外要求（安装器会自动处理，列在这里是为了出事时好排查）：

- 系统自带 `rclone`（ROCKNIX 是自带的，在 `/usr/bin/rclone`）。
  内核**没有** cifs，所以 NAS 挂载只能走 rclone 的 FUSE。
- 需要 `fusermount`。ROCKNIX 只带了 v2 版，而 rclone 找的是 `fusermount3` ——
  安装器会在 `<程序目录>/bin` 建一个同名软链把名字补上。

**DarkOS 侧**同样有两个（这台根可写、有 apt，安装器会自己补装）：

- 内核里有 cifs，但**没装 `mount.cifs`** → 补 `cifs-utils`
- **没有 `fsck.exfat`** → 补 `exfatprogs`（`/roms` 只读自愈要用）

> ⚠️ DarkOS 的 `apt-get update` 要跑满 **~100 秒**（索引来自 `deb.debian.org`），
> 别给它加短超时，否则 Packages 索引下不完，后面会一路报
> `Unable to locate package`。

### NAS（或任何一台开着 SMB 的机器）

只要求一件事：**开着 SMB（445 端口），开一个共享，把游戏目录放进共享根下。**
NAS 上**不需要装任何服务**，本程序**只读不写**，不会往 NAS 上放任何东西。

#### 目录怎么摆

**规则只有一条：每个平台一个「顶层目录」，里面放一份 `metadata.pegasus.txt`。**

```
Roms/                            ← 这是 SMB 共享名，随便叫什么都行
├── FC/                          ← 顶层目录 = 一个平台
│   ├── metadata.pegasus.txt     ← 【必须】文件名必须一字不差，见下
│   ├── media/                   ← 【可选】素材目录，见下
│   │   └── 1943/
│   │       ├── boxFront.jpg     ← 封面（安装时复制到掌机）
│   │       ├── logo.png         ← 标题图
│   │       └── video.mp4        ← 预览视频（不复制，直接串流播）
│   ├── 超级马里奥.nes           ← ROM 文件，位置随意
│   ├── 1943.zip
│   └── ...
├── SFC/                         ← 另一个平台
│   └── metadata.pegasus.txt
├── PSP/
│   └── metadata.pegasus.txt
└── ...
```

**几个容易踩的点：**

| 要求 | 说明 |
| --- | --- |
| 平台目录必须在**共享根下** | 不能是 `Roms/roms/FC/` 这种嵌套。程序只扫共享根的**一层**子目录 |
| 文件名必须**一字不差** | `metadata.pegasus.txt`（全小写、`.txt` 后缀）。写成 `metadata.txt` / `pegasus.txt` / `METADATA.PEGASUS.TXT` 都不会被识别 |
| 目录名用什么随意 | 常见短名最好：`FC` `SFC` `GBA` `PSP` `MD` `PS1` `SS` `DC` `3DO`… **认不出来这个平台会被跳过**，见本节末尾 |
| ROM 可以放在子目录里 | 如 `D no Shokutaku/xxx.chd`，`metadata` 里的 `file:` 写相对路径即可 |
| 共享名无所谓 | 装的时候填你实际的共享名（默认 `Roms`） |

> **只有一个平台目录行不行？** 行。放一个也能用，只是商店里只有一个系统可选。

#### `metadata.pegasus.txt` 怎么写

这是**唯一必须自己准备的文件** —— 它是游戏清单，程序靠它才知道有哪些游戏。
格式是 [Pegasus _metadata](https://pegasus-assets.org/metadata)（标准 YAML，程序只读其中几个字段）。
最小可用版本长这样：

```yaml
collection: FC                      # 文件头：平台名，随便写，不影响功能
sort-by: 001

game: 1943                         # 【必须】游戏标题，显示在商店里
file: 1943.zip                     # 【必须】ROM 相对路径（单文件）
description: 1943年，是 Capcom 的经典纵版射击游戏。
developer: Capcom

game: 超级马里奥兄弟3
file: 超级马里奥兄弟3.zip
```

**字段说明：**

| 字段 | 必须 | 说明 |
| --- | :---: | --- |
| `game:` | ✅ | 游戏标题。商店里显示的名字就是它 |
| `file:` | ✅¹ | ROM 的相对路径。**单文件游戏用它** |
| `files:` | ✅¹ | 多碟游戏用这个代替 `file:`，下面每行缩进写一个碟 |
| `description:` | | 简介，显示在右栏。省略就没有简介 |
| `assets.box_front:` | | 封面相对路径。**可以省略** —— 程序会自动按 `media/<ROM名>/` 推导 |
| `developer:` `genre:` `players:` `release:` | | 补充信息，程序目前不显示，但留着不亏 |
| `collection:` `sort-by:` `launch:` `extensions:` | | 文件头块，程序会跳过；保留是 Pegasus 的标准写法 |

> ¹ 每个游戏必须有 `file:` 或 `files:` 其中之一，否则这个游戏会被跳过。

**多碟游戏**用 `files:`（缩进表示继续行）：

```yaml
game: 宇宙冒险家
files:
  Daedalus Encounter/Daedalus Encounter (Disc 1).chd
  Daedalus Encounter/Daedalus Encounter (Disc 2).chd
  Daedalus Encounter/Daedalus Encounter (Disc 3).chd
description: 1990 年的太空飞行模拟。
```

装这类游戏时会自动生成 `.m3u` 播放列表，ES 能正确读多碟。

#### 素材目录（可选，但强烈建议）

封面和预览视频都放在平台目录下的 `media/` 里，**子目录名 = ROM 文件名去掉扩展名**：

```
FC/1943.zip                ← ROM
FC/media/1943/boxFront.jpg ← 封面
FC/media/1943/video.mp4    ← 预览视频
```

这条对应关系让程序能**直接推导**素材路径，不需要遍历几万个子目录（实测遍历要 24.8 秒，推导是 0 秒）。

- **素材目录名**：默认认 `media/`，也兼容 `img` / `images` / `image` / `covers` / `boxart`
- **封面文件名**：认 `boxfront` / `box_front` / `box` / `cover` / `front` / `folder` 等，
  扩展名认 `.png` `.jpg` `.jpeg` `.webp`，**全部大小写不敏感**
  （实测同一目录里 `boxFront.jpg` 和 `boxfront.jpg` 并存，硬拼路径会静默读不到）
- **兜底**：上表全都没有时，会取该目录下第一张「像封面」的图片
- **没有素材不算错误**：游戏照样能列出、能安装，只是没封面没预览

> 程序找素材时用的是 **ROM 真实所在的那个平台目录**，不是清单里写的那个。
> 同一份清单被拆到两个目录时（`SS` / `SS plus`、`PS1` / `PS1 hack` 等都这样）也能找对。

#### 目录名认不出来怎么办

程序内置**三张**映射表（`ArkOS 68 条 / ROCKNIX 72 条 / DarkOS 70 条`），
把 NAS 目录名翻成 EmulationStation 的系统名。下面是 ArkOS 那套的片段：

| NAS 目录 | ES 系统名 | | NAS 目录 | ES 系统名 |
| --- | --- | --- | --- | --- |
| `FC` | `nes` | | `PSP` | `ppsspp` |
| `SFC` | `sfc` | | `MD` | `megadrive` |
| `GBA` | `gba` | | `PS1` | `psx` |
| `DC` | `dreamcast` | | `SS` | `saturn` |
| `3DO` | `3do` | | | |

**注意两边名字往往不一样** —— `FC` 对应的 ES 系统叫 `nes` 而不是 `fc`，
`PSP` 叫 `ppsspp`，`DC` 叫 `dreamcast`。所以**别按 ES 的名字建 NAS 目录**，
按常见短名建就行。

⚠️ **三张表之间不能互相抄** —— 同一个 NAS 目录在三族固件上可能对应不同的
ES 系统名。最典型的是 `PSP`：ArkOS / DarkOS 上 ES 叫 **`ppsspp`**，
ROCKNIX 上却叫 **`psp`**（那台的 `ppsspp` 是另一回事）。`install.sh`
会按探测到的平台只启用对应的那一张。

⚠️ **目录名不在表里，这个平台就整个被跳过** —— 游戏不会被索引，商店里看不到。
如果你换了新的 ROM 包、某个平台突然消失了，先跑校验（它会同时核对
映射表、ES 配置和 NAS 实际目录）：

```bash
python3 -m romstore.cli --validate   # 校验映射表 / ES 配置 / NAS 目录
python3 -m romstore.cli --systems    # 列出识别成功的平台及其游戏数
python3 -m romstore.cli --list FC    # 看某个平台下的游戏
```

对策：把目录名改成表里已有的名字。表里没有的话，在
`src/romstore/mapp.py` 里对应的那张表加一行 —— **三族各一张**
（`MAP_ARKOS` / `MAP_ROCKNIX_RAW` / `MAP_DARKOS_RAW`，`install.sh`
按平台选用），只改你需要的那张即可：

```python
('你的目录名', 'es系统名', '/roms/es系统名', 'ok', '中文说明'),
```

---

## 二、安装

### 1. 把安装包传到掌机

```bash
# 在你的电脑上（ROCKNIX 那台把 ark 换成 root）
scp ark-romstore-0.6.0.tar.gz ark@<掌机IP>:/tmp/
# 或 scp -r 整个 ark-romstore/ 目录
```

### 2. 解包并安装

```bash
ssh ark@<掌机IP>
cd /tmp
tar xzf ark-romstore-0.6.0.tar.gz
cd ark-romstore-0.6.0

# 先干跑一遍，只看环境够不够，不动任何文件
sudo ./install.sh --check

# 正式安装（把 NAS 地址和共享名填上）
sudo ./install.sh --host 10.168.1.100 --share Roms
```

> 缺依赖（ArkOS / DarkOS 上最常见是 `cifs-utils`）时安装器会**自动 `apt-get` 补装**，
> 不必自己上去敲。想让它只报错不动手，加 `--no-auto-apt`。
> `--check` 是干跑，一律不补装。
>
> ROCKNIX 上**没有包管理器**（根是只读 squashfs），所以那一侧不补装任何依赖，
> 只做检查；缺 `rclone` 这类东西会明确告诉你，但不会自己动手。


> 若提示 `Permission denied`（从 Windows 解压、或经 U 盘拷贝时常见，
> 可执行位丢了），改用 `sudo bash install.sh ...` 即可，不用先 `chmod`。

NAS 需要账号时：

```bash
sudo ./install.sh --host 10.168.1.100 --share Roms \
                  --smbuser myuser --smbpass mypassword
```

不想用命令行也行：**不带 `--host` 直接装**，然后用内置默认装起来，
再到掌机上按 `Start` →「修改商店源」改对（见第四节）。

安装器会做这些事，每一步都会打印结果：

0. **探测平台**（ArkOS / ROCKNIX / DarkOS），后面每一步的路径都按它分支，
   结论写进 `<程序目录>/platform.json`
1. 定位目标用户（ArkOS / DarkOS 找 `ark`，ROCKNIX 用 `root`）；
   **没给 `--es-dir` 时自动从 `es_systems.cfg` 探测入口目录**
   （ArkOS 是 options 的 `/opt/system`，ROCKNIX 是 ports 的
   `/storage/roms/ports`，DarkOS 是 ports 的 `/roms/ports`）
2. 检查依赖
   - ArkOS：python3 / mount.cifs / flock / nc / SDL2 / SDL2_ttf / 中文字体 /
     **内核 cifs 支持** —— 可 `apt-get` 补装的会自己装上
   - DarkOS：同上，另加 `fsck.exfat`（`exfatprogs`，`/roms` 自愈要用）
   - ROCKNIX：python3 / `rclone` / `fusermount` / SDL2 / SDL2_ttf —— 只报告不补装
   - 三侧都要：确认程序目录所在分区**可写**（ROCKNIX 上不是每个目录都能写）
3. 备份会被覆盖的文件到 `<程序目录>/backup/<时间戳>/`（全新安装会说"无旧文件"）
4. 装程序到 `<程序目录>/`，入口脚本到 ES 的入口目录
5. 生成挂载脚本（ArkOS / DarkOS 在 `/usr/local/sbin/`；ROCKNIX 放在程序目录里）
6. 写 `platform.json` 与 `source.json`（密码不进命令行，走环境变量）
7. 装挂载单元：`nas-roms.timer`（**开机 20 秒后**触发 `nas-roms.service`）
   ＋ ArkOS / DarkOS 侧另装 NetworkManager dispatcher 与 `/roms` 只读自愈
8. 挂载 NAS、建索引（首次约 10–15 秒，实测 DarkOS 上 78 个目录 75 秒）
9. 注册 EmulationStation 入口
10. **安装后自检**：程序本体 / 入口脚本 / gamelist 条目 / `source.json` /
    挂载 / 索引 / 开机自启 + 一次无头探针（SDL / 手柄 / 中文字体），
    最后给出「全部通过」或「N 项未通过」

> **为什么 NAS 挂载要由 timer 触发，而不是开机直接跑？**
> `nas-roms.service` 是 `Type=oneshot`。只要它被 enable 到
> `multi-user.target.wants/`，systemd 就会**自动**给它加一条
> `Before=multi-user.target` —— 于是**整机开机必须等这个挂载脚本跑完**。
> NAS 不在线时 rclone 要重试到超时，实测把开机拖慢 **2 分半**
> （`multi-user.target @2min38s`，其中 153 秒就是它）。
> 改由 timer 在开机后 **20 秒 / 90 秒 / 5 分钟**各触发一次
> （ROCKNIX 侧 WiFi 实测开机 9 秒就关联上了），`Before=multi-user.target`
> 随之消失，**开机零等待**，网络就绪后再补挂。
> ⚠️ 所以**不要再 `systemctl enable nas-roms.service`** —— 那会把软链装回
> `multi-user.target.wants/`，开机重新被拖慢。启用 / 停用统一走
> `systemctl enable/disable nas-roms.timer`。

> `--check` 是干跑：只做第 2 步的检查，不写任何文件、也不补装依赖。

### 3. 升级

同一个安装包再跑一次即可，**幂等**：

```bash
sudo ./install.sh --host 10.168.1.100 --share Roms
```

源码被覆盖，`source.json`、索引、已装的游戏都保留。

---

## 三、使用

掌机上（菜单名随平台不同，都是同一个入口）：

- **ArkOS**：EmulationStation → **Options** → 「ROM 商店」
- **AURKNIX / ROCKNIX**：EmulationStation → **Ports** → 「ROM 商店」
- **DarkOS**：EmulationStation → **Ports** → 「ROM 商店」

> 看不到就重启一次 EmulationStation。三族的方式不同：
>
> ```bash
> # ROCKNIX：杀掉 ES，essway.service 会自动把它拉起来并重读 gamelist
> # ⚠️ 必须写 emulationstatio（15 个字母）—— pkill 比的是 comm 名
> pkill emulationstatio
> # ArkOS / DarkOS：ES 由 emulationstation.service 管
> sudo systemctl restart emulationstation.service
> ```

| 动作 | 按键 |
| --- | --- |
| 左右移动（**只在本行内**，到行首/行尾停住） | 十字键左右 / 左摇杆左右 |
| 上下移动（**网格只有它会引起纵向滚动**） | 十字键上下 / 左摇杆上下 |
| **安装** | **`A`** |
| **全屏播放预览视频** | **`X`** |
| 卸载 | `Y` |
| **滚动右栏简介** | **右摇杆上下** |
| 返回 | `B` |
| 上下翻页（一整页 16 款） | `L1` / `R1` |
| **功能菜单** | **`Start`**（`Select` 等效） |

**游戏页是一屏看完的**（0.5.0 起）：左边是封面卡片网格，右栏是选中游戏的
标题、安装状态、**视频预览区**和简介。**没有单独的详情页** —— 移动光标，
右栏跟着变；要装或要播，按 `A` 或 `X` 就行，不用先进详情页。

**网格的滚动方式**（0.6.0 按反馈改过）：一屏 4 列 × 4 行，**只有上下键会
引起滚动**，而且只在光标要跑出可见区时才滚，一次滚一行 —— 光标在第 1~3 行
时画面完全不动，从第 4 行再往下才开始滚。左右键**只在本行内移动**，
不会跨到下一行，所以按右键永远只表示"往右"。
（4 列全都在屏上，不存在按不到"看不见的列"，不跨行不会漏游戏。）

卡片上已安装的游戏右上角有一个**绿点**，扫一眼就知道哪些装过了。
选中项用**亮边框**框出（不铺色块），封面始终完整可见。

右栏从上到下依次是**标题 → 安装状态 → 视频预览区 → 简介正文**。
简介比较长时用**右摇杆上下滚动**，右下角有 `3/12` 这样的行号和 ▲▼ 箭头
提示还剩多少。左摇管移动、右摇管滚动，两者互不干扰。

**按 `X` 全屏播放预览视频**。NAS 上每个游戏的素材目录
`<系统>/media/<ROM 名>/video.mp4` 就是它的过场演示，商店会直接串流播放，
**不下载、不占本机空间**。播放时商店会先退出界面让出画面，由 `mpv` 全屏播放
（ArkOS / DarkOS 走 `--vo=drm`，ROCKNIX 走 `--vo=gpu`/Wayland）；
按 `B` 或播放结束会**自动回到刚才那张卡片**。

> 封面和简介是**后台慢慢加载**的（一屏 16 张封面要从 NAS 现读），
> 刚进系统时卡片可能还是空的，等一两秒就填满，期间界面不会卡。
>
> 底栏会标明当前游戏有没有片源：有片源显示「X 全屏预览」，没有则显示「X 无预览」。

**Start 菜单**里有三项：

- **重扫 NAS** —— NAS 上加了新游戏后，用它刷新索引
- **修改商店源** —— 换 NAS / 换共享 / 换账号（见下节）
- **退出商店**

> 退出与重扫都收在 Start 菜单里，而不是绑在某个键上直接触发 ——
> 一是 `Start` 在 ArkOS 里本来就是菜单键，二是这样顺带避免了误触退出。

安装时会显示进度（检查空间 → 复制文件 → 生成播放列表 → 写入游戏列表），
装完自动出现在 ES 的游戏列表里，不需要手动刷新。

**复制阶段会显示实时速度**（0.6.0 起）：

```
处理中
复制文件
刑事快打2 加强版.chd
[████████████░░░░░░░░░░░░]        107.00 MB / 198.36 MB
速度 35.57 MB/s     已用 3 秒          剩余 3 秒
▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▂▃▄▅▆▇█
```

- **速度**是最近 3 秒的滑动平均；下面那排柱子是最近 60 次采样的曲线，
  一眼看得出传输是稳、是在忽快忽慢、还是已经停了
- **剩余**是按当前速度估的，网速变了它会跟着变
- 传输卡住时显示「**— 等待数据…**」而不是 0 —— 0 读起来像"坏了"，
  这里要表达的是"还在等着"（NAS 忙、源文件在转、或者就快写完了）

**空间不够会拒绝安装**并告诉你要多少；**卸载会进回收站**
（`/roms/.romstore-trash/<时间戳>/`），不是直接删。

---

## 四、换商店源（不用电脑）

`Start` → **修改商店源** → 四个字段：主机 / 共享名 / 账号 / 密码。

- `A` 编辑当前字段（调出手柄软键盘）
- `X` **扫描局域网**，列出开着 445 端口的主机，直接选一个当主机
- `B` 返回
- 选到「保存并连接」按 `A` —— 会立刻重挂载并重建索引

**软键盘**（掌机没有物理键盘，这是唯一输入方式）：

| 动作 | 按键 |
| --- | --- |
| 选字符 | 十字键 |
| 输入 | `A` |
| 退格 | `Y` |
| 完成 | `X` |
| 切页（数字 / 小写 / 大写 / 符号） | `L1` / `R1` |
| 取消 | `B` |

> 软键盘**故意不提供逗号** —— cifs 的挂载选项是逗号分隔的，
> 账号或密码里带逗号会让整条 `-o` 串错位。程序另外还会再剔除一次。

改完会写到 `~/.romstore/source.json`（权限 600）。
**开机挂载脚本读的是同一个文件**，所以「商店里改的源」和
「开机挂的源」永远一致。

万一这个文件丢了或写坏了，挂载脚本会回落到内置默认值
（`10.168.1.100 / Roms / 匿名`）—— **宁可挂错也不能开机挂不上**，
装错了可以在商店里改回来。

---

## 五、命令行工具（排障用）

```bash
cd ~/.romstore

# 环境自检：视频驱动 / 屏幕 / 手柄 / 字体 / NAS / 索引
python3 run.py --probe

# 映射表与 ES 配置是否对得上
python3 -m romstore.cli --validate

# 索引
python3 -m romstore.cli --stats
python3 -m romstore.cli --systems
python3 -m romstore.cli --list FC --page 1
python3 -m romstore.cli --search 马里奥
python3 -m romstore.cli --detail <game_id>
python3 -m romstore.cli --build          # 增量重建
python3 -m romstore.cli --build --force  # 全量重建

# 商店源
python3 -m romstore.cli --source-show
python3 -m romstore.cli --source-set 10.168.1.100,Roms
python3 -m romstore.cli --scan-hosts            # 扫本机网段的 445
python3 -m romstore.cli --mount --force         # 按当前源强制重挂
python3 -m romstore.cli --umount

# 安装 / 卸载（无界面）
python3 -m romstore.cli --install <game_id> --dry-run
python3 -m romstore.cli --install <game_id>          # 连封面一起导入
python3 -m romstore.cli --install <game_id> --no-media   # 只装 ROM，跳过封面
python3 -m romstore.cli --remove <game_id>

# ES 入口
python3 -m romstore.cli --register
python3 -m romstore.cli --unregister
```

---

## 六、文件都装到哪了

下表 `<程序目录>` = `~/.romstore`（ArkOS / DarkOS 上是 `/home/ark/.romstore`，
ROCKNIX 上是 `/storage/.romstore`）。表里写「ArkOS / DarkOS」那几项在这两台
机器上几乎逐项相同；ROCKNIX 上不存在的项，是因为那台的 ROM 分区由固件 udev
挂载，没有对应的 systemd 单元可以自愈。

| 路径 | 是什么 | 平台 |
| --- | --- | --- |
| `/opt/system/RomStore.sh` | ES 入口（rootfs，换 SD 卡不影响） | ArkOS |
| `/storage/roms/ports/RomStore.sh` | ES 入口（Ports 菜单） | ROCKNIX |
| `/roms/ports/RomStore.sh` | ES 入口（Ports 菜单） | DarkOS |
| `/usr/local/sbin/nas-roms-mount.sh` | 幂等挂载脚本（原文件备份为 `.orig-bak`） | ArkOS / DarkOS |
| `<程序目录>/nas-roms-mount.sh` | 幂等挂载脚本 | ROCKNIX |
| `/etc/systemd/system/nas-roms.service` | 挂载单元（由 timer 触发，**不挂在 multi-user.target**） | ArkOS / DarkOS |
| `/etc/systemd/system/nas-roms.timer` | 开机后 20s/90s/5min 触发挂载 | ArkOS / DarkOS |
| `/storage/.config/system.d/nas-roms.service` | 挂载单元（同上） | ROCKNIX |
| `/storage/.config/system.d/nas-roms.timer` | 开机后触发挂载（软链进 `timers.target.wants/`） | ROCKNIX |
| `/etc/NetworkManager/dispatcher.d/50-nas-roms` | 网络重连时补挂 | ArkOS / DarkOS |
| `/opt/system/roms-fs-selfheal.sh` 等 | `/roms` 只读自愈 | ArkOS / DarkOS |
| `<程序目录>/run.py` `<程序目录>/romstore/` | 程序本体 | 三族 |
| `<程序目录>/platform.json` | 平台档案（安装期写，运行期一切路径的来源） | 三族 |
| `<程序目录>/source.json` | 商店源（600） | 三族 |
| `<程序目录>/index.db` | 索引（实测 8,210 款游戏约 1.76 MB） | 三族 |
| `<程序目录>/backup/<时间戳>/` | 安装时被覆盖文件的备份 | 三族 |
| `<程序目录>/log/run.log` | 运行日志 | 三族 |
| `<程序目录>/bin/fusermount3` | `fusermount` 名字桥接（rclone 要用） | ROCKNIX |
| `/mnt/Roms` | NAS 挂载点 | ArkOS / DarkOS |
| `/storage/nas-roms` | NAS 挂载点 | ROCKNIX |
| `<ROM 根>/.romstore-trash/` | 卸载回收站（`/roms` 或 `/storage/roms`） | 三族 |

---

## 七、排障

### ES 里看不到「ROM 商店」

**先确认自已在哪台机器上**，三族的路径不同：

```bash
cat ~/.romstore/platform.json 2>/dev/null | head -3
```

然后按平台查：

```bash
# ---- ArkOS ----
md5sum /opt/system/RomStore.sh                       # 应该存在
grep RomStore /opt/system/gamelist.xml
grep -A2 '<name>options</name>' /etc/emulationstation/es_systems.cfg

# ---- AURKNIX / ROCKNIX ----
ls -l /storage/roms/ports/RomStore.sh                # 应该存在且可执行
grep RomStore /storage/roms/ports/gamelist.xml
grep -A4 '<name>ports</name>' \
  /storage/.config/emulationstation/es_systems.cfg   # <path> 应是 /storage/roms/ports

# ---- DarkOS ----
ls -l /roms/ports/RomStore.sh                        # 应该存在且可执行
grep RomStore /roms/ports/gamelist.xml
grep -A4 '<name>ports</name>' \
  /etc/emulationstation/es_systems.cfg               # <path> 应是 /roms/ports
grep -c '<name>options</name>' /etc/emulationstation/es_systems.cfg  # 应为 0（这台没有 options）
```

`grep` 到条目但菜单里没有 → ES 还没重读 gamelist。手动催一次：

```bash
# ROCKNIX：杀掉 ES，essway.service 的 Restart=always 会拉起一个新的
# ⚠️ 必须写 emulationstatio（15 个字母）—— pkill 比的是 comm 名，写全名匹配不到
pkill emulationstatio

# ArkOS / DarkOS：ES 由 emulationstation.service 管，重启它会重读 gamelist
sudo systemctl restart emulationstation.service
```

### 界面能开但列表是空的

NAS 没挂上，或索引没建：

```bash
python3 -m romstore.cli --source-show
python3 -m romstore.cli --mount --force
python3 -m romstore.cli --build
```

手工核对一下挂载（挂载点随平台不同）：

```bash
# ArkOS / DarkOS
mount | grep /mnt/Roms ; ls /mnt/Roms | head
# ROCKNIX（fstype 应该是 fuse.rclone）
mount | grep nas-roms ; ls /storage/nas-roms | head
cat /storage/.romstore/.mount-state      # 挂载脚本写的状态文件
```

### 挂载失败

`--source-show` 会直接告诉你原因，例如
`mount error(2): No such file or directory` 就是共享名不对，
`mount error(13): Permission denied` 是账号密码问题。

排查顺序：

```bash
# 1. 主机通不通、445 开没开
python3 -m romstore.cli --probe-host 10.168.1.100
# 2. 共享名对不对（本机那台 NAS 实测不区分大小写，但换了机器先核对拼写）
python3 -m romstore.cli --source-show
# 3. 手动看内核报错
sudo /usr/local/sbin/nas-roms-mount.sh --force
```

已知的兼容性硬约束（改脚本时别动）：**内核 cifs 只认 `vers=2.1`**，
`vers=2.0` 会返回 `EOPNOTSUPP`。

ROCKNIX 侧完全不走 cifs（这台内核里没有），排查看 rclone 的日志：

```bash
ls -l /storage/.romstore/bin/fusermount3     # 名字桥接在不在
tail -20 /storage/.romstore/log/rclone.log
# 手动重挂（脚本自带 --status，不用真挂）
/storage/.romstore/nas-roms-mount.sh --status
/storage/.romstore/nas-roms-mount.sh --force
```

三条最常见的报错：

| 报错 | 原因 | 处置 |
|---|---|---|
| `unknown filesystem type 'cifs'` | 这台内核就是没有 cifs | 别折腾了，用默认的 rclone 后端 |
| `"fusermount3": executable file not found in $PATH` | 桥接软链没了 | 重跑 `install.sh`，或手动 `ln -s $(command -v fusermount) /storage/.romstore/bin/fusermount3` |
| `Failed to create file system ... connection refused` | 共享名/账号密码/445 端口 | 看 `.mount-state` 里的 HOST/SHARE 对不对 |
| `dial tcp 10.168.1.100:445: connect: no route to host` | 那一刻 NAS 不在线（≠ 配置错） | 等 90s / 5min 那两个定时点自己补上，或手动 restart |

#### 服务是 `failed`、开机后没自动挂上

`nas-roms.service` 是 `Type=oneshot`，**失败后不会自己重试**。不过现在它由
`nas-roms.timer` 在开机后 20 秒 / 90 秒 / 5 分钟各触发一次，所以「开机时 NAS
恰好还没就绪」这个最常见的场景已经能自愈（实测 WiFi 约在开机后 9 秒关联）。

```bash
systemctl status nas-roms.timer --no-pager      # 定时器在不在、下次何时触发
systemctl list-timers --no-pager | grep nas
systemctl restart --no-block nas-roms.service   # 手动救急
```

⚠️ 手动 `restart` 时 rclone 要重试到超时，**会阻塞约 2.5 分钟**，
所以上面用的是 `--no-block`（不占着终端）。

⚠️ **别用 `systemctl enable nas-roms.service` 去"修"它** —— 那会把软链装回
`multi-user.target.wants/`，开机重新变成「必须等挂载脚本跑完」，实测拖慢 2 分半。
启用 / 停用一律走 `systemctl enable/disable nas-roms.timer`。

### 界面里中文是空白方块

缺中文字体。三族的来源不同：

```bash
# ArkOS / DarkOS：系统自带 NotoCJK，缺了装一下
sudo apt-get install fonts-noto-cjk

# ROCKNIX：这台**一个系统级 CJK 字体都没有**，也不该往只读的 / 上装。
# 安装器会把 scummvm 主题里的那份拷到程序目录，检查一下：
ls -l /storage/.romstore/fonts/
python3 /storage/.romstore/run.py --probe | grep -i 字体
```

三族都不要试图改控制台字体 —— `consolefonts` 里的字体**最大只有 14.6 KB，
没有 CJK 字形**，装字体才是对的。

### 手柄没反应

```bash
python3 run.py --probe        # 看「手柄数量」是不是 0
```

`SDL_GAMECONTROLLERCONFIG_FILE` 由 `RomStore.sh` 导出，各平台指向各自的固件文件：

- **ArkOS / DarkOS**：`/opt/inttools/gamecontrollerdb.txt`（固件自带，两台同一个路径）
- **ROCKNIX**：`/storage/.config/SDL-GameControllerDB/gamecontrollerdb.txt`

文件不在就说明这不是标准固件。

### ES 反复重启 / 屏幕一直闪（仅 ArkOS）

这是 EmulationStation 自己崩了，被 `Restart=on-failure` +
`RestartSec=100ms` 放大成死循环，与本商店无关但很容易同时踩到。
装上崩溃上限保护即可：

```bash
sudo ./install.sh --with-es-guard
# 或手动
sudo install -D -m 644 extras/10-crash-limit.conf \
    /etc/systemd/system/emulationstation.service.d/10-crash-limit.conf
sudo systemctl daemon-reload
```

效果：120 秒内最多重启 5 次，仍失败就彻底停下并留控制台，
不再无休止空转写 SD 卡。**不改厂商原始的 unit 文件**。

需要确认到底崩在哪时：

```bash
# 打开 ES 日志
sudo sed -i 's|name="LogLevel" value="disabled"|name="LogLevel" value="debug"|' \
    /home/ark/.emulationstation/es_settings.cfg
sudo journalctl -u emulationstation -b --no-pager | tail -40
```

### 界面卡住 / 黑屏

`RomStore.sh` 会在退出时连子进程一起收掉，正常情况下不会留下孤儿进程。
真有残留：

```bash
ps -eo pid,args | grep 'romstor[e]/run.py'
sudo pkill -9 -f 'romstore/run[.]py'
```

> 注意 `pkill -f` 的模式写法 —— 用 `romstore/run[.]py` 而不是
> `romstore/run.py`，否则这条命令自己的命令行里含有该字符串，
> 会把执行命令的 shell 一起杀掉。

### 右栏预览没声音 / /tmp 里堆了一堆 jpg

那是**上一个界面进程被杀后留下的 mpv 孤儿**：它会一直往帧目录写图
（实测 10 分钟 2741 个 / 22MB），并且**占着声卡**，让新开的预览变静音。
正常退出不会留下它（`run.py` 装了信号处理器，收尾会连 mpv 一起收），
启动时也会自动扫一遍 `/proc` 收尸。要手动确认 / 手动收：

```bash
ls /tmp/romstore-prev | wc -l            # 正常情况下是 0~几个
python3 <程序目录>/run.py --sweep        # 收掉遗留的预览进程，打印收了几个
```

> 判定只看命令行里的 `--vo-image-outdir=`，且目录必须是
> `romstore-prev` 或 `store/cache/frames` —— 不会误伤你自己开的 mpv。
> 这台机器上**别用 `pkill -f` 来做这件事**：BusyBox 的 `pkill -f` 匹配不稳
> （同一个模式，匹配 mpv 的参数有效，匹配 `python3 <程序目录>/run.py`
> 却毫无反应，实测），所以扫的是 `/proc/*/cmdline`。

---

## 八、卸载

```bash
cd /tmp/ark-romstore-0.6.0

sudo ./uninstall.sh --check        # 先看会删什么
sudo ./uninstall.sh                # 卸商店 + NAS 挂载服务，保留配置与索引
sudo ./uninstall.sh --keep-mount   # 只卸商店，保留 /mnt/Roms 挂载
sudo ./uninstall.sh --purge        # 连索引/配置/日志/备份一起删
```

卸完会**自动自检**是否有残留（文件 / 挂载 / 菜单条目），逐条列出。

**已下载的游戏文件不受影响** —— 它们在 `/roms/` 下，卸载脚本不碰。

---

## 九、已知边界

- **封面/logo 会自动导入**（0.4.0 起，0.4.1 修正布局）。装完的游戏在 ES 里就有图：
  NAS 上 `media/<游戏名>/boxFront.jpg` 会被复制过来并写进 gamelist 的 `<image>`。
  **掌机上素材有两套布局，程序会先探测再按同款写**（见下方「刮削素材布局」）：
  - 散文件式：`/roms/nes/img/1943.png`
  - 每游戏子目录式：`/roms/gba/media/黄金太阳/boxfront.png`

  **视频（`video.mp4`）会复制到掌机吗？不会** —— 它是**直接串流播 NAS 上的源文件**，
  不占 SD 空间、不占下载时间（SD 写速只有 4.8 MB/s，1.75 GB 的镜像要 6 分钟）。
  游戏页按 `X` 就能预览。
  没有封面的游戏静默跳过，**不算安装失败**。想只装 ROM 可用 `--no-media`。
- **卸载回收站不会自动清理**，需要手动删 `/roms/.romstore-trash/`。
- **账号密码走明文 NTLM**（SMB 2.1 协议使然），家用局域网够用，
  **不要暴露到公网**。
- **列不出「这台主机上有哪些共享」** —— 掌机上没装 `smbclient`
  （装了会拖进一堆依赖），所以扫描只能告诉你哪些主机开着 445 端口，
  共享名需要手输。Pegasus 侧建议固定用 `Roms` 这类好记的名字。
- **挂载点不可配置**（ArkOS / DarkOS `/mnt/Roms`、ROCKNIX `/storage/nas-roms`）——
  `es_systems.cfg` 里各系统的 `<path>` 都指向本机 ROM 根，与挂载点是两回事；
  挂载点变了要连 `platform.py` 与两份 systemd 单元一起改。
- **多碟游戏**（`.m3u` 生成那条分支）已在 DarkOS 真机上跑通一个 2 文件条目：
  自动生成 `.m3u`、gamelist 指向它、装完在 ES 里可见、卸载后字节级还原。
  **但没在 ES 里真正启动过游戏**。（**单文件大游戏已验证**：854 MB 的 PSP ISO
  装完 md5 与 NAS 源逐字节一致。）
- **MAME 分类目录的归宿**：ArkOS 上映射到 `mame2003`（推测）；ROCKNIX 侧映射到
  `mame`；DarkOS 上 `mame2003`(/roms/mame2003) 与 `mame2010`(/roms/mame) 并存。
  三族都需要实机确认。
- **ROCKNIX 上「装完的游戏要退出商店后才出现」是设计如此** —— ES 只在启动时
  读一次 gamelist，而商店是杀掉 ES 让 `essway.service` 拉起新实例来触发重读。
  商店运行期间 ES 仍在后台（Wayland 合成器里被压在商店窗口下面），这是正常的。
- **ROCKNIX 的 `/roms`（=`/storage/roms`）自愈没装** —— 那是 mmcblk1p3，
  由固件 udev 自动挂载，没有 `roms.mount` 单元可重启，自愈脚本那套
  umount/fsck/remount 用不上。真出问题手工来：
  停 `essway` → `umount /storage/roms` → `fsck.exfat -y` → 重新挂载。
  （DarkOS 的 `/roms` 同样是 exFAT 且带 `errors=remount-ro`，**这一台装了自愈**。）
- **ROCKNIX 上的界面帧率偏低**（实测单帧 85~127ms，约 8~12fps）。
  在真机上量过 A/B/C 三段（无视频后端 / 只 blit 静态帧 / mpv 后端），
  **不开视频也有 49~72% CPU** —— 所以主要开销不在视频上，而在
  Sway + ES + rclone 同时跑的环境里；同一时间 ES 仍在后台活着。
  没定位到主因，也没做优化（不影响用，就是翻页不如 ArkOS 跟手）。
- **DarkOS 上没在真机点过 UI 与 `X` 键预览** —— 装 / 封面 / ES 可见 / 卸载
  四条链路都已实测通过，但商店界面的实际渲染和 mpv 全屏预览还等人在掌机上点一次。
- **`uninstall.sh --purge` 的完整卸载→重装往返**仍未在真机跑过（ArkOS 侧同样）。

---

## 十、目录结构

```
ark-romstore/
├── README.md            本文件
├── VERSION              版本号
├── CHANGELOG.md         更新日志
├── RELEASE-NOTES.md     版本说明（装机前先看这份）
├── install.sh           一键安装 / 升级
├── uninstall.sh         卸载
├── make-dist.py         打包成 tar.gz / zip（维护者用）
├── docs/screenshots/    README 里的真机截图（**不进包**，只为 GitHub 服务）
├── src/                 程序本体（原样部署到 ~/.romstore/）
│   ├── RomStore.sh         ES 入口（模板）
│   ├── run.py              入口（--probe / --display-test / --sweep / 无头模式）
│   ├── nas-roms-mount.sh   幂等挂载脚本（模板）
│   ├── systemd/
│   │   ├── roms-fs-selfheal.sh  /roms 只读自愈脚本本体
│   │   ├── roms-fs-watch.sh     只读监控本体
│   │   └── mpv-libgbm-fix.sh    Mali 抢占 libgbm 时给 mpv 备一份 Mesa 真库
│   └── romstore/
│       ├── platform.py     平台档案（ArkOS / ROCKNIX / DarkOS）+ 探测（**路径的唯一来源**）
│       ├── config.py       从 platform 取出的路径常量（含 0.5.x 兼容名）
│       ├── mapp.py         NAS 目录 → ES 系统 映射表（三族各一份，含置信度）
│       ├── meta.py         Pegasus metadata 解析
│       ├── media.py        刮削素材（封面）路径推导与复制
│       ├── index.py        L1 索引 + 指纹增量
│       ├── install.py      空间预检 + 流式复制 + 封面导入 + gamelist 文本级 upsert
│       ├── source.py       商店源读写 / 主机扫描 / 挂载
│       ├── sdl.py          SDL2 的 ctypes 绑定（含封面缩略图缓存）
│       ├── ui.py           界面
│       ├── cover.py        封面/简介/文件大小 后台加载
│       ├── vplayer.py      右栏视频：ffmpeg 管道 / mpv 帧服务（两条后端）
│       └── cli.py          无头命令行
├── systemd/
│   ├── nas-roms.timer           开机后异步触发挂载（**三族都装**，见第二节的说明）
│   ├── nas-roms.service         挂载单元（cifs，ArkOS / DarkOS）
│   ├── nas-roms-rocknix.service 挂载单元（rclone FUSE，ROCKNIX）
│   ├── roms-fs-selfheal.service /roms 只读时开机自动修复（ArkOS / DarkOS）
│   ├── roms-fs-watch.service    /roms 运行期只读监控（ArkOS / DarkOS）
│   └── 50-nas-roms              NetworkManager dispatcher（ArkOS / DarkOS）
├── src/systemd/
│   ├── roms-fs-selfheal.sh   自愈脚本本体
│   ├── roms-fs-watch.sh      只读监控本体
│   └── mpv-libgbm-fix.sh     mpv 的 libgbm 桥接（见「十一、技术要点」）
├── tests/
│   ├── test_gamelist_roundtrip.py  ES 入口注册/撤销的字节级往返（防空行累积）
│   ├── test_media.py      刮削/封面/gamelist 的离线自测（不碰真机）
│   ├── test_nav.py        网格导航与纵向滚动（左右不跨行 / 第 4 行才滚）
│   ├── test_platform.py   平台层、三族映射、模板占位符自测
│   └── test_speed.py      进度页速度计（衰减/退化输入/窗口有界）
├── tools/
│   ├── make-release.py    打 tag + 发 GitHub Release
│   ├── api-push.py        github.com 不通时改用 GitHub REST API 推送
│   └── patch-ports-covers.py  ports 封面补齐（文本级、幂等）
└── extras/
    └── 10-crash-limit.conf ES 崩溃循环上限保护（可选，--with-es-guard，仅 ArkOS）
```

`tools/patch-ports-covers.py` 是 ports 封面补齐的补丁脚本，属于**运维工具**、
不进安装包；`src/` 下的三个 `systemd/*.sh` 才会被部署到掌机。

`src/RomStore.sh` 与 `src/nas-roms-mount.sh` 里带
`@@STORE_DIR@@` / `@@ES_DIR@@` / `@@UID@@` / `@@GID@@` 等占位符，
由 `install.sh` 按实机情况替换；替换不干净安装器会直接报错退出。
所以这两个文件**不能直接拷到机器上跑**。
新增占位符时记得同步 `install.sh` 的 `render()` ——
`tests/test_platform.py` 第 6 节会核对两边的清单是否一致。

### 提交前跑一遍

```bash
python3 tests/test_gamelist_roundtrip.py   # ES 入口注册/撤销的往返
python3 tests/test_media.py       # 刮削 / 封面 / gamelist
python3 tests/test_platform.py    # 平台探测 / 三族映射 / 模板占位符
python3 tests/test_speed.py       # 进度页速度计
python3 tests/test_nav.py         # 网格导航 / 纵向滚动
```

五个都不碰真机、不联网。后两个**不构造 `ui.App`**（那要 SDL 与字体），
而是继承它但不跑 `__init__`，只摆被测逻辑要用的状态 —— 测的就是那几个
方法本身，所以不需要显示环境（本机也没有 libSDL2）。
改过任何进包的文件后必须重跑 `python3 make-dist.py`，旧包的 `SHA256SUMS` 随之作废。
**这一步别省**：本轮就试过只在掌机上热更 `cli.py` 去验一个修复，结果
`install.sh` 一跑又把包里的旧版拷了回去，修复白验 —— 第 2 轮往返立刻复现旧症状。

### 怎么打这个包

```bash
cd ark-romstore
python3 make-dist.py --list      # 先看会打哪些文件、各自的权限位
python3 make-dist.py             # 产出 ../ark-romstore-<版本>.tar.gz 与 .zip
python3 make-dist.py -o /tmp/d   # 指定输出目录
python3 make-dist.py --epoch 1750000000   # 可复现构建（固定所有时间戳）
```

输出目录里还会生成 `SHA256SUMS`（对应 tar.gz 与 zip）。
它**不能**放进包里 —— 那样哈希会自我引用。验证：

```bash
sha256sum -c SHA256SUMS
```

打包脚本只做三件事，但每件都踩过：

1. **统一收进 `ark-romstore-<版本>/` 前缀** —— 否则解包时会散落在当前
   目录（tar bomb）。
2. **按规则写死权限位**（`*.sh` / `make-dist.py` / `systemd/50-nas-roms`
   为 `755`，其余 `644`）—— **不能靠宿主文件系统**：在 Windows 上打出来的
   `install.sh` 是 `0666`，掌机上 `./install.sh` 直接 `Permission denied`。
   zip 侧还额外设了 `create_system=3`，否则解压方不认 `external_attr` 里的权限。
3. **排除 `__pycache__` / `*.pyc`** —— 那是打包机的 Python 版本产物，
   掌机是 Python 3.7，带过去既没用又可能让人误判。

`--epoch` 要同时钉住**两层**时间戳：tar 条目的 mtime，以及 **gzip 容器头部
的 mtime**。后者最容易被漏——`tarfile.open(mode='w:gz')` 内部用
`time.time()` 且不接受 mtime 参数，背靠背跑两次常常落在同一秒，
于是「md5 一致」是巧合而非可复现。脚本里是自己套一层 `GzipFile` 传
`mtime` 解决的。

打之前脚本会自检：必备文件在不在、两个模板里的 `@@STORE_DIR@@`
占位符还在不在（缺了说明模板被误渲染过）。

> 万一手上的包解出来 `install.sh` 没有可执行位（从 Windows 解压再拷进
> U 盘常见），用 `sudo bash install.sh` 一样能装，不必先 `chmod`。

---

## 十一、技术要点（为什么这么写）

供维护者参考，细节见同目录的技术架构文档。

- **`es_systems.cfg` 不是合法 XML** —— options 系统的 `<command>` 里有裸 `&`
  （`2>&1`），直接喂给 ElementTree 会解析出 **0 个系统**。解析前必须先做实体转义。
- **gamelist 用文本级插入，不重新序列化** —— 实机上 `nes/gamelist.xml` 是纯
  CRLF、`psp/gamelist.xml` 是纯 LF，格式并不统一，一旦重新序列化就会破坏
  原有上千条目的字节序列。插入时逐文件探测换行风格，插入点取
  `</gameList>` 之前且不 `rstrip`。安装→卸载一个来回后 md5 能回到原值。
- **不用控制台 UI（dialog）** —— 实机 `consolefonts/` 最大字体仅 14.6 KB，
  无 CJK 字形，中文全变空白；且 fbcon 不支持双宽字形。SDL2 是唯一可行路线。
- **不用 gptokeyb** —— 那是控制台程序的「手柄转键盘」桥。本程序直接读
  SDL 手柄事件，不经过键盘这一层。
- **挂载必须共用同一把锁** —— systemd、NetworkManager dispatcher、
  商店换源三处都会调用挂载脚本，无锁时第二个会
  `mount error(16) Device or resource busy`。统一用 `/run/nas-roms.lock` +
  `flock -w 120` 串行化。
- **`cache=loose,actimeo=60`** —— 属性缓存从默认 1 秒放宽到 60 秒，
  目录遍历类操作实测快约 40%（慢速 WiFi 链路上尤其明显）。
- **开机挂载改由 timer 触发，而不是 `multi-user.target`** —— 这条是踩出来的：
  `Type=oneshot` 的 unit 只要被 enable 到 `multi-user.target.wants/`，
  systemd 就会**自动**给它加 `Before=multi-user.target`，于是整个
  `multi-user.target` 都要等它跑完。同一台机上做的对照实验：
  unit 未 enable（无软链）→ `Before=shutdown.target`；已 enable（有软链）
  → `Before=multi-user.target shutdown.target`。NAS 不在线时 rclone 重试到
  超时，实测 `multi-user.target @2min38s`（其中 153 秒是它）。改成
  `nas-roms.timer` 触发后 `Before=multi-user.target` 消失，**开机零等待**。
  ⚠️ 顺带一个反直觉点：timer **不能用 `OnUnitActiveSec` 做重试** —— 这个
  service 成功后长期停在 `active`（`RemainAfterExit=yes`），不产生新的激活
  时间戳，实测 `list-timers` 的 NEXT 直接变成 `-`；失败则停在 `failed`
  不经过 inactive，`OnUnitInactiveSec` 同样不计时。**重试只能用多个
  `OnBootSec`** 这种绝对时间点。
- **`src/systemd/mpv-libgbm-fix.sh`：Mali 固件把 `libgbm.so*` 三个名字全抢走了**
  —— 那几个名字实际都指向 `libMali.so`，于是 mpv **在加载期**就报
  `undefined symbol: gbm_surface_create_with_modifiers`（所以换 `--vo` 完全无效，
  现象是「按 `X` 没反应」）。**绝不能改系统软链** —— Mali 是 ES / GPU 加速的
  依赖，改了黑屏。正解是解一份 Mesa 真库到 `<程序目录>/lib/mpv/`，
  起 mpv **只给那一条命令**加 `LD_LIBRARY_PATH`；`install.sh` 会自动体检并修。

