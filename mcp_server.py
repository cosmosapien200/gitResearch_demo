import base64
import os
from datetime import datetime, timezone

import httpx
from mcp.server.fastmcp import FastMCP


# -- SETUP ---------------------------------------------------------------
mcp = FastMCP("gitResearch GitHub Intelligence Server")


GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")    # optional, raises rate limit 60 -> 5000/hr


# GitHub requires a User-Agent header on every request, or it rejects the call.
_HEADERS = {"User-Agent": "gitResearch-MCP-Server"}
if GITHUB_TOKEN:
    _HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"



def _get(path: str, params: dict | None = None) -> tuple[int, dict]:
    """Single shared real HTTP GET against the GitHub REST API."""
    try:
        r = httpx.get(f"{GITHUB_API}{path}", headers=_HEADERS, params=params,
                       timeout=10, follow_redirects=True)
        return r.status_code, (r.json() if r.content else {})
    except httpx.RequestError as e:
        return -1, {"message": f"Network error: {e}"}



def _friendly_error(status: int, data: dict, context: str) -> str:
    if status == 403 and "rate limit" in str(data.get("message", "")).lower():
        return (f"ERROR: GitHub API rate limit exceeded. {context} "
                f"Unauthenticated requests are capped at 60/hour -- set the GITHUB_TOKEN "
                f"environment variable to raise this to 5,000/hour.")
    if status == 404:
        return f"ERROR: {context} not found on GitHub (404)."
    if status == -1:
        return f"ERROR: could not reach GitHub. {data.get('message')}"
    return f"ERROR: GitHub API returned status {status} for {context}: {data.get('message', 'unknown error')}"



def _days_ago(iso_timestamp: str) -> str:
    """'2026-10-07T10:03:29Z' -> '5 days ago'. Computed here because the LLM
    doesn't reliably know today's date."""
    then = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - then).days
    return "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"



def _clamp(limit: int, maximum: int = 20) -> int:
    return max(1, min(limit, maximum))



# -- TOOLS (real, live actions) -------------------------------------------


@mcp.tool()
def search_repositories(query: str) -> str:
    """Search public GitHub repositories by keyword, topic, or language.
    Args: query e.g. 'machine learning language:python'
    """
    status, data = _get("/search/repositories", params={"q": query, "sort": "stars", "per_page": 5})
    if status != 200:
        return _friendly_error(status, data, f"search for '{query}'")
    items = data.get("items", [])
    if not items:
        return f"No repositories found for query: {query}"
    lines = [f"Top results for '{query}':"]
    for repo in items:
        lines.append(f"- {repo['full_name']} *{repo['stargazers_count']} -- {repo['description']}")
    return "\n".join(lines)



@mcp.tool()
def get_repo_details(owner: str, repo: str) -> str:
    """Get live details for a GitHub repository: stars, forks, license, description.
    Args: owner e.g. 'facebook', repo e.g. 'react'
    """
    status, data = _get(f"/repos/{owner}/{repo}")
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo}")
    license_name = (data.get("license") or {}).get("name", "No license")
    return (f"{data['full_name']}: {data.get('description') or 'No description'}\n"
            f"{data['stargazers_count']:,} stars | {data['forks_count']:,} forks | "
            f"Language: {data.get('language') or 'N/A'} | License: {license_name}\n"
            f"Open issues + PRs: {data['open_issues_count']} | Default branch: {data['default_branch']}\n"
            f"Created: {data['created_at'][:10]} | Last push: {_days_ago(data['pushed_at'])}"
            f"{' | ARCHIVED (read-only)' if data.get('archived') else ''}")



@mcp.tool()
def get_readme(owner: str, repo: str) -> str:
    """Get the README of a GitHub repository, to understand what the project does.
    Args: owner, repo
    """
    status, data = _get(f"/repos/{owner}/{repo}/readme")
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} README")
    # GitHub returns the file base64-encoded inside the JSON response.
    text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    if len(text) > 3000:
        text = text[:3000] + "\n...(truncated)"
    return f"README of {owner}/{repo}:\n\n{text}"



