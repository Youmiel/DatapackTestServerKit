# 配置详解

工具默认读取项目根目录的 `config.yaml`（可从 `config.example.yaml` 复制）。
`--config <path>` 可指定其它文件。以下为各配置块说明。

## 顶层结构

```yaml
java:             # Java 运行时探测
network:          # 网络下载
modrinth:         # Modrinth API（匿名可用）
curseforge:       # CurseForge API（需 key）
mcdr:             # MCDR 全局设置
server:           # 端口 / RCON / 启动命令（全局默认，组级可覆盖）
version_groups:   # 服务器版本组（核心）
```

---

## java — Java 运行时探测

```yaml
java:
  launcher_roots: ["C:/Program Files (x86)/Minecraft Launcher/runtime"]
  use_env_fallback: true
```

- `launcher_roots`：MC 官方启动器的 `runtime` 目录，可多个、**可留空**。
  工具只使用官方 `java-runtime-*` 运行时（忽略其它 jdk/jre）。常见位置：
  - Windows：`C:/Program Files (x86)/Minecraft Launcher/runtime` 或 `%APPDATA%/.minecraft/runtime`
  - Linux/macOS：`~/.minecraft/runtime`
- `use_env_fallback`：默认 `true`。为 `true` 时若 launcher_roots 找不到，回退 `JAVA_HOME` / `PATH`。

### 所需 Java 自动推断（无需人工映射表）

部署时工具查询 **Mojang 官方 version manifest**，得到该 MC 版本官方要求的
Java（大版本 + 运行时组件名）。例如：1.20.1→Java17/`java-runtime-gamma`，1.21.1→Java21/`java-runtime-delta`。
然后：
1. 在 `launcher_roots` 里**优先精确定位同名官方运行时**（最准）；
2. 否则按大版本匹配已装 Java（JAVA_HOME / PATH）；
3. 匹配不到**明确报错**列出可用 Java（不默默用错版本）。

结果缓存到 `.cache/`（24h）。若某版本组显式配了 `preferred_major`，则以配置为准（不查 Mojang）。

---

## network — 网络下载 / 失败重试

```yaml
network:
  timeout: 60             # 单次请求/下载超时（秒）
  proxy: ""               # 可选代理；工具默认不读系统代理
  verify_sha1: true       # 是否校验下载文件 SHA1
  download_retries: 3     # 每个请求/下载失败的总体重试次数（含首次）
```

- `timeout`：单次请求/下载超时（秒），默认 `60`。
- `proxy`：可选代理（如 `http://127.0.0.1:7890`）。工具**不读**系统/环境代理，
  只有这里显式填了才走代理。
- `verify_sha1`：是否校验下载文件 SHA1（Modrinth 提供），默认 `true`。
- `download_retries`：每个请求/下载失败后的**总体尝试次数**（含首次），默认 `3`
  （首次失败后最多再重试 2 次，间隔退避）。连接层对 5xx/429 已先自动重试，
  此项兜底**断流 / 超时 / SHA1 校验失败**等整体错误。设为 `1` 即关闭总体重试。

---

## server — 端口 / RCON / 启动命令

```yaml
server:
  port: 25565              # 游戏端口（server-port）
  rcon:
    enable: false          # 是否启用 RCON
    port: 25575            # RCON 端口
    password: ""           # RCON 密码（启用时建议设置；留空默认 "password"）
    # address: 127.0.0.1   # MCDR 连接用，一般不用改
  start_command_template: ""   # 自定义启动命令（可选）
```

### 端口 / RCON

- `deploy` 时自动应用（写 `server.properties` + 同步 MCDR `config.yml` 的 rcon 段）。
- RCON 需**两侧一致**（MC 服务端与 MCDR 客户端），工具自动保持同步。
- 部署后可用 `mcserver set-ports` 冷修改（见 usage），改后重启服务端生效。

### 自定义启动命令（内存 / JVM 参数）

默认启动命令为 `java -Xms1G -Xmx2G ... -jar minecraft_server.jar nogui`（1G 内存）。
自定义请给 `start_command_template`（shell 命令串）：

```yaml
server:
  start_command_template: '"{java}" -Xms2G -Xmx4G -XX:+UseG1GC -Dfoo=bar -jar {jar} nogui'
```

占位符：
- `{java}` → 工具探测到的 java 绝对路径。**路径含空格时请加引号**：`"{java}"`
- `{jar}` → 服务器 jar 文件名（`minecraft_server.jar`）

**不配模板时保持默认**（exec 直启，不经 shell）；配了则按模板（shell 方式）生成。
改动后需重新 `deploy`（或手动改实例 config.yml）生效。

---

## version_groups — 服务器版本组（核心）

```yaml
version_groups:
  - id: "1.21"                        # 组标识
    instances: ["1.21.1", "1.21.4"]   # 要部署的小版本（增删即调数量）
    # java: { preferred_major: 21 }   # 可选：强制 Java 大版本（一般不填，自动推断）
    # server: { ... }                 # 可选：覆盖顶层 server 设置
    mods: []
    plugins: []
```

每个组 = 一个大版本线，组内共享 mod / 插件列表。组下每个小版本生成一个独立服务器
（`servers/<版本>/`，均为自包含 MCDR 根）。

### mod — fabric mod（Modrinth / CurseForge）

```yaml
mods:
  - id: carpet            # Modrinth slug（默认 source）
    source: modrinth
  - id: fabric-api
    source: modrinth
  # - id: some-cf-mod     # CurseForge 项目名
  #   source: curseforge
```

- `source: modrinth`（默认）：**无需 key**，自动选该 MC 版本 + fabric 的最新稳定版。
  可选 `version` 填 version_id 锁定。
- `source: curseforge`：需 API key（不写进配置，用环境变量提供，见下）。

### plugins — MCDR 插件（显式 URL）

```yaml
plugins:
  - id: quick_backup_multi
    url: "https://github.com/TISUnion/QuickBackupM/releases/download/v1.10.1/QuickBackupM-v1.10.1.mcdr"
```

工具下载 URL 到实例 `plugins/`，MCDR 启动时自动加载。插件官方目录
<https://mcdreforged.com/en/plugins> 可查各插件 release 直链。

**插件跨 MC 版本通用，带共享缓存**：插件文件（如 `.mcdr`）不区分 MC 版本，
工具下载时统一先进 `.cache/plugins/` 共享缓存（同一 URL 只网络下载一次），
再复制到各实例的 `plugins/`。多版本实例部署同一插件不再重复下载；
`deploy --force` 会强制重下并刷新该缓存。

---

## modrinth / curseforge

```yaml
modrinth:
  user_agent: "your-name/tool (https://github.com/you)"   # 建议改成你的标识
curseforge:
  api_key_env: "CURSEFORGE_API_KEY"   # 从该环境变量读 key
```

- Modrinth 匿名可用，但官方要求有辨识度的 User-Agent。
- CurseForge 需要 API key，通过环境变量提供，不落盘：
  ```powershell
  $env:CURSEFORGE_API_KEY = "你的 key"
  ```

---

## mcdr

```yaml
mcdr:
  pip_spec: "mcdreforged>=2.0"   # MCDR pip 版本约束
  language: "zh_cn"              # en_us / zh_cn / zh_tw
```

MCDR 通过 pip 装进项目共享 venv（一次即可），每个实例是独立运行目录。
