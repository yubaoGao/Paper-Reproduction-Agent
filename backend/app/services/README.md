# Services

本包承载论文/仓库分析、对齐、规划、复现 intake/API、队列/GPU contracts、外部资源、结果解析/比较与产品事件等应用服务。服务层依赖 domain/ports，不直接操作 Docker、HTTP framework 或已删除的 legacy runtime。
