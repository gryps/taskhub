# TaskHub V2 产品化编排 Phase 1 技术说明

状态：已实现

## 交付范围

- Requirement 永久保留原始需求、附件摘要、提交者和时间；后续信息只能追加为补充；
- Productization 生成结构化 ProductSpec 草稿，并将关键缺失信息合并成一个产品级决策；
- ProductSpec 支持草稿、评审、批准、废弃、修订版本及字段级差异；
- 批准版本不可修改，新版本引用上一版本和对应 ChangeRequest；
- 同一项目的新版本批准后，旧批准版本自动进入 `superseded`；
- 开发流程产品规格卡同时呈现原始需求、结构化规格、版本、状态、待决策和版本差异；
- 功能开关启用时，LangGraph 运行只能从已批准规格启动，并永久记录规格编号和版本。

## API 边界

- `/api/requirements`：提交和查询原始需求；
- `/api/requirements/{id}/supplements`：追加需求补充；
- `/api/product-specs`：查询项目规格；
- `/api/product-specs/{id}/versions/{version}`：读取和编辑未批准版本；
- `review`、`approve`、`revisions`、`diff` 子资源：规格治理；
- `/api/projects/{project_id}/product-decisions/{id}/resolve`：一次解决合并待决策事项；
- `/api/productization/status`：向前端公开功能开关状态。

规格读取允许具备项目查看权限的用户；需求提交、决策、评审、批准和修订要求项目管理
权限。服务端权限检查是事实来源，前端按钮隐藏不替代授权。

## 兼容和不变量

- `TASKHUB_PRODUCTION_ORCHESTRATION_ENABLED=false` 时不展示产品规格入口，也不改变旧运行
  创建行为；
- 功能开关开启后，客户端提交的自由文本不能替换批准规格来源，运行输入由 Requirement
  在该规格版本创建时固化的需求快照重建；后续补充只进入新的修订版本；
- Requirement 原文、附件和既有补充不能被静默修改或删除；
- 已解决的产品决策、已批准或已废弃的规格不可再次改写；
- Phase 1 不声称 ProductSpec 已编译为 ProjectContract 或真实任务 DAG。

## 验证记录

- 需求保存、合并决策、批准门禁、不可变性、修订、差异、旧版废弃和运行绑定均有 API
  回归测试；
- 真实 Chromium 已验证产品规格卡在 1440、680 和 390 像素视口下的字体层级、列布局和
  页面无横向溢出；
- 静态资源版本为 `styles.css?v=40` 和 `app.js?v=14`。

## 下一阶段边界

Phase 2 从批准的 ProductSpec 建立 `.taskhub/*.yaml` ProjectContract、官方项目档案和可执行
结构门禁。不得在 Phase 2 完成前将当前项目仓库检查描述为完整架构合同验证。
