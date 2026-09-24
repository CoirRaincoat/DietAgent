# 比赛 Demo 部署验收报告

题目：ZX-2026-0301 · 方太个性化膳食规划 Agent
验收日期：2026-09-24 · 应用版本：0.2.0 · 结论：PASS

## 验收环境与范围

宿主为 Windows，使用现有 Python 3.13.15 虚拟环境及 Docker Desktop Linux 引擎。镜像基于应用提交 `6ffdda4` 加本阶段 Docker 配置及启动/验收工具构建；最终 Phase 9 提交为 `388f179`。

本次实际构建镜像并启动容器，调用真实 HTTP 与真实 DeepSeek。模型联调只使用手写合成画像 900001 和固定合成消息；真实健康档案与原始对话只做宿主本地验收。

- 镜像标签：`dietagent:demo`
- 镜像 ID：`sha256:1de6da46ded10143e5221e283609b8767455ff1bf9d79f0f0791a21322167c70`
- 验收容器：`dietagent-phase9-final-6ffdda4`
- 临时地址：`http://127.0.0.1:59479`
- HTTP 报告：`artifacts/docker_demo_http.json`（忽略的本地产物，不进入 Git）

## 实际执行

```powershell
docker --context desktop-linux build --builder desktop-linux --progress=plain --tag dietagent:demo .
.\.venv\Scripts\python.exe -m evaluation.docker_run --name dietagent-phase9-final-6ffdda4 --port 0 --context desktop-linux --ephemeral
.\.venv\Scripts\python.exe -m evaluation.demo_http --base-url http://127.0.0.1:59479 --output artifacts/docker_demo_http.json
```

`--port 0` 分配临时端口，上述 59479 是本次实际端口。启动器只绑定本机回环地址；临时验收模式使用可自动移除的容器。默认演示模式使用 `dietagent-demo` 容器和命名卷保存会话。

## 检查结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Docker build | PASS | 完成构建并生成上述镜像 ID |
| Docker run | PASS | 启动后真实 HTTP 可访问 |
| 内置 HEALTHCHECK | PASS | 容器状态 healthy |
| GET /health | PASS | 2000 条菜谱、3 个 synthetic 画像、4 个注册工具、模型已配置 |
| GET /demo/profiles | PASS | 仅 900001、900002、900003 |
| OpenAPI | PASS | 版本 0.2.0 |
| 运行用户 | PASS | 非 root 用户 mealagent |
| 文件与写入 | PASS | runtime 可写；镜像中无 .env、原始档案目录及原始对话目录 |
| 工具与呈现 | PASS | 卡片、食材来源、营养结构、替换位置和解释稳定性检查通过 |
| 清理 | PASS | 测试容器已清理，镜像保留，未推送镜像 |

| 合成对话轮次 | HTTP | 行为 | 请求耗时 |
| --- | --- | --- | --- |
| 信息不足 | 200 | 询问 3 项信息，无工具调用 | 0.682 秒 |
| 补充人数与餐次 | 200 | 仍询问忌口，无工具调用 | 0.559 秒 |
| 补齐要求 | 200 | 返回 3 道菜与 2 项替换建议 | 1.411 秒 |
| 只换第二道 | 200 | 仅指定位置发生变化 | 0.887 秒 |
| 解释菜单 | 200 | 菜单不变，无检索或重新规划 | 1.070 秒 |

完整 HTTP 验收耗时 6.536 秒。后三轮解释来源均为 `deepseek_verified_facts`：模型选择已验证事实，文本由程序生成。以上是单次观测，不代表压力测试或比赛性能指标达标。

## 环境问题及处理

1. Docker Hub 直连曾超时；使用用户既有系统代理，仅向构建进程设置代理环境变量，未修改全局配置。一次镜像重建出现 Docker Hub EOF，有界重试后构建成功。
2. 现有 `.env` 的键名与等号之间有空格，Settings/dotenv 可以解析，Docker `--env-file` 会拒绝。新增 `evaluation.docker_run` 读取 Settings，通过子进程环境传值，Docker 命令参数仅含变量名，不含密钥值；未修改原 `.env`。失败时仅显示通用错误，不回显配置。
3. 启动器的 7 项 Mock 测试覆盖参数、端口、卷及密钥不进入命令参数/输出。真实容器运行补充了 Mock 无法验证的部署行为。

## 复现比赛演示

```powershell
docker build -t dietagent:demo .
.\.venv\Scripts\python.exe -m evaluation.docker_run
.\.venv\Scripts\python.exe -m evaluation.demo_http --base-url http://localhost:8080
```

已有本次镜像时可跳过构建。默认容器启动后可访问 `http://localhost:8080/docs`。需本地有效 DeepSeek 配置；启动器支持现有 dotenv 格式，密钥不进入镜像或版本库。

```powershell
docker stop dietagent-demo
docker rm dietagent-demo
```

以上保留命名卷中的演示会话。本次临时测试容器已经清理，没有留下验收 HTTP 服务。镜像仍可用于重新启动。

当前为单 worker、无鉴权的本机演示服务；尚未执行公网部署、多 worker 一致性或并发压力验收。容器刻意不包含真实档案和原始对话，真实数据验收命令须在宿主项目中执行。

## 后续部署地址调整（2026-09-24）

用户要求默认地址改为 `http://localhost:8080`，已同步现行启动与验证命令。前文保留 Phase 9 当时镜像和临时端口的验收记录；本次重新构建的镜像 ID 为 `sha256:480e4b15608905e42aa25d2ae81adaf467210ae194099478c87e0726e99a8231`。

运行中的 `dietagent-demo` 已替换为新镜像，映射 `127.0.0.1:8080` 到容器 `8000`，继续使用原 `dietagent_demo_runtime` 命名卷。旧容器已移除，卷和会话数据保留，新容器保持运行。

实际检查 `http://localhost:8080/health`、`/docs`、`/demo/profiles` 均 HTTP 200，Docker 健康状态 healthy。7 项已有启动器测试及相关 Ruff 检查通过。本次端口调整没有调用模型。
