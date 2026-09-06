# 任务中心验收

在允许本地 HTTP/socket 的测试环境，使用独立、可丢弃的 PostgreSQL 数据库：

```sh
python -m pip install -e '.[dev,browser]'
python -m playwright install chromium firefox
TASKHUB_TEST_POSTGRES_DSN='postgresql://localhost/taskhub_acceptance' \
  scripts/test-task-center-acceptance.sh --basetemp=/tmp/taskhub-acceptance
```

此入口执行全套 pytest，并强制启用浏览器验收。缺少数据库、Playwright 或浏览器时会失败，不会将其跳过后视为验收通过。数据库中会保留测试任务，请勿使用生产数据库。无需修改环境文件或凭据文件。

`test_task_center_postgres.py` 验证三任务持久化、缺失/过期索引修复、幂等回填、A/B 筛选，以及重新连接后的 checkpoint 恢复和无重复执行。`test_postgres_recovery.py` 验证运行时重建后仍可审批并完成工作流。

`test_task_center_browser.py` 使用真实 Chromium/Firefox、HTTP API、认证、LangGraph 和 PostgreSQL；仅以确定性适配器替代外部模型、施工和发布服务，不拦截或伪造浏览器 API 响应。覆盖：

- 三任务默认表格、刷新、项目/生产线/状态/环节组合筛选。
- 桌面及窄屏菜单位于内容左侧，菜单项纵向排列。
- 点击任务显示十环节，隐藏新建表单，在计划审批和发布审批环节旁点击操作。
- 发布服务真实抛错后显示阻塞原因，关闭 Chromium 并重建应用。
- 全新 Firefox 会话重新登录查询历史，在发布环节旁恢复，验证执行记录前缀未变，已完成节点调用次数未增加。
- 保存列表、阻塞及恢复后的截图到 pytest 临时目录。

## 本次受限工作区执行记录

PostgreSQL 16 临时集群初始化成功，但启动报错：`could not bind Unix address ... Operation not permitted`；显式执行两项 PostgreSQL 测试同样在连接 socket 时失败。浏览器测试显式执行时因缺少 `playwright` 失败，安装请求也被网络沙箱拒绝。因此本次没有获得 PostgreSQL 或浏览器端通过证据，不能据此宣称持久化/跨浏览器验收已完成。需要在上述具备依赖和 socket 权限的环境运行验收入口并审阅截图。

常规全套测试在本沙箱默认 asyncio 运行器下挂起；使用已安装的 uvloop 执行后为 **56 passed、3 skipped**：

```sh
python -c 'import uvloop, pytest; uvloop.install(); raise SystemExit(pytest.main(["-q"]))'
```

三项跳过为两项 PostgreSQL 测试及新增浏览器测试，不计入验收通过。另通过架构测试（2 passed）、JavaScript 语法检查、浏览器测试 Python 编译检查、验收脚本 shell 语法检查及 `git diff --check`。
