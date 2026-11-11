#!/usr/bin/env python3
# Description: Act as a GitHub App bot: sign a JWT with the app private key, exchange it for an installation token, then work with repos, issues, PRs, releases, files, workflow runs and search. Falls back to the local gh login when the app has no repositories.
# Display name: GitHub Bot
# 显示名称：GitHub 机器人
# 描述: 用 GitHub App 私钥换 installation token，读写仓库、issue、PR、release、文件、workflow，带批量巡检与搜索
# Timeout: 150
# Parameters:
# {"type":"object","properties":{
#   "action":{"type":"string","enum":["status","installations","repos","token","repo","branch_list","issue_list","issue_get","issue_create","issue_update","issue_comment","issue_close","comment_list","comment_delete","pr_list","pr_get","pr_comment","pr_create","pr_review","pr_merge","release_list","workflow_list","workflow_dispatch","run_list","file_get","file_put","file_list","scan","search","api"],"description":"What to do. Default status"},
#   "auth":{"type":"string","enum":["auto","app","gh"],"description":"Credential source: app (installation token), gh (gh CLI login), auto (app first, fall back to gh). Default auto"},
#   "app_id":{"type":"string","description":"GitHub App ID; falls back to the config file"},
#   "key":{"type":"string","description":"Path to the app private key .pem; falls back to the config file"},
#   "installation_id":{"type":"integer","description":"Installation id; auto-discovered when omitted"},
#   "owner":{"type":"string","description":"Org or user, e.g. linuztx"},
#   "repo":{"type":"string","description":"Repository name"},
#   "number":{"type":"integer","description":"Issue or PR number"},
#   "title":{"type":"string"},
#   "body":{"type":"string"},
#   "labels":{"type":"array","items":{"type":"string"},"description":"Labels to set"},
#   "assignees":{"type":"array","items":{"type":"string"}},
#   "state":{"type":"string","enum":["open","closed","all"],"description":"Issue/PR filter, default open"},
#   "path":{"type":"string","description":"File or directory path in the repo"},
#   "ref":{"type":"string","description":"Branch, default main"},
#   "content":{"type":"string","description":"New file content for file_put"},
#   "message":{"type":"string","description":"Commit message for file_put"},
#   "head":{"type":"string","description":"For pr_create: source branch, e.g. yxxbc:fix/x"},
#   "base":{"type":"string","description":"For pr_create: target branch, default main"},
#   "draft":{"type":"boolean","description":"For pr_create"},
#   "event":{"type":"string","enum":["APPROVE","REQUEST_CHANGES","COMMENT"],"description":"For pr_review"},
#   "merge_method":{"type":"string","enum":["merge","squash","rebase"],"description":"For pr_merge, default squash"},
#   "workflow":{"type":"string","description":"Workflow file name or id"},
#   "inputs":{"type":"object","description":"workflow_dispatch inputs"},
#   "query":{"type":"string","description":"For search: keywords or full query"},
#   "scope":{"type":"string","enum":["issues","repos","code","prs"],"description":"For search, default issues"},
#   "endpoint":{"type":"string","description":"For action=api: path like /repos/O/R/issues or a full URL"},
#   "method":{"type":"string","enum":["GET","POST","PATCH","PUT","DELETE"],"description":"For action=api, default GET"},
#   "payload":{"type":"object","description":"For action=api: JSON body"},
#   "limit":{"type":"integer","description":"List size, default 20"},
#   "all_installations":{"type":"boolean","description":"For scan: cover every app installation, default true"},
#   "format":{"type":"string","enum":["json","md"],"description":"Default json"}
# },"required":[]}
import base64
import concurrent.futures as cf
import json
import os
import shutil
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
        with urllib.request.urlopen(req, timeout=25) as r:
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


def gh_token():
    exe = shutil.which("gh")
    if not exe:
        for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", os.path.expanduser("~/.local/bin/gh")):
            if os.path.exists(p):
                exe = p
                break
    if not exe:
        return None
    for cmd in ([exe, "auth", "token"], [exe, "auth", "token", "--user", "yxxbc"]):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def jwt_for(cfg, a):
    app_id = pick(cfg, a, "app_id")
    key_path = pick(cfg, a, "key")
    if not app_id or not key_path:
        raise SystemExit("缺少 App ID 或私钥路径")
    return make_jwt(app_id, key_path)


