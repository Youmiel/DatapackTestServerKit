# 使用说明

## 环境准备（一次性）

```powershell
# 用项目 venv 安装依赖
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

> Python 3.10+。venv 已创建在项目目录（若新建环境：`python -m venv venv`）。

## 配置

```powershell
Copy-Item config.example.yaml config.yaml
```

编辑 `config.yaml`。最小可用示例：

```yaml
java:
  launcher_roots:
    - "C:/Program Files (x86)/Minecraft Launcher/runtime"   # 留空则用 JAVA_HOME/PATH

version_groups:
  - id: "1.21"
    instances: ["1.21.1", "1.21.4"]
    mods: []
    plugins: []
```

各配置块详细说明见 **[docs/configuration.md](configuration.md)**。

## 部署

```powershell
.\venv\Scripts\python.exe mcserver.py list                              # 先看配置
.\venv\Scripts\python.exe mcserver.py deploy                            # 部署全部实例
.\venv\Scripts\python.exe mcserver.py deploy --group 1.21               # 部署某组全部
.\venv\Scripts\python.exe mcserver.py deploy --version 1.21.1           # 部署单个
.\venv\Scripts\python.exe mcserver.py deploy --version 1.21.1 --dry-run # 只解析展示，不下载
.\venv\Scripts\python.exe mcserver.py deploy --version 1.21.1 --force   # 强制重下全部并覆盖
```

deploy 做的事：探测 Java → 下载 fabric server jar → 初始化 MCDR 实例目录 → 生成
`config.yml`/`eula.txt`/`server.properties`（应用端口/RCON/启动命令）→ 下载 mod 到
`mods/` → 下载插件到 `plugins/` → 生成 `start.bat`。

### deploy 默认是"幂等补齐"（断点继续 / 自动补齐）

- 已有且非空的 jar / mod / 插件**自动跳过**，只下载缺失的 —— 部署中途断网/中断后
  重跑同一条命令即可**从断点继续**，不会重复下载。
- 配置里**新增了 mod / 插件**后重跑 `deploy`，会自动把新文件补齐进既有实例
  （旧文件不删除，多余的手动清理即可）。
- 下载失败会按 `network.download_retries`（默认 3）自动重试，仍失败才报错中断，
  此时可修复网络后重跑（断点继续）。

### `deploy --force` 强制重新部署

`--force` 会**无条件重下** jar / mod / 插件并覆盖已有文件（用于强制拉取最新版本、
  loader 等）。注意：只覆盖配置里仍存在的文件，**不会**删除配置中已移除的旧
  mod/插件残留。

## 启动

MCDR 是交互式前台控制台工具，工具**不托管**其进程（后台重定向会破坏控制台交互）。
二选一：

```powershell
# 方式 A：命令前台启动
.\venv\Scripts\python.exe mcserver.py start --version 1.21.1

# 方式 B：直接双击/运行实例目录里的启动脚本
.\servers\1.21.1\start.bat      # Windows
./servers/1.21.1/start.sh       # Linux/macOS
```

### `start --update` —— 同端口更新启动命令（不下载资源）

改了配置里该组的 `server` 块（端口 / RCON / `start_command_template`）或全局
`mcdr.language` 后，想让既有实例**原地更新启动命令**再启动（不换端口、不重下
mod），用：

```powershell
.\venv\Scripts\python.exe mcserver.py start --version 1.21.1 --update
```

`--update` 会在启动前就地刷新：
- MCDR `config.yml` 的 `start_command`（含自定义模板变更）、`language`
- `server.properties` 与 `config.yml` 的**端口 / RCON**（与当前配置保持一致）
- `start.bat` / `start.sh`

它**只同步本地生成文件、不联网下载任何资源**，且保持同端口，适合"改了启动参数
/ 端口后重启实例"的场景。要求实例已部署过（缺 `config.yml` 会提示先 `deploy`）。

MCDR 控制台常用命令：`!!MCDR status`、`!!MCDR server stop_exit`（停服并退出）、`!!MCDR exit`。
也可直接 `Ctrl+C`。

> 首次启动 fabric server 会联网下载 vanilla server + loader 依赖并解压，耗时较长
> （一次性），之后启动很快。若提示接受 EULA，工具已在部署时写好 `eula.txt=true`。

## 测试数据包

1. 启动服务器（见上），首次会自动生成 `server/world/`
2. 把数据包（文件夹或 zip）放进 `servers\<版本>\server\world\datapacks\`
3. 在 MCDR 控制台执行 `/reload` 重新加载数据包
4. 修改数据包后再次 `/reload` 即可迭代测试

## 修改端口 / RCON（部署后冷改）

```powershell
# 按配置文件的 server 块应用到全部实例
.\venv\Scripts\python.exe mcserver.py set-ports

# 单个实例 + 命令行覆盖（不写配置）
.\venv\Scripts\python.exe mcserver.py set-ports --version 1.20.1 --port 25566
.\venv\Scripts\python.exe mcserver.py set-ports --version 1.20.1 --rcon-enable --rcon-port 25576 --rcon-password test123

# 禁用 RCON / 恢复默认
.\venv\Scripts\python.exe mcserver.py set-ports --version 1.20.1 --rcon-disable
```

修改即时落盘（同时更新 `server.properties` 与 MCDR `config.yml` 保持一致），重启服务端后生效。

## 查看状态

```powershell
.\venv\Scripts\python.exe mcserver.py status              # 所有实例
.\venv\Scripts\python.exe mcserver.py status --version 1.21.1
```

`status` 展示是否已部署及启动方式（工具不托管进程，故不显示运行中/停止）。

## 常见问题

**首次启动一直"下载/解压"？**
fabric server jar 首次自举（下载 vanilla server 与依赖），一次性成本，之后很快。

**只想测纯数据包，不要 mod？**
把组的 `mods` 留空即可，仍是 fabric（=vanilla 兼容）服务端。

**多实例端口冲突？**
一次只开一个实例时无需处理；换端口用 `set-ports --version X --port 新端口`。

**RCON 连不上？**
确认 `enable-rcon=true` 且 `rcon.password` 非空。用
`set-ports --rcon-enable --rcon-password xxx` 会自动保持两侧一致。

**报"要求 Java X 但本机只有 Y"？**
该 MC 版本需要 Java X，但本机没有。装对应 JDK / 设 JAVA_HOME，或在
`java.launcher_roots` 填入含官方运行时的 MC 启动器目录。工具不会默默用错版本。

**Modrinth 下载被墙/很慢？**
可在 `network.proxy` 配置代理（如 `http://127.0.0.1:7890`）；工具默认不读系统代理。
