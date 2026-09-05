# Minecraft 多版本测试服务器部署工具

用 Python 编写的跨版本 Minecraft 服务器部署工具，用于**测试数据包（datapack）**。
为不同 MC 版本自动装配 **Fabric 服务端 + Fabric mod（Modrinth/CurseForge）+ MCDR 及插件**，
产出"进目录就能启动"的完整测试环境。

## 特性

- **多版本并行**：按"大版本组"组织，组内共享 mod/插件列表，可展开任意数量小版本（`servers/<版本>/`）
- **自动装 Fabric**：官方 meta API 直接下载可启动的 server jar（含 loader，免 installer）
- **自动匹配 mod**：mod 按大版本配一次，自动选各小版本兼容版本
- **MCDR 集成**：每个实例独立运行目录；插件按显式 URL 下载进 `plugins/`
- **Java 自动推断**：按目标 MC 版本查 Mojang 官方数据，自动推断所需 Java 并在官方运行时精确定位（无需人工映射）
- **数据包就绪**：首次启动自动生成 `world/`，数据包丢进 `server/world/datapacks/` 即可
- 纯 Python（3.10+），仅依赖 `PyYAML` + `requests`

## 目录布局

```
<项目根>/
├── mcserver.py               # CLI 入口
├── config.yaml               # 你的配置（从 config.example.yaml 复制）
├── config.example.yaml       # 带详尽注释的示例
├── server_manager/           # 工具源码
├── docs/                     # 详细文档（配置 / 用法）
└── servers/
    └── <MC 版本>/            # 每个小版本一个自包含实例
        ├── config.yml        # MCDR 配置（自动生成）
        ├── plugins/          # MCDR 插件
        ├── start.bat/start.sh
        └── server/           # 服务器文件
            ├── minecraft_server.jar
            ├── mods/         # fabric mod
            └── world/datapacks/   # ← 你的数据包放这里
```

## 快速开始

```powershell
# 0. 创建虚拟环境（venv）
python.exe -m venv venv

# 1. 装依赖（用项目 venv）
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 生成配置并编辑（详见 config.example.yaml 注释）
Copy-Item config.example.yaml config.yaml

# 3. 部署（示例：1.21 组 + 1.21.1 小版本；不指定则部署全部）
.\venv\Scripts\python.exe mcserver.py deploy --group 1.21 --version 1.21.1

# 4. 前台启动（也可直接双击 servers\<版本>\start.bat）
.\venv\Scripts\python.exe mcserver.py start --version 1.21.1
```

启动后把数据包放进 `servers\<版本>\server\world\datapacks\`，在 MCDR 控制台执行 `/reload` 即可测试。
其余子命令见下表；详细配置与用法见 **[docs/](docs/)**。

## CLI 子命令

| 命令 | 说明 |
| --- | --- |
| `list` | 列出配置中的版本组、实例、mod、插件 |
| `deploy [--group G] [--version V] [--dry-run]` | 部署指定/全部实例（`--dry-run` 只解析不下载） |
| `status [--version V]` | 查看部署状态与启动方式 |
| `start [--group G] [--version V]` | 前台启动 MCDR |
| `set-ports [--group G] [--version V] [--port N] [--rcon-*]` | 改端口/RCON（部署后冷改） |

`--config <path>` 指定配置文件（默认 `./config.yaml`），子命令前后均可。

## 文档

- **[docs/configuration.md](docs/configuration.md)** — 配置详解（Java / server / mod / 插件 / 版本组）
- **[docs/usage.md](docs/usage.md)** — 详细用法（部署 / 启动 / 冷改 / 测试数据包 / FAQ）