def installations(jwt):
    s, d = request("GET", "/app/installations", jwt)
    return d if s == 200 and isinstance(d, list) else []


def installation_token(cfg, a):
    jwt = jwt_for(cfg, a)
    inst_id = a.get("installation_id") or cfg.get("installation_id")
    items = []
    if not inst_id:
        items = installations(jwt)
        if not items:
            raise SystemExit("这个 App 还没有安装到任何账号/组织")
        owner = (a.get("owner") or cfg.get("owner") or "").lower()
        chosen = next((i for i in items if i.get("account", {}).get("login", "").lower() == owner), items[0])
        inst_id = chosen["id"]
    s, d = request("POST", f"/app/installations/{inst_id}/access_tokens", jwt, body={})
    if s not in (200, 201):
        raise SystemExit(f"换 token 失败：HTTP {s} {d.get('message','')}")
    return d["token"], int(inst_id)


def resolve_token(cfg, a):
    mode = (a.get("auth") or cfg.get("auth") or "auto").lower()
    if mode in ("app", "auto"):
        try:
            tok, _ = installation_token(cfg, a)
            if mode == "app":
                return tok, "app"
            s, d = request("GET", "/installation/repositories?per_page=1", tok)
            if s == 200 and d.get("total_count", 0) > 0:
                return tok, "app"
        except (SystemExit, RuntimeError):
            if mode == "app":
                raise
    if mode in ("gh", "auto"):
        tok = os.environ.get("GH_TOKEN") or gh_token()
        if tok:
            return tok, "gh"
    raise SystemExit("没有可用凭证")


def repo_path(a, suffix=""):
    owner, repo = a.get("owner"), a.get("repo")
    if not owner or not repo:
        raise SystemExit("这个动作需要 owner 和 repo")
    return f"/repos/{owner}/{repo}{suffix}"


def slim_issue(i):
    return {
        "repo": i.get("repository", {}).get("full_name") or i.get("repo"),
        "number": i["number"],
        "title": i["title"],
        "state": i["state"],
        "author": (i.get("user") or {}).get("login"),
        "labels": [l["name"] for l in i.get("labels", [])],
        "comments": i.get("comments"),
        "updated": i.get("updated_at"),
        "url": i.get("html_url"),
    }


def scan_installation(tok, inst, limit, timeout=35):
    """Search first (one request), fall back to per-repo reads with threads."""
    account = inst.get("account", {}).get("login", "")
    kind = inst.get("account", {}).get("type", "User")
    qualifier = "org" if kind == "Organization" else "user"
    q = urllib.parse.quote(f"is:issue is:open {qualifier}:{account}")
    s, d = request("GET", f"/search/issues?q={q}&per_page=100", tok)
    hits = []
    if s == 200 and isinstance(d, dict) and d.get("total_count"):
        hits = [i for i in d.get("items", []) if "pull_request" not in i]
        for i in hits:
            i["repo"] = (i.get("repository_url") or "").replace(API + "/repos/", "")
    if hits:
        return hits

    s, d = request("GET", "/installation/repositories?per_page=100", tok)
    repos = [r["full_name"] for r in d.get("repositories", [])] if s == 200 else []

    def one(full):
        ss, ii = request("GET", f"/repos/{full}/issues?state=open&per_page=50", tok)
        if ss != 200 or not isinstance(ii, list):
            return []
        out = []
        for i in ii:
            if "pull_request" in i:
                continue
            i["repo"] = full
            out.append(i)
        return out

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, r) for r in repos]
        for f in cf.as_completed(futs, timeout=None):
            try:
                hits.extend(f.result())
            except Exception as e:
                print(f"scan worker: {type(e).__name__}: {e}", file=sys.stderr)
    return hits


