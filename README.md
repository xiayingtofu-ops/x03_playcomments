# 游玩反馈管理平台 MVP

这是一个面向游戏项目内部用户的反馈收集与处理平台原型，目标是把零散建议沉淀成策划可判断、可筛选、可跟进的条目。

## MVP 功能

- 反馈提交：内部成员用轻量表单提交标题、描述、类型、模块、标签和负责人。
- Feed 流：所有人可以浏览反馈、点赞、评论，快速补充上下文和热度。
- 策划表格：按负责人和状态查看条目，并直接调整优先级和处理状态。
- 通知机制：支持站内通知，用户可配置点赞、评论、状态变化、分配提醒。
- 本地数据：数据默认保存在 `data/store.json`，便于原型演示和后续替换数据库。

## 运行

```bash
npm start
```

启动后访问：

```text
http://localhost:3000
```

## 核心接口

- `GET /api/bootstrap`：获取用户、反馈、通知和通知偏好。
- `GET /api/feedback`：查询反馈，支持 `ownerId`、`status`、`q`。
- `POST /api/feedback`：创建反馈。
- `POST /api/feedback/:id/like`：点赞或取消点赞。
- `POST /api/feedback/:id/comments`：添加评论。
- `PATCH /api/feedback/:id`：更新状态、优先级、负责人、模块或类型。
- `PATCH /api/users/:id/notification-prefs`：更新通知偏好。
- `PATCH /api/notifications/:id/read`：标记通知已读。

## 后续建议

- 接入公司 SSO 和真实游戏账号。
- 增加附件、截图、版本号、设备信息和复现步骤模板。
- 增加策划处理备注、重复反馈合并、导出需求池。
- 接入企业 IM、邮件或项目管理系统。
