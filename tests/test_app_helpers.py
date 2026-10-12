"""Tests for the repo-name parsing used by the /Health, /Compare and /Triage commands."""

from app import parse_repos, prompt_arguments


def test_parse_single_repo():
    assert parse_repos("fastapi/fastapi") == [("fastapi", "fastapi")]


def test_parse_github_url():
    assert parse_repos("https://github.com/pallets/flask.git") == [("pallets", "flask")]


def test_parse_two_repos():
    repos = parse_repos("fastapi/fastapi vs pallets/flask")
    assert repos == [("fastapi", "fastapi"), ("pallets", "flask")]


def test_parse_no_repo():
    assert parse_repos("hello there") == []


def test_prompt_arguments():
    assert prompt_arguments("repo_health_check", [("o", "r")]) == {"owner": "o", "repo": "r"}
    assert prompt_arguments("compare_repos", [("a", "b"), ("c", "d")]) == {"repo_a": "a/b", "repo_b": "c/d"}
