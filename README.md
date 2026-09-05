# Minecraft 多版本测试服务器部署工具

用 Python 编写的跨版本 Minecraft 服务器部署工具，用于**测试数据包（datapack）**。
为不同 MC 版本自动装配 **Fabric 服务端 + Fabric mod（来自 Modrinth/CurseForge）+ MCDR 及插件**，
产出"进目录就能启动"的完整测试环境。

## 特性

- **多版本并行**：按"大版本组"组织，组内共享 mod/插件列表，可展开任意数量的小版本实例（`servers/<版本>/`）
- **自动装 Fabric**：从官方 meta API 下载"已内嵌 loader、可直接启动"的 server jar，无需手动跑 installer
- **自动匹配 mod**：mod 列表按大版本配一次，部署时自动为每个小版本选 Modrinth/CurseForge 上兼容的 fabric 版本
- **MCDR 集成**：pip 安装一次 MCDR，每个实例独立运行目录；插件按配置的显式 URL 下载进 `plugins/`
- **自动探测 Java**：从 MC 官方启动器 runtime（`java-runtime-*`）或 JAVA_HOME/PATH 自动选 Java。版本要求无需人工填——工具按目标 MC 版本查询 Mojang 官方数据自动推断所需 Java，并在官方运行时里精确匹配对应组件
- **数据包就绪**：首次启动自动生成 `world/`，把数据包放进 `server/world/datapacks/` 即可测试
- 纯 Python（3.10+），仅依赖 `PyYAML` + `requests`

## 目录布局

```
<项目根>/
├── mcserver.py               # CLI 入口
├── config.yaml               # 你的配置（从 config.example.yaml 复制）
├── config.example.yaml       # 带详尽注释的示例
├── requirements.txt
├── server_manager/           # 工具源码
│   ├── cli.py  config.py  deploy.py
│   ├── fabric.py  mods.py  java.py  mcdr.py
│   ├── http_util.py  server.py  paths.py
└── servers/
    └── <MC 版本>/            # 每个小版本一个自包含实例 = 一个 MCDR 根
        ├── config.yml        # MCDR 配置（自动生成，exec 模式启动 java）
        ├── plugins/          # MCDR 插件（如 QuickBackupM-v1.10.1.mcdr）
        ├── start.bat / start.sh   # 一键前台启动
        └── server/           # MCDR 的 working_directory（服务器文件）
            ├── minecraft_server.jar   # fabric server jar
            ├── mods/                 # fabric mod（carpet 等）
            ├── eula.txt / server.properties
            └── world/datapacks/      # ← 把你的数据包放这里
```

## 快速开始

### 1. 环境准备（一次性）

```powershell
# 用项目 venv 安装依赖（venv 已存在于项目目录）
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 配置

```powershell
Copy-Item config.example.yaml config.yaml
```

编辑 `config.yaml`（详见文件内注释）：

```yaml
java:
  launcher_roots:
    - "C:/Program Files (x86)/Minecraft Launcher/runtime"   # MC 官方启动器 runtime

version_groups:
  - id: "1.21"                        # 大版本组
    instances: ["1.21.1", "1.21.4"]   # 要部署的小版本（增删即调整数量）
    java: { preferred_major: 21 }
    mods:                             # 组内共享，各小版本自动匹配
      - { id: carpet, source: modrinth }
    plugins:
      - id: quick_backup_multi
        url: "https://github.com/TISUnion/QuickBackupM/releases/download/v1.10.1/QuickBackupM-v1.10.1.mcdr"
```

### 3. 部署 & 启动

```powershell
.\venv\Scripts\python.exe mcserver.py list                                # 查看配置
.\venv\Scripts\python.exe mcserver.py deploy --group 1.21 --version 1.21.1  # 部署单个
.\venv\Scripts\python.exe mcserver.py deploy                              # 部署全部
.\venv\Scripts\python.exe mcserver.py status                              # 查看部署状态
.\venv\Scripts\python.exe mcserver.py start --version 1.21.1              # 前台启动 MCDR
```

也可以**直接双击** `servers\<版本>\start.bat` 启动。

### 4. 测试数据包

1. 首次启动会自动生成 `server/world/`（需联网下载 vanilla server + loader，一次性）
2. 把你的数据包（文件夹或 zip）放进 `servers\<版本>\server\world\datapacks\`
3. 在 MCDR 控制台执行 `/reload` 重新加载数据包
4. 用 `!!MCDR` 系列命令管理（`!!MCDR status`、`!!MCDR plugin` 等）

> 注意：MCDR 是交互式前台控制台工具，建议直接在前台运行。工具**不托管** MCDR
> 进程（后台重定向会破坏其控制台交互），因此用 `start` 子命令或 `start.bat` 前台运行。

### 端口 / RCON 统一设置

可在配置顶层加 `server:` 块统一管理所有实例的游戏端口与 RCON（也可在某版本组下
加同级 `server:` 单独覆盖该组）：

```yaml
server:
  port: 25565              # 游戏端口（server-port）
  rcon:
    enable: false          # 是否启用 RCON
    port: 25575            # RCON 端口
    password: ""           # RCON 密码（启用时建议设置）
