#!/usr/bin/env python3
# Description: Act as a GitHub App bot: sign a JWT with the app private key, exchange it for an installation token, then read repos, issues, PRs, releases, files and dispatch workflows.
# Display name: GitHub Bot
# 显示名称：GitHub 机器人
# 描述: 用 GitHub App 私钥换 installation token，做仓库巡检、读写文件、发 issue/PR 评论、触发 workflow
# Timeout: 90
# Parameters:
# {"type":"object","properties":{
#   "action":{"type":"string","enum":["status","installations","repos","token","repo","issue_list","issue_get","issue_create","issue_comment","pr_list","pr_get","pr_comment","release_list","workflow_dispatch","file_get","file_put","branch_list"],"description":"What to do. Default status"},
#   "app_id":{"type":"string","description":"GitHub App ID; falls back to the config file"},
#   "key":{"type":"string","description":"Path to the app private key .pem; falls back to the config file"},
#   "installation_id":{"type":"integer","description":"Installation id; auto-discovered when omitted"},
#   "auth":{"type":"string","enum":["auto","app","gh"],"description":"Credential source: app (GitHub App installation token), gh (gh CLI login), auto (try app, fall back to gh). Default auto"},
#   "owner":{"type":"string","description":"Org or user, e.g. Mymiyu-tool"},
#   "repo":{"type":"string","description":"Repository name"},
#   "number":{"type":"integer","description":"Issue or PR number"},
#   "title":{"type":"string"},
#   "body":{"type":"string"},
#   "path":{"type":"string","description":"File path in the repo"},
#   "ref":{"type":"string","description":"Branch, default main"},
#   "content":{"type":"string","description":"New file content for file_put"},
#   "message":{"type":"string","description":"Commit message for file_put"},
#   "workflow":{"type":"string","description":"Workflow file name, e.g. package.yml"},
#   "inputs":{"type":"object","description":"workflow_dispatch inputs"},
#   "state":{"type":"string","enum":["open","closed","all"],"description":"Issue/PR filter, default open"},
#   "limit":{"type":"integer","description":"List size, default 20"}
# },"required":[]}
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
CONFIG = os.path.expanduser("~/.miyu/etc/github-bot.json")


def args():
    raw = os.environ.get("MIYU_ARGS_JSON")
    if not raw:
        raw = sys.stdin.read() or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {}


def config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(app_id, key_path):
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    signing = ".".join([
        b64url(json.dumps(header, separators=(",", ":")).encode()),
        b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ])
    proc = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", os.path.expanduser(key_path)],
        input=signing.encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("openssl 签名失败: " + proc.stderr.decode()[:200])
    return signing + "." + b64url(proc.stdout)


def request(method, path, token, body=None, accept="application/vnd.github+json"):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", accept)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw[:300]}


def pick(cfg, a, key, default=None):
    value = a.get(key)
    if value in (None, ""):
        value = cfg.get(key, default)
    return value


def installation_token(cfg, a):
    app_id = pick(cfg, a, "app_id")
    key_path = pick(cfg, a, "key")
    if not app_id:
        raise SystemExit("缺少 App ID：在参数里传 app_id，或写进 ~/.miyu/etc/github-bot.json")
    if not key_path:
        raise SystemExit("缺少私钥路径：传 key，或写进 ~/.miyu/etc/github-bot.json")

    jwt = make_jwt(app_id, key_path)
    inst_id = a.get("installation_id") or cfg.get("installation_id")

    if not inst_id:
        status, data = request("GET", "/app/installations", jwt)
        if status != 200:
            raise SystemExit(f"列 installations 失败：HTTP {status} {data.get('message','')}")
        items = data if isinstance(data, list) else data.get("installations", [])
        if not items:
            raise SystemExit("这个 App 还没有安装到任何账号/组织，先去安装")
        owner = (a.get("owner") or cfg.get("owner") or "").lower()
        chosen = next((i for i in items if i.get("account", {}).get("login", "").lower() == owner), items[0])
        inst_id = chosen["id"]

    status, data = request("POST", f"/app/installations/{inst_id}/access_tokens", jwt, body={})
    if status not in (200, 201):
        raise SystemExit(f"换 token 失败：HTTP {status} {data.get('message','')}")
    return data["token"], int(inst_id)


