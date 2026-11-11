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
| `pr_list` / `pr_get` | PR 列表 / 详情 | `state` `number` |
| `pr_comment` | 评论 PR | `number` `body` |
| `release_list` | release 与资产 | — |
| `workflow_dispatch` | 手动触发 Actions | `workflow` `ref` `inputs` |
| `file_get` | 读文件 | `path` `ref` |
| `file_put` | 写文件并提交 | `path` `content` `message` `ref` |

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
