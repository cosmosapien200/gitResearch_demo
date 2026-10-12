import os
import httpx
from mcp.server.fastmcp import FastMCP


# -- SETUP ---------------------------------------------------------------
mcp = FastMCP("DevPulse GitHub Intelligence Server")


GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")    # optional, raises rate limit 60 -> 5000/hr


# GitHub requires a User-Agent header on every request, or it rejects the call.
_HEADERS = {"User-Agent": "DevPulse-MCP-Server"}
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
    return (f"{data['full_name']}: {data.get('description', 'No description')}\n"
            f"{data['stargazers_count']:,} stars | {data['forks_count']:,} forks | "
            f"Language: {data.get('language', 'N/A')} | License: {license_name}\n"
            f"Open issues: {data['open_issues_count']} | Default branch: {data['default_branch']}")



@mcp.tool()
def list_open_issues(owner: str, repo: str, limit: int = 5) -> str:
    """List currently open issues for a GitHub repository.
    Args: owner, repo, limit (max issues to return, default 5)
    """
    status, data = _get(f"/repos/{owner}/{repo}/issues", params={"state": "open", "per_page": limit})
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} issues")
    if not data:
        return f"{owner}/{repo} currently has no open issues."
    lines = [f"Open issues for {owner}/{repo} (showing {len(data)}):"]
    for issue in data:
        labels = ", ".join(l["name"] for l in issue.get("labels", [])) or "no labels"
        lines.append(f"- #{issue['number']}: {issue['title']} [{labels}]")
    return "\n".join(lines)



@mcp.tool()
def list_contributors(owner: str, repo: str, limit: int = 5) -> str:
    """List the top contributors to a GitHub repository by commit count.
    Args: owner, repo, limit (max contributors to return, default 5)
    """
    status, data = _get(f"/repos/{owner}/{repo}/contributors", params={"per_page": limit})
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
    if status != 200:
        return _friendly_error(status, data, f"{owner}/{repo} latest release")
    return (f"Latest release of {owner}/{repo}: {data['tag_name']} ({data.get('name', '')})\n"
            f"Published: {data['published_at']}\n"
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



# -- PROMPT (reusable instruction template) --------------------------------


@mcp.prompt()
def issue_triage_prompt() -> str:
    """A structured workflow for triaging a GitHub issue."""
    return """
    Review this GitHub issue and:
    1. Classify it as exactly one of: Bug / Feature Request / Question / Documentation
    2. Assign a priority: Critical / High / Medium / Low, based on impact described
    3. Suggest which existing label(s) it should carry
    4. Write a one-sentence summary suitable for a triage dashboard
    Be concise -- output should fit in 4 short lines.
    """



# -- RUN THE SERVER ---------------------------------------------------------
# NOTE: every line below logs to stderr, never stdout -- stdout is reserved
# exclusively for MCP's JSON-RPC protocol messages on the stdio transport.


if __name__ == "__main__":
    import sys
    print("Starting DevPulse MCP Server...", file=sys.stderr)
    print("5 tools: search_repositories, get_repo_details, list_open_issues, "
          "list_contributors, get_latest_release", file=sys.stderr)
    print("1 resource: github://repo/{owner}/{repo}/summary", file=sys.stderr)
    print("1 prompt: issue_triage_prompt", file=sys.stderr)
    if not GITHUB_TOKEN:
        print("NOTE: no GITHUB_TOKEN set -- limited to 60 unauthenticated requests/hour.",
              file=sys.stderr)
    print("Waiting for MCP client connections (Ctrl+C to stop)...\n", file=sys.stderr)
    mcp.run()
