# Phase 10.1 前端架构

## 接管结论

已阅读 COMPETITION_DEMO_DELIVERY.md、API.md、DEMO_FLOW.md、最近 10 条 Git 记录，再读取 prompt/phase10.md。基线为 e2f5584；此前没有 frontend、web 或其他前端工程，只有 FastAPI/Swagger。新增独立 frontend/，后端保留 app/，避免为目录命名迁移稳定业务。

## 技术与部署

Vue 3 + Vite + TypeScript + Axios；采用轻量原生组件与 CSS，避免为少量卡片引入完整 UI 库。定性数据用来源卡片呈现，不引入没有数值依据的 ECharts 雷达图、进度或评分。依赖精确版本及传递依赖由 package-lock.json 固定。

frontend/src/ 按 services、composables、components、styles 划分；类型映射响应 schema_version=2.0。后端应用版本更新为 0.3.0 时不变更业务协议。

Docker Compose 分开运行 Nginx 静态前端和 FastAPI。浏览器访问 http://localhost:8080，前端 /api/ 经同源代理到内部 backend:8000；根 /demo 为演示页面，保留原 /docs、/health、/chat、/demo/profiles、/openapi.json 兼容入口。没有浏览器端密钥，也不加载真实健康档案。继续使用原会话命名卷。

本地开发在 frontend/ 执行 npm ci、npm run dev；Vite 默认 http://localhost:5173，/api 转发本地 8000 后端，API_PROXY_TARGET 可覆盖。部署入口使用 evaluation.demo_start，由 Settings 解析现有 .env，敏感值只进入子进程环境，不进入命令参数或前端构建。

## 状态与交互约束

- 浏览器只从 /demo/profiles 选择合成画像。人数、餐次、忌口全部依赖后端澄清，不自行猜测确认状态。
- 同一会话串行请求；每次新操作生成 request_id，失败重试保留原始 payload 和 request_id。切换画像/新对话清空当前会话；不把聊天内容写入 localStorage。
- 菜单以最新后端响应为准；澄清或不可行结果不能继续显示旧菜为有效推荐。
- 换菜发送明确槽位自然语言，收到成功响应才更新；候选建议仅供查看，现有 API 不支持通过建议 ID 强制选菜。
- 卡片忠实展示食材、步骤、来源、解释；无图片用标注占位，无热量/营养克数显示数据不足。当前只有一餐，营养面板不假装全天摄入。
- /demo 的三案例为手写合成需求，每案新会话；点击实际调用后端，显示真实等待/错误，不播放伪造工具进度。

## 阶段与验收

10.1 架构与工程；10.2 聊天与请求状态；10.3 结构化澄清；10.4 菜品详情；10.5 换菜；10.6 定性营养可视化；10.7 三案例演示；10.8 Compose/一键部署；10.9 浏览器、后端与 Docker 回归及交付。各阶段提交使用 Phase10-x 前缀。

浏览器使用独立合成响应测试页面/网络/状态与异常，另执行合成资料真实模型联调。二者分别报告，不将 Mock 视为模型理解能力证据；不进入 Phase 11 的模型专项评测。

技术依据：[Vue 官方指南](https://vuejs.org/guide/introduction.html)、[Vite 官方指南](https://vite.dev/guide/)、[Playwright 官方文档](https://playwright.dev/docs/intro)。
