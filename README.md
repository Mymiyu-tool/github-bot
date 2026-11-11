<p align="center">
  <img src="assets/icon.png" width="128" alt="GitHub Bot">
</p>

<h1 align="center">GitHub Bot · GitHub 机器人</h1>

<p align="center">用 GitHub App 的私钥换一个一小时有效的 installation token，然后以 bot 身份读写仓库。</p>

---

## 它怎么工作

```
私钥 ──RS256──> JWT(9分钟) ──POST /app/installations/{id}/access_tokens──> token(1小时)
                                                                          │
                     issue / PR / contents / actions / releases <──────────┘
```

不用账号密码，不用 PAT，权限锁死在 App 被授权的仓库和 scope 里，token 过期自动失效。

## 配置

一次配好，之后都不用传：

```bash
# 私钥放到私有目录
cp ~/Downloads/yxxbc-bot.private-key.pem ~/.miyu/etc/private/github-bot.pem
chmod 600 ~/.miyu/etc/private/github-bot.pem

# 写配置
cat > ~/.miyu/etc/github-bot.json <<'EOF'
{
  "app_id": "123456",
  "key": "~/.miyu/etc/private/github-bot.pem",
  "owner": "Mymiyu-tool"
}
EOF
```

`app_id` 在 App 的设置页顶部，`installation_id` 可以不填——包会自动去 `/app/installations` 里按 owner 匹配。

## 两套凭证：个人 + 组织同时用

`auth` 参数决定用谁的身份：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 先试 App；如果 App 的安装里一个仓库都没勾，自动回退到 `gh` 登录态 |
| `app` | 强制用 GitHub App 的 installation token（显示 `xxx[bot]`） |
| `gh` | 强制用本机 `gh auth token`（你自己的身份，能访问个人 + 所有组织仓库） |

所以不需要二选一：

- 组织仓库：`auth` 留空即可，App 没授权仓库时会自动走 `gh`
- 想要 bot 身份：把 App 装到组织并勾仓库，之后 `auto` 会自动优先用 App
- 想强制：`{"action": "repos", "auth": "gh"}` 或 `"app"`

App ID 和私钥没配也不影响——`auto` 直接落到 `gh`，只要有 `gh auth login` 过。

## 动作

| action | 作用 | 关键参数 |
|---|---|---|
| `status` | 看 App 身份、安装、token 是否正常 | — |
| `installations` | 列出 App 装到了哪些账号/组织 | — |
| `repos` | 列出这次安装能访问的仓库 | — |
| `repo` | 单个仓库的概况 | `owner` `repo` |
| `branch_list` | 分支列表 | `owner` `repo` |
| `token` | 直接输出 installation token | — |
| `issue_list` | issue 列表 | `state` `limit` |
| `issue_get` | issue 详情 | `number` |
| `issue_create` | 开 issue | `title` `body` |
| `issue_comment` | 评论 issue | `number` `body` |
| `issue_close` | 关闭 issue | `number` |
| `pr_list` / `pr_get` | PR 列表 / 详情 | `state` `number` |
| `pr_comment` | 评论 PR | `number` `body` |
| `release_list` | release 与资产 | — |
| `workflow_dispatch` | 手动触发 Actions | `workflow` `ref` `inputs` |
| `file_get` | 读文件 | `path` `ref` |
| `file_put` | 写文件并提交 | `path` `content` `message` `ref` |
| `issue_update` | 改标题/正文/状态/标签/assignee | `number` `title` `labels` |
| `comment_list` | 读评论 | `number` |
| `comment_delete` | 删评论 | `number`（=comment id） |
| `pr_create` | 开 PR | `head` `base` `title` |
| `pr_review` | 审 PR | `number` `event` `body` |
| `pr_merge` | 合并 PR | `number` `merge_method` |
| `workflow_list` | 列 Actions | — |
| `run_list` | 列最近运行 | — |
| `file_list` | 列目录 | `path` |
| `search` | 搜 issue/PR/仓库/代码 | `query` `scope` |
| `scan` | 扫所有仓库的 open issue（默认覆盖全部安装） | — |
| `api` | 直接打任意 GitHub API | `endpoint` `method` `payload` |

## 例子

```jsonc
// 体检
{ "action": "status" }

// 看看能碰哪些仓库
{ "action": "repos" }

// 给 gray-intel 开个 issue
{ "action": "issue_create", "owner": "Mymiyu-tool", "repo": "gray-intel",
  "title": "下载超时", "body": "镜像站偶尔 502" }

// 读 README
{ "action": "file_get", "owner": "Mymiyu-tool", "repo": "gray-intel", "path": "README.md" }

// 改一行并提交
{ "action": "file_put", "owner": "Mymiyu-tool", "repo": "gray-intel",
  "path": "docs/note.txt", "content": "hello\n", "message": "bot: add note" }

// 触发打包工作流
{ "action": "workflow_dispatch", "owner": "Mymiyu-tool", "repo": "fake-id",
  "workflow": "package.yml", "ref": "main" }
```

## 依赖

`openssl`（macOS 自带，Linux 一般也有）用来做 RS256 签名。其余全是 Python 标准库。

## 安全

- 私钥只在本地，权限 600，**不要提交到任何仓库**
- 这个包不会把私钥写进日志，只会输出 token（token 本身是短期的）
- 想收回权限：App 设置页 → Suspend 或卸载

## 许可

MIT © 2026 yxxbc
