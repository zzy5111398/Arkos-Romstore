# 更新日志

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
  并在打包前自检必备文件与模板占位符。
- **修掉两个会让「装了但装不上」的 bug**（都是本地模拟安装时才暴露的）：
  1. 两个模板脚本的**注释**里写有「不应残留任何 `@@` 占位符」，这句
     本身含字面 `@@`，而 `install.sh` 用 `grep -q '@@'` 判断替换结果 ——
     于是**每次安装都会误判「替换未完成」并中止**。注释改用词，并把
     检查收紧为 `@@[A-Z_]\{1,\}@@`，只认真正的占位符。
  2. 包内文件权限位取的是宿主文件系统（Windows 下为 `0666`），
     `install.sh` 落地后**没有可执行位**，`./install.sh` 直接
     `Permission denied`。改为在打包时对每个条目**显式写死 mode**，
     与宿主无关。

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