```

RCON 需**两侧一致**：MC 服务端 `server.properties` 与 MCDR `config.yml` 的 rcon 段。
工具会同时写入并保持同步。

- **部署时**自动应用（`deploy` 会写 `server.properties` + 同步 MCDR config）
- **部署后冷改**：
  ```powershell
  # 全部实例按配置文件 server 块应用
  .\venv\Scripts\python.exe mcserver.py set-ports
  # 仅某个实例，用命令行覆盖（不写配置）
  .\venv\Scripts\python.exe mcserver.py set-ports --version 1.20.1 --port 25566 --rcon-enable --rcon-port 25576 --rcon-password test123
  # 禁用 RCON / 恢复默认
  .\venv\Scripts\python.exe mcserver.py set-ports --version 1.20.1 --rcon-disable
  ```
  修改即时落盘，重启服务端后生效。

## 配置说明

### Java 探测

`config.yaml -> java.launcher_roots` 填 MC 官方启动器的 `runtime` 目录（可多个，也可留空）。
工具只使用官方 `java-runtime-*` 运行时（忽略其它遗留 jdk/jre）。

**所需 Java 版本自动推断**（无需人工维护映射表）：部署时工具查询 Mojang 官方数据
（version manifest），得到该 MC 版本的官方 Java 要求（大版本 + 运行时组件名，如
1.21.1→Java21/java-runtime-delta，1.20.1→Java17/java-runtime-gamma），然后：
1. 在 `launcher_roots` 里优先精确定位同名官方运行时；
2. 否则按大版本匹配已装的 Java（JAVA_HOME / PATH）；
3. 匹配不到时**明确报错**列出可用 Java（不默默用错版本）。

若在某版本组下显式配了 `preferred_major`，则以配置为准（不查 Mojang）。

> 例：非官启用户只有 Java 11，部署 1.21.1（需 Java 21）时会得到清晰报错与引导，
> 而不是让服务器因版本不符启动失败。

### Mod 下载（Modrinth / CurseForge）

- `source: modrinth`（默认）：无需 key。`id` 填项目 slug（如 `carpet`）。自动选该 MC 版本 + fabric 的最新稳定版。
  可选 `version` 填 version_id 锁定。
- `source: curseforge`：需要 API key。**不写进配置文件**，通过环境变量提供：
  ```powershell
  $env:CURSEFORGE_API_KEY = "你的 key"   # 变量名可用 curseforge.api_key_env 改
  ```
  `id` 填项目名，工具会先搜索得到 modId 再取文件。

### MCDR 插件

采用"显式 URL"方式：每个插件给直链（通常是 GitHub release asset，`.mcdr`/`.py` 文件）。
工具下载到实例的 `plugins/`，MCDR 启动时自动加载。插件官方目录见
<https://mcdreforged.com/en/plugins>（可查各插件 release 页拿直链）。

## CLI 子命令

| 命令 | 说明 |
| --- | --- |
| `list` | 列出配置中的版本组、实例、mod、插件 |
| `deploy [--group G] [--version V] [--dry-run]` | 部署指定/全部实例。`--dry-run` 只解析展示，不下载 |
| `status [--version V]` | 查看各实例部署状态与启动方式 |
| `start [--group G] [--version V]` | 前台透传运行 MCDR（等同进实例目录运行） |
| `set-ports [--group G] [--version V] [--port N] [--rcon-enable/--rcon-disable] [--rcon-port N] [--rcon-password P]` | 改端口/RCON（冷改）。值来自配置 `server` 块，命令行参数优先 |

`--config <path>` 可指定配置文件（默认 `./config.yaml`），放在子命令前或后均可。
`set-ports` 不传 `--group/--version` 时作用于全部实例。

## 常见问题

- **首次启动服务器一直在"下载/解压"**：这是 fabric server jar 首次运行自举（下载 vanilla server
  与依赖），一次性成本，之后启动很快。
- **只想测纯数据包，不要 mod**：把组的 `mods` 留空即可，仍是 fabric（=vanilla 兼容）服务端。
- **多实例端口冲突**：一次只开一个实例时无需处理；若要换端口，用
  `mcserver set-ports --version X --port 新端口`（部署后冷改）。
- **RCON 连不上**：确认 `enable-rcon=true` 且 `rcon.password` 非空，并已在 MCDR 控制台
  `!!MCDR rcon` 相关配置同步——用本工具 `set-ports --rcon-enable --rcon-password xxx`
  会自动保持两侧一致。
