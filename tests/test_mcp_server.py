"""Tests for the MCP server tools. GitHub is never called: _get is replaced
with a fake that returns canned responses."""

import base64
from datetime import datetime, timedelta, timezone

import mcp_server


def fake_get(responses):
    """Return a fake _get that answers by URL path."""
    def _get(path, params=None):
        return responses[path]
    return _get


def iso(days_ago):
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_days_ago():
    assert mcp_server._days_ago(iso(0)) == "today"
    assert mcp_server._days_ago(iso(1)) == "1 day ago"
    assert mcp_server._days_ago(iso(45)) == "45 days ago"


def test_clamp_keeps_limit_in_range():
    assert mcp_server._clamp(0) == 1
    assert mcp_server._clamp(5) == 5
    assert mcp_server._clamp(5000) == 20


def test_repo_details_with_missing_description(monkeypatch):
    # GitHub sends null for description/language/license on some repos
    repo = {
        "full_name": "someone/tiny", "description": None, "language": None, "license": None,
        "stargazers_count": 3, "forks_count": 0, "open_issues_count": 1,
        "default_branch": "main", "created_at": iso(400), "pushed_at": iso(10),
    }
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/someone/tiny": (200, repo)}))

    result = mcp_server.get_repo_details("someone", "tiny")

    assert "No description" in result
    assert "None" not in result
    assert "Last push: 10 days ago" in result


def test_open_issues_skips_pull_requests(monkeypatch):
    items = [
        {"number": 1, "title": "Crash on start", "labels": [{"name": "bug"}],
         "created_at": iso(2), "comments": 4, "body": "It crashes"},
        {"number": 2, "title": "Add feature X", "labels": [], "created_at": iso(3),
         "comments": 0, "body": "", "pull_request": {"url": "..."}},
    ]
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/o/r/issues": (200, items)}))

    result = mcp_server.list_open_issues("o", "r")

    assert "#1: Crash on start [bug]" in result
    assert "#2" not in result


def test_readme_is_decoded(monkeypatch):
    content = base64.b64encode("# Hello\nA test project".encode()).decode()
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/o/r/readme": (200, {"content": content})}))

    assert "A test project" in mcp_server.get_readme("o", "r")


def test_recent_commits(monkeypatch):
    commits = [{
        "sha": "abc1234567", "author": {"login": "dev1"},
        "commit": {"message": "Fix login bug\n\nlonger text", "author": {"name": "Dev", "date": iso(3)}},
    }]
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/o/r/commits": (200, commits)}))

    result = mcp_server.get_recent_commits("o", "r")

    assert "latest was 3 days ago" in result
    assert "abc1234 by dev1: Fix login bug" in result


def test_no_releases_is_not_an_error(monkeypatch):
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/o/r/releases/latest": (404, {})}))

    assert mcp_server.get_latest_release("o", "r") == "o/r has no published releases."


def test_rate_limit_message(monkeypatch):
    error = (403, {"message": "API rate limit exceeded for 1.2.3.4"})
    monkeypatch.setattr(mcp_server, "_get", fake_get({"/repos/o/r": error}))

    result = mcp_server.get_repo_details("o", "r")

    assert result.startswith("ERROR: GitHub API rate limit exceeded")
    assert "GITHUB_TOKEN" in result


def test_health_prompt_has_repo_and_rules():
    text = mcp_server.repo_health_check("fastapi", "fastapi")
    assert "fastapi/fastapi" in text
    assert "last commit < 30 days" in text
    assert "| Maintenance |" in text


def test_compare_prompt_names_both_repos():
    text = mcp_server.compare_repos("fastapi/fastapi", "pallets/flask")
    assert "fastapi/fastapi vs pallets/flask" in text