def gh_token():
    import shutil
    exe = shutil.which("gh")
    if not exe:
        for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", os.path.expanduser("~/.local/bin/gh")):
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return None
    for args in ([exe, "auth", "token"], [exe, "auth", "token", "--user", "yxxbc"]):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def resolve_token(cfg, a):
    """Return (token, kind). kind is 'app' or 'gh'."""
    mode = (a.get("auth") or cfg.get("auth") or "auto").lower()

    if mode in ("app", "auto"):
        try:
            tok, _inst = installation_token(cfg, a)
            if mode == "app":
                return tok, "app"
            # auto: an installation with no selected repositories is useless
            s, data = request("GET", "/installation/repositories?per_page=1", tok)
            if s == 200 and data.get("total_count", 0) > 0:
                return tok, "app"
        except (SystemExit, RuntimeError):
            if mode == "app":
                raise

    if mode in ("gh", "auto"):
        tok = os.environ.get("GH_TOKEN") or gh_token()
        if tok:
            return tok, "gh"

    raise SystemExit("没有可用凭证：App 装不上，也没有 gh 登录态")


def repo_path(a, suffix=""):
    owner = a.get("owner")
    repo = a.get("repo")
    if not owner or not repo:
        raise SystemExit("这个动作需要 owner 和 repo")
    return f"/repos/{owner}/{repo}{suffix}"