def main():
    a = args()
    cfg = config()
    action = a.get("action") or "status"
    fmt = a.get("format") or "json"

    token, kind = resolve_token(cfg, a)

    if action == "token":
        print(token)
        return

    if action == "status":
        if kind == "app":
            jwt = jwt_for(cfg, a)
            s_app, app = request("GET", "/app", jwt)
            insts = installations(jwt)
            print(json.dumps({
                "auth": "app",
                "app": {"id": app.get("id"), "slug": app.get("slug"), "name": app.get("name")},
                "installations": [{"id": i["id"], "account": i["account"]["login"]} for i in insts],
                "token_ok": True,
            }, ensure_ascii=False, indent=1))
        else:
            s, me = request("GET", "/user", token)
            print(json.dumps({"auth": "gh", "user": me.get("login"), "token_ok": s == 200}, ensure_ascii=False, indent=1))
        return

    if action == "installations":
        if kind != "app":
            print(json.dumps({"auth": "gh", "note": "当前用的是 gh 登录态"}, ensure_ascii=False))
            return
        jwt = jwt_for(cfg, a)
        rows = []
        for i in installations(jwt):
            tok, _ = installation_token(cfg, {**a, "installation_id": i["id"]})
            s, r = request("GET", "/installation/repositories?per_page=1", tok)
            rows.append({"id": i["id"], "account": i["account"]["login"],
                         "type": i["account"]["type"], "repos": r.get("total_count") if s == 200 else None})
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    if action == "repos":
        if kind == "app":
            s, d = request("GET", "/installation/repositories?per_page=100", token)
            items = d.get("repositories", []) if isinstance(d, dict) else []
        else:
            s, d = request("GET", "/user/repos?per_page=100&sort=updated", token)
            items = d if isinstance(d, list) else []
        print(json.dumps([{"full_name": r["full_name"], "private": r["private"],
                           "default_branch": r["default_branch"]} for r in items], ensure_ascii=False, indent=1))
        return

    if action == "repo":
        s, d = request("GET", repo_path(a), token)
        print(json.dumps({k: d.get(k) for k in ("full_name", "description", "default_branch", "private",
                                                "topics", "open_issues_count", "stargazers_count", "pushed_at")},
                         ensure_ascii=False, indent=1))
        return

    if action == "branch_list":
        s, d = request("GET", repo_path(a, "/branches?per_page=100"), token)
        print(json.dumps([b["name"] for b in d] if isinstance(d, list) else d, ensure_ascii=False))
        return

    if action in ("issue_list", "pr_list"):
        what = "issues" if action == "issue_list" else "pulls"
        state = a.get("state") or "open"
        limit = int(a.get("limit") or 20)
        s, d = request("GET", repo_path(a, f"/{what}?state={state}&per_page={limit}"), token)
        items = d if isinstance(d, list) else []
        print(json.dumps([slim_issue(i) for i in items], ensure_ascii=False, indent=1))
        return

    if action in ("issue_get", "pr_get"):
        what = "issues" if action == "issue_get" else "pulls"
        s, d = request("GET", repo_path(a, f"/{what}/{a.get('number')}"), token)
        print(json.dumps({k: d.get(k) for k in ("number", "title", "state", "body", "user",
                                                "labels", "created_at", "html_url")}, ensure_ascii=False, indent=1))
        return

    if action == "issue_create":
        if not a.get("title"):
            raise SystemExit("issue_create 需要 title")
        body = {"title": a["title"], "body": a.get("body", "")}
        if a.get("labels"):
            body["labels"] = a["labels"]
        if a.get("assignees"):
            body["assignees"] = a["assignees"]
        s, d = request("POST", repo_path(a, "/issues"), token, body)
        print(json.dumps({"status": s, "number": d.get("number"), "url": d.get("html_url")}, ensure_ascii=False))
        return

    if action == "issue_update":
        body = {}
        for field in ("title", "body", "state"):
            if a.get(field):
                body[field] = a[field]
        if a.get("labels") is not None:
            body["labels"] = a["labels"]
        if a.get("assignees") is not None:
            body["assignees"] = a["assignees"]
        s, d = request("PATCH", repo_path(a, f"/issues/{a.get('number')}"), token, body)
        print(json.dumps({"status": s, "state": d.get("state"), "labels": [l["name"] for l in d.get("labels", [])] if isinstance(d, dict) else None}, ensure_ascii=False))
        return

    if action in ("issue_comment", "pr_comment"):
        s, d = request("POST", repo_path(a, f"/issues/{a.get('number')}/comments"), token, {"body": a.get("body", "")})
        print(json.dumps({"status": s, "url": d.get("html_url"), "author": (d.get("user") or {}).get("login")}, ensure_ascii=False))
        return

    if action == "comment_list":
        s, d = request("GET", repo_path(a, f"/issues/{a.get('number')}/comments?per_page=100"), token)
        items = d if isinstance(d, list) else []
        print(json.dumps([{"id": c["id"], "author": c["user"]["login"], "body": c["body"][:400],
                           "created": c["created_at"]} for c in items], ensure_ascii=False, indent=1))
        return

    if action == "comment_delete":
        cid = a.get("number") or a.get("comment_id")
        s, d = request("DELETE", repo_path(a, f"/issues/comments/{cid}"), token)
        print(json.dumps({"status": s, "ok": s == 204}, ensure_ascii=False))
        return

    if action == "issue_close":
        s, d = request("PATCH", repo_path(a, f"/issues/{a.get('number')}"), token, {"state": "closed"})
        print(json.dumps({"status": s, "state": d.get("state"), "url": d.get("html_url")}, ensure_ascii=False))
        return

    if action == "pr_create":
        body = {"title": a.get("title") or a.get("body", "update"), "head": a.get("head"),
                "base": a.get("base") or "main", "body": a.get("body", ""), "draft": bool(a.get("draft"))}
        if not body["head"]:
            raise SystemExit("pr_create 需要 head（如 yxxbc:fix/xxx）")
        s, d = request("POST", repo_path(a, "/pulls"), token, body)
        print(json.dumps({"status": s, "number": d.get("number"), "url": d.get("html_url")}, ensure_ascii=False))
        return

    if action == "pr_review":
        s, d = request("POST", repo_path(a, f"/pulls/{a.get('number')}/reviews"), token,
                       {"body": a.get("body", ""), "event": a.get("event") or "COMMENT"})
        print(json.dumps({"status": s, "state": d.get("state"), "url": d.get("html_url")}, ensure_ascii=False))
        return

    if action == "pr_merge":
        s, d = request("PUT", repo_path(a, f"/pulls/{a.get('number')}/merge"), token,
                       {"merge_method": a.get("merge_method") or "squash"})
        print(json.dumps({"status": s, "merged": d.get("merged"), "message": d.get("message")}, ensure_ascii=False))
        return

    if action == "release_list":
        s, d = request("GET", repo_path(a, "/releases?per_page=10"), token)
        items = d if isinstance(d, list) else []
        print(json.dumps([{"tag": r["tag_name"], "name": r["name"],
                           "assets": [x["name"] for x in r.get("assets", [])]} for r in items], ensure_ascii=False, indent=1))
        return

    if action == "workflow_list":
        s, d = request("GET", repo_path(a, "/actions/workflows?per_page=100"), token)
        items = d.get("workflows", []) if isinstance(d, dict) else []
        print(json.dumps([{"id": w["id"], "name": w["name"], "path": w["path"], "state": w["state"]} for w in items], ensure_ascii=False, indent=1))
        return

    if action == "workflow_dispatch":
        wf = a.get("workflow")
        if not wf:
            raise SystemExit("workflow_dispatch 需要 workflow")
        s, d = request("POST", repo_path(a, f"/actions/workflows/{wf}/dispatches"), token,
                       {"ref": a.get("ref") or "main", "inputs": a.get("inputs") or {}})
        print(json.dumps({"status": s, "ok": s in (204, 201)}, ensure_ascii=False))
        return

    if action == "run_list":
        s, d = request("GET", repo_path(a, "/actions/runs?per_page=10"), token)
        items = d.get("workflow_runs", []) if isinstance(d, dict) else []
        print(json.dumps([{"id": r["id"], "name": r["name"], "status": r["status"],
                           "conclusion": r["conclusion"], "branch": r["head_branch"]} for r in items], ensure_ascii=False, indent=1))
        return

    if action == "file_get":
        p = a.get("path")
        if not p:
            raise SystemExit("file_get 需要 path")
        s, d = request("GET", repo_path(a, f"/contents/{urllib.parse.quote(p)}?ref={a.get('ref') or 'main'}"), token)
        if s == 200 and d.get("content"):
            print(base64.b64decode(d["content"]).decode("utf-8", "ignore"))
        else:
            print(json.dumps(d, ensure_ascii=False)[:400])
        return

    if action == "file_list":
        p = a.get("path") or ""
        s, d = request("GET", repo_path(a, f"/contents/{urllib.parse.quote(p)}?ref={a.get('ref') or 'main'}"), token)
        items = d if isinstance(d, list) else []
        print(json.dumps([{"name": i["name"], "type": i["type"], "size": i.get("size")} for i in items], ensure_ascii=False, indent=1))
        return

    if action == "file_put":
        p = a.get("path")
        if not p or a.get("content") is None:
            raise SystemExit("file_put 需要 path 和 content")
        ref = a.get("ref") or "main"
        s0, cur = request("GET", repo_path(a, f"/contents/{urllib.parse.quote(p)}?ref={ref}"), token)
        body = {"message": a.get("message") or f"bot: update {p}",
                "content": base64.b64encode(a["content"].encode()).decode(), "branch": ref}
        if s0 == 200 and cur.get("sha"):
            body["sha"] = cur["sha"]
        s, d = request("PUT", repo_path(a, f"/contents/{urllib.parse.quote(p)}"), token, body)
        print(json.dumps({"status": s, "commit": (d.get("commit") or {}).get("sha"),
                          "url": (d.get("content") or {}).get("html_url")}, ensure_ascii=False))
        return

    if action == "scan":
        if kind == "app":
            jwt = jwt_for(cfg, a)
            targets = installations(jwt)
            if not a.get("all_installations", True):
                owner = (a.get("owner") or cfg.get("owner") or "").lower()
                targets = [i for i in targets if i.get("account", {}).get("login", "").lower() == owner]
        else:
            targets = [{"id": 0, "account": {"login": a.get("owner") or cfg.get("owner") or "",
                                               "type": "Organization" if (a.get("owner") or "").endswith("Team") else "User"}}]
        rows, errors = [], []
        for inst in targets:
            try:
                tok = token if inst["id"] == 0 else installation_token(cfg, {**a, "installation_id": inst["id"]})[0]
                for i in scan_installation(tok, inst, int(a.get("limit") or 100)):
                    rows.append(slim_issue(i))
            except Exception as e:
                errors.append(f"{inst.get('account',{}).get('login')}: {type(e).__name__}")
        rows.sort(key=lambda r: (r["repo"] or "", r["number"]))
        if fmt == "md":
            lines = [f"共 {len(rows)} 条 open issue", ""]
            for r in rows:
                lines.append(f"- [{r['repo']}#{r['number']}]({r['url']}) {r['title']} — @{r['author']}")
            print("\n".join(lines))
        else:
            print(json.dumps({"count": len(rows), "issues": rows, "errors": errors}, ensure_ascii=False, indent=1))
        return

    if action == "search":
        q = a.get("query")
        if not q:
            raise SystemExit("search 需要 query")
        scope = a.get("scope") or "issues"
        if scope == "issues":
            extra = "is:issue"
        elif scope == "prs":
            extra = "is:pr"
        else:
            extra = ""
        if a.get("owner"):
            extra += f" user:{a['owner']}" if not a["owner"].endswith("Team") else f" org:{a['owner']}"
        endpoint = f"/search/{'repositories' if scope == 'repos' else 'issues' if scope in ('issues','prs') else 'code'}"
        endpoint += f"?q={urllib.parse.quote((q + ' ' + extra).strip())}&per_page={int(a.get('limit') or 20)}"
        s, d = request("GET", endpoint, token)
        items = d.get("items", []) if isinstance(d, dict) else []
        print(json.dumps({"status": s, "total": d.get("total_count"), "items": [
            {"name": i.get("full_name") or i.get("name"), "number": i.get("number"),
             "title": i.get("title") or i.get("description"), "url": i.get("html_url")} for i in items
        ]}, ensure_ascii=False, indent=1))
        return

    if action == "api":
        endpoint = a.get("endpoint")
        if not endpoint:
            raise SystemExit("api 需要 endpoint")
        s, d = request((a.get("method") or "GET").upper(), endpoint, token, a.get("payload"))
        print(json.dumps({"status": s, "data": d}, ensure_ascii=False)[:6000])
        return

    raise SystemExit(f"未知动作：{action}")


if __name__ == "__main__":
    main()
