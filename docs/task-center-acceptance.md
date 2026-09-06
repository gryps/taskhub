# 任务中心验收

在允许本地 HTTP/socket 的测试环境，使用独立、可丢弃的 PostgreSQL 数据库：

```sh
python -m pip install -e '.[dev,browser]'
python -m playwright install chromium firefox
TASKHUB_TEST_POSTGRES_DSN='postgresql://localhost/taskhub_acceptance' \
  scripts/test-task-center-acceptance.sh --basetemp=/tmp/taskhub-acceptance
```

临时 PostgreSQL 集群必须使用 UTF-8 初始化，例如
`initdb --encoding=UTF8 --no-locale`。`SQL_ASCII` 会使 checkpoint 文本标识被
驱动读取为字节串，导致 LangGraph 中断恢复无法匹配。

此入口执行全套 pytest，并强制启用浏览器验收。缺少数据库、Playwright 或浏览器时会失败，不会将其跳过后视为验收通过。数据库中会保留测试任务，请勿使用生产数据库。无需修改环境文件或凭据文件。

`test_task_center_postgres.py` 验证三任务持久化、缺失/过期索引修复、幂等回填、A/B 筛选，以及重新连接后的 checkpoint 恢复和无重复执行。`test_postgres_recovery.py` 验证运行时重建后仍可审批并完成工作流。

`test_task_center_browser.py` 使用真实 Chromium/Firefox、HTTP API、认证、LangGraph 和 PostgreSQL；仅以确定性适配器替代外部模型、施工和发布服务，不拦截或伪造浏览器 API 响应。覆盖：

- 三任务默认表格、刷新、项目/生产线/状态/环节组合筛选。
- 桌面及窄屏菜单位于内容左侧，菜单项纵向排列。
- 点击任务显示十环节，隐藏新建表单，在计划审批和发布审批环节旁点击操作。
- 发布服务真实抛错后显示阻塞原因，关闭 Chromium 并重建应用。
- 全新 Firefox 会话重新登录查询历史，在发布环节旁恢复，验证执行记录前缀未变，已完成节点调用次数未增加。
- 保存列表、阻塞及恢复后的截图到 pytest 临时目录。

## Windows Edge 图形节点

当 PostgreSQL 与临时验收服务运行在控制节点、浏览器运行在独立 Windows 图形节点时：

```sh
python scripts/task-center-acceptance-server.py \
  --dsn 'postgresql://localhost/taskhub_acceptance' \
  --state-dir /tmp/taskhub-edge-state --host 0.0.0.0 --port 8324
```

Windows 图形节点只需 Python Playwright 包和系统 Microsoft Edge，不需要安装
TaskHub 或额外下载 Chromium：

```powershell
python scripts/task-center-edge-acceptance.py `
  --url http://192.168.31.31:8324 `
  --output C:\taskhub-acceptance
```

脚本使用两个全新的 Edge 进程，验证任务创建、筛选、审批、阻塞原因、系统资源、
重新登录、服务器历史查询和恢复完成，并产出四张截图及 `result.json`。PostgreSQL 应用重启
恢复由 `test_task_center_postgres.py` 和 `test_postgres_recovery.py` 独立验证。

## 验收记录

验收结果必须记录实际命令、数据库编码、通过/跳过数量、浏览器名称及证据目录。
浏览器测试被跳过时，不得宣称图形验收完成。

项目可单独配置“验收命令”。这些命令只会调度到具备 `acceptance` 工作负载的节点，
结果显示在任务详情的“验收证据”中。监督阶段因证据不足停在返工上限时，可在操作面板
填写证据类型、来源和结果，提交后直接重新进入审查，不再调用施工模型。

TaskHub 自身的 PostgreSQL 验收可运行：

```bash
python scripts/run-postgres-acceptance.py
```

脚本使用 `TASKHUB_POSTGRES_DSN`，但将验收表隔离在 `taskhub_acceptance` schema，
不会写入生产任务表。

2026-09-06 在控制节点的 UTF-8 PostgreSQL 16 临时库执行完整测试：
**61 passed、1 skipped**；跳过项为本机浏览器测试。随后由 Windows 图形节点
`192.168.31.34` 使用系统 Microsoft Edge 执行独立图形验收并通过，产出任务列表、
系统资源、阻塞处理和恢复完成四张截图及 `result.json`。两个临时服务均已停止。