def main():
    a = args()
    cfg = config()
    action = a.get("action") or "status"

    token, kind = resolve_token(cfg, a)

    if action == "token":
        print(token)
        return

    if action == "status":
        if kind == "app":
            jwt = _jwt_only(cfg, a)
            inst_id = a.get("installation_id") or cfg.get("installation_id")
            s_app, app = request("GET", "/app", jwt)
            if not inst_id:
                s_list, data = request("GET", "/app/installations", jwt)
                items = data if isinstance(data, list) else []
                inst_id = items[0]["id"] if items else None
            s_inst, inst = request("GET", f"/app/installations/{inst_id}", jwt) if inst_id else (0, {})
            print(json.dumps({
                "auth": "app",
                "app": {"id": app.get("id"), "slug": app.get("slug"), "name": app.get("name")} if s_app == 200 else app,
                "installation": inst if s_inst != 200 else {"id": inst.get("id"), "account": inst.get("account", {}).get("login")},
                "token_ok": bool(token),
            }, ensure_ascii=False, indent=1))
        else:
            s, me = request("GET", "/user", token)
            print(json.dumps({"auth": "gh", "user": me.get("login"), "token_ok": s == 200}, ensure_ascii=False, indent=1))
        return

    if action == "installations":
        if kind != "app":
            print(json.dumps({"auth": "gh", "note": "当前用的是 gh 登录态，不涉及 App 安装"}, ensure_ascii=False))
            return
        status, data = request("GET", "/app/installations", _jwt_only(cfg, a))
        items = data if isinstance(data, list) else []
        print(json.dumps([{"id": i["id"], "account": i["account"]["login"], "type": i["account"]["type"]} for i in items], ensure_ascii=False, indent=1))
        return

    if action == "repos":
        if kind == "app":
            status, data = request("GET", "/installation/repositories?per_page=100", token)
            items = data.get("repositories", []) if isinstance(data, dict) else []
        else:
            status, data = request("GET", "/user/repos?per_page=100&sort=updated", token)
            items = data if isinstance(data, list) else []
        print(json.dumps([{"full_name": r["full_name"], "private": r["private"], "default_branch": r["default_branch"]} for r in items], ensure_ascii=False, indent=1))
        return

    if action == "repo":
        status, data = request("GET", repo_path(a), token)
        print(json.dumps({k: data.get(k) for k in ("full_name", "description", "default_branch", "private", "topics", "open_issues_count", "pushed_at")}, ensure_ascii=False, indent=1))
        return

    if action == "branch_list":
        status, data = request("GET", repo_path(a, "/branches"), token)
        print(json.dumps([b["name"] for b in data] if isinstance(data, list) else data, ensure_ascii=False))
        return

    if action in ("issue_list", "pr_list"):
        kind = "issues" if action == "issue_list" else "pulls"
        state = a.get("state") or "open"
        limit = int(a.get("limit") or 20)
        status, data = request("GET", repo_path(a, f"/{kind}?state={state}&per_page={limit}"), token)
        items = data if isinstance(data, list) else []
        print(json.dumps([{"number": i["number"], "title": i["title"], "state": i["state"], "user": i["user"]["login"]} for i in items], ensure_ascii=False, indent=1))
        return

    if action in ("issue_get", "pr_get"):
        kind = "issues" if action == "issue_get" else "pulls"
        status, data = request("GET", repo_path(a, f"/{kind}/{a.get('number')}"), token)
        print(json.dumps({k: data.get(k) for k in ("number", "title", "state", "body", "user", "created_at")}, ensure_ascii=False, indent=1))
        return

    if action == "issue_create":
        if not a.get("title"):
            raise SystemExit("issue_create 需要 title")
        status, data = request("POST", repo_path(a, "/issues"), token, {"title": a["title"], "body": a.get("body", "")})
        print(json.dumps({"status": status, "number": data.get("number"), "url": data.get("html_url")}, ensure_ascii=False))
        return

    if action == "issue_comment":
        status, data = request("POST", repo_path(a, f"/issues/{a.get('number')}/comments"), token, {"body": a.get("body", "")})
        print(json.dumps({"status": status, "url": data.get("html_url")}, ensure_ascii=False))
        return

    if action == "pr_comment":
        status, data = request("POST", repo_path(a, f"/issues/{a.get('number')}/comments"), token, {"body": a.get("body", "")})
        print(json.dumps({"status": status, "url": data.get("html_url")}, ensure_ascii=False))
        return

    if action == "release_list":
        status, data = request("GET", repo_path(a, "/releases?per_page=10"), token)
        items = data if isinstance(data, list) else []
        print(json.dumps([{"tag": r["tag_name"], "name": r["name"], "assets": [x["name"] for x in r.get("assets", [])]} for r in items], ensure_ascii=False, indent=1))
        return

    if action == "workflow_dispatch":
        wf = a.get("workflow")
        if not wf:
            raise SystemExit("workflow_dispatch 需要 workflow 文件名")
        ref = a.get("ref") or "main"
        status, data = request("POST", repo_path(a, f"/actions/workflows/{wf}/dispatches"), token, {"ref": ref, "inputs": a.get("inputs") or {}})
        print(json.dumps({"status": status, "ok": status in (204, 201)}, ensure_ascii=False))
        return

    if action == "file_get":
        p = a.get("path")
        if not p:
            raise SystemExit("file_get 需要 path")
        ref = a.get("ref") or "main"
        status, data = request("GET", repo_path(a, f"/contents/{urllib.parse.quote(p)}?ref={ref}"), token)
        if status == 200 and data.get("content"):
            print(base64.b64decode(data["content"]).decode("utf-8", "ignore"))
        else:
            print(json.dumps(data, ensure_ascii=False)[:400])
        return

    if action == "file_put":
        p = a.get("path")
        if not p or a.get("content") is None:
            raise SystemExit("file_put 需要 path 和 content")
        ref = a.get("ref") or "main"
        s0, cur = request("GET", repo_path(a, f"/contents/{urllib.parse.quote(p)}?ref={ref}"), token)
        body = {
            "message": a.get("message") or f"bot: update {p}",
            "content": base64.b64encode(a["content"].encode()).decode(),
            "branch": ref,
        }
        if s0 == 200 and cur.get("sha"):
            body["sha"] = cur["sha"]
        status, data = request("PUT", repo_path(a, f"/contents/{urllib.parse.quote(p)}"), token, body)
        print(json.dumps({"status": status, "commit": data.get("commit", {}).get("sha"), "url": data.get("content", {}).get("html_url")}, ensure_ascii=False))
        return

    raise SystemExit(f"未知动作：{action}")


def _jwt_only(cfg, a):
    app_id = pick(cfg, a, "app_id")
    key_path = pick(cfg, a, "key")
    return make_jwt(app_id, key_path)


if __name__ == "__main__":
    main()