@mcp.tool()
def get_recent_commits(owner: str, repo: str, limit: int = 10) -> str:
    """List the most recent commits on a repository's default branch, with how long ago
    each was made. Use this to judge whether a project is actively maintained.
    Args: owner, repo, limit (max commits to return, default 10)
    """
    status, data = _get(f"/repos/{owner}/{repo}/commits", params={"per_page": _clamp(limit)})
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} commits")
    if not data:
        return f"{owner}/{repo} has no commits."
    latest = data[0]["commit"]["author"]["date"]
    lines = [f"Recent commits to {owner}/{repo} (latest was {_days_ago(latest)}):"]
    for c in data:
        date = c["commit"]["author"]["date"]
        author = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
        message = c["commit"]["message"].splitlines()[0][:80]
        lines.append(f"- {date[:10]} ({_days_ago(date)}) {c['sha'][:7]} by {author}: {message}")
    return "\n".join(lines)



@mcp.tool()
def list_open_issues(owner: str, repo: str, limit: int = 5) -> str:
    """List currently open issues (not pull requests) for a GitHub repository,
    with labels, age, comment count and the start of each issue's description.
    Args: owner, repo, limit (max issues to return, default 5)
    """
    # The issues endpoint also returns pull requests; over-fetch, then filter them out.
    status, data = _get(f"/repos/{owner}/{repo}/issues",
                        params={"state": "open", "per_page": min(_clamp(limit) * 3, 100)})
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} issues")
    issues = [i for i in data if "pull_request" not in i][:_clamp(limit)]
    if not issues:
        return f"{owner}/{repo} currently has no open issues."
    lines = [f"Open issues for {owner}/{repo} (showing {len(issues)}):"]
    for issue in issues:
        labels = ", ".join(l["name"] for l in issue.get("labels", [])) or "no labels"
        body = " ".join((issue.get("body") or "").split())[:200]
        lines.append(f"- #{issue['number']}: {issue['title']} [{labels}] "
                     f"(opened {_days_ago(issue['created_at'])}, {issue['comments']} comments)\n"
                     f"  {body or '(no description)'}")
    return "\n".join(lines)



@mcp.tool()
def list_contributors(owner: str, repo: str, limit: int = 5) -> str:
    """List the top contributors to a GitHub repository by commit count.
    Args: owner, repo, limit (max contributors to return, default 5)
    """
    status, data = _get(f"/repos/{owner}/{repo}/contributors", params={"per_page": _clamp(limit)})
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} contributors")
    if not data:
        return f"No contributor data available for {owner}/{repo}."
    lines = [f"Top contributors to {owner}/{repo}:"]
    for c in data:
        lines.append(f"- {c['login']}: {c['contributions']} commits")
    return "\n".join(lines)



@mcp.tool()
def get_latest_release(owner: str, repo: str) -> str:
    """Get the latest published release for a GitHub repository.
    Args: owner, repo
    """
    status, data = _get(f"/repos/{owner}/{repo}/releases/latest")
    if status == 404:
        return f"{owner}/{repo} has no published releases."
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} latest release")
    return (f"Latest release of {owner}/{repo}: {data['tag_name']} ({data.get('name') or ''})\n"
            f"Published: {data['published_at'][:10]} ({_days_ago(data['published_at'])})\n"
            f"Notes: {(data.get('body') or 'No release notes.')[:300]}")



# -- RESOURCE (read-only data, addressed by URI) ---------------------------


@mcp.resource("github://repo/{owner}/{repo}/summary")
def repo_summary_resource(owner: str, repo: str) -> str:
    """A pre-formatted, read-only snapshot of a repository's key stats."""
    status, data = _get(f"/repos/{owner}/{repo}")
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo}")
    return (f"REPO SUMMARY: {data['full_name']}\n"
            f"Stars: {data['stargazers_count']} | Forks: {data['forks_count']} | "
            f"Open Issues: {data['open_issues_count']}\n"
            f"Created: {data['created_at']} | Last push: {data['pushed_at']}")



# -- PROMPTS (reusable workflow templates) ---------------------------------
# The client fetches these by name with get_prompt() and sends the result to
# the LLM as the user's request. The server owns the workflow; the agent just
# follows it, choosing and calling the tools itself.


HEALTH_RULES = """Rate each area 🟢 / 🟡 / 🔴 using these rules:
- Maintenance: last commit < 30 days 🟢, 30-180 days 🟡, older 🔴 (an archived repo is always 🔴)
- Releases: latest release < 90 days 🟢, 90-365 days 🟡, older or none 🔴
- Community: 5+ active contributors or 1,000+ stars 🟢, a few contributors 🟡, a single person 🔴
- Issues: open issues getting attention (recent, commented) 🟢, piling up 🟡, ignored 🔴
- License: OSI license (MIT, Apache, BSD, GPL...) 🟢, other 🟡, none 🔴"""


