# Daily Info 文档

这里是 Daily Info 的维护文档。根目录 `README.md` 和 `README_zh-CN.md` 面向首次访问 GitHub 的用户；本目录面向准备部署、配置、开发或扩展项目的人。

## 文档导航

- [架构说明](architecture.md)：服务拓扑、数据流、后台任务和关键边界。
- [配置与部署](configuration.md)：Docker Compose、环境变量、数据存储和安全注意事项。
- [Source Catalog](source-catalog.md)：内置信息源、订阅模型、source definition 格式和扩展规则。
- [开发与测试](development.md)：本地开发、测试命令、验收流程和贡献检查清单。

## 项目定位

Daily Info 是一个 self-hosted research reading desk，用来聚合论文、技术博客、AI lab 更新、科技媒体和 RSSHub-backed feeds。它的核心目标是让个人或小团队维护一组可信信息源，并把新增内容收敛到一个可搜索、可筛选、可观察健康状态的阅读入口。

Daily Info 默认不依赖云服务、AI key、Postgres 或外部队列。最小部署路径是 Docker Compose + SQLite volume；RSSHub、自托管数据库和 AI 摘要 provider 都是可选增强。

## 当前边界

- 单用户/小团队自托管优先，不提供多租户权限系统。
- AI 摘要是可选能力；抓取、阅读、搜索和 source 管理不依赖 AI provider。
- Source catalog 是公开配置，不应保存 secret 值。
- 当前仓库还没有选择开源许可证；添加 LICENSE 前，复用和再分发权利并未被明确授予。