@mcp.prompt()
def repo_health_check(owner: str, repo: str) -> str:
    """Full health report for one repository: is it maintained and safe to depend on?"""
    return f"""Run a health check on the GitHub repository {owner}/{repo}.

First gather the facts with your tools: get_repo_details, get_readme, get_recent_commits,
get_latest_release, list_contributors and list_open_issues for {owner}/{repo}.

{HEALTH_RULES}

Then write the report in exactly this markdown format:

## 🩺 Health Report: {owner}/{repo}
**Verdict:** <🟢 Healthy / 🟡 Slowing down / 🔴 Risky> — <one sentence why>

**What it is:** <2 sentences from the README>

| Area | Finding | Status |
|---|---|---|
| Maintenance | ... | 🟢/🟡/🔴 |
| Releases | ... | ... |
| Community | ... | ... |
| Issues | ... | ... |
| License | ... | ... |

**Should you use it?** <2-3 sentences of practical advice>

Only use facts returned by the tools. Reply with only the markdown above: no extra
sections, nothing before or after it, no follow-up questions."""


@mcp.prompt()
def compare_repos(repo_a: str, repo_b: str) -> str:
    """Side-by-side comparison of two repositories (each given as owner/repo)."""
    return f"""Compare the GitHub repositories {repo_a} and {repo_b} to help a developer choose one.

For EACH repository, gather the facts with your tools: get_repo_details, get_readme,
get_recent_commits, get_latest_release and list_contributors.

{HEALTH_RULES}

Then write the comparison in exactly this markdown format:

## ⚖️ {repo_a} vs {repo_b}

| | {repo_a} | {repo_b} |
|---|---|---|
| What it is | ... | ... |
| Stars / Forks | ... | ... |
| Language / License | ... | ... |
| Maintenance | <finding> 🟢/🟡/🔴 | ... |
| Releases | ... | ... |
| Community | ... | ... |

**Pick {repo_a} if:** <one sentence>
**Pick {repo_b} if:** <one sentence>
**Overall:** <one sentence recommendation>

Only use facts returned by the tools. Reply with only the markdown above: no extra
sections, nothing before or after it, no follow-up questions."""


@mcp.prompt()
def triage_issues(owner: str, repo: str) -> str:
    """Triage the open issues of a repository into type and priority."""
    return f"""Triage the open issues of the GitHub repository {owner}/{repo}.

First fetch them with list_open_issues (owner="{owner}", repo="{repo}", limit=10).

For each issue decide:
1. Type: exactly one of Bug / Feature / Question / Docs
2. Priority: Critical / High / Medium / Low, based on the impact described
   (crashes, security, data loss, many users affected -> higher)
3. A one-line summary suitable for a triage dashboard

Then write the result in exactly this markdown format:

## 🗂️ Issue Triage: {owner}/{repo}

| Issue | Type | Priority | Summary |
|---|---|---|---|
| #<number> <short title> | ... | ... | ... |

**Fix first:** <the 3 most urgent issue numbers, one short reason each>

Only use facts returned by the tools. Reply with only the markdown above: no extra
sections, nothing before or after it, no follow-up questions."""



# -- RUN THE SERVER ---------------------------------------------------------
# NOTE: every line below logs to stderr, never stdout -- stdout is reserved
# exclusively for MCP's JSON-RPC protocol messages on the stdio transport.


if __name__ == "__main__":
    import sys
    print("Starting gitResearch MCP Server...", file=sys.stderr)
    print("7 tools: search_repositories, get_repo_details, get_readme, get_recent_commits, "
          "list_open_issues, list_contributors, get_latest_release", file=sys.stderr)
    print("1 resource: github://repo/{owner}/{repo}/summary", file=sys.stderr)
    print("3 prompts: repo_health_check, compare_repos, triage_issues", file=sys.stderr)
    if not GITHUB_TOKEN:
        print("NOTE: no GITHUB_TOKEN set -- limited to 60 unauthenticated requests/hour.",
              file=sys.stderr)
    print("Waiting for MCP client connections (Ctrl+C to stop)...\n", file=sys.stderr)
    mcp.run()
