"""`build_report` + the AppSpec issue-URL helpers.

The property that matters: an installed binary — with no README or AGENTS.md
beside it — can still say exactly where a problem is reported. So this is pure,
offline, and degrades cleanly when a tool has no repo set yet.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from agentcli import AppSpec, build_report

DRONE = AppSpec(
    name="drone-cli", env_prefix="DRONECLI", repo="alexander-zierhut/agent-tool-drone-cli"
)
NOREPO = AppSpec(name="x-cli", env_prefix="XCLI")


def test_url_helpers_build_the_expected_links():
    assert DRONE.repo_url() == "https://github.com/alexander-zierhut/agent-tool-drone-cli"
    assert DRONE.issues_url() == DRONE.repo_url() + "/issues"
    assert DRONE.new_issue_url() == DRONE.repo_url() + "/issues/new"


def test_new_issue_url_urlencodes_a_prefilled_body():
    url = DRONE.new_issue_url(title="boom", body="line one\n## heading")
    parsed = urlparse(url)
    assert parsed.path.endswith("/issues/new")
    q = parse_qs(parsed.query)
    # Round-trips through the query string intact — newlines and '#' survive.
    assert q["title"] == ["boom"]
    assert q["body"] == ["line one\n## heading"]


def test_empty_repo_yields_empty_urls_not_a_crash():
    assert NOREPO.repo_url() == ""
    assert NOREPO.issues_url() == ""
    assert NOREPO.new_issue_url(body="x") == ""


def test_a_malformed_repo_slug_is_rejected_at_construction():
    with pytest.raises(ValueError):
        AppSpec(name="x-cli", env_prefix="XCLI", repo="not-a-slug")
    with pytest.raises(ValueError):
        AppSpec(name="x-cli", env_prefix="XCLI", repo="too/many/slashes")


def test_build_report_is_pure_and_carries_the_version():
    r = build_report(DRONE, "1.2.3")
    assert r["tool"] == "drone-cli"
    assert r["version"] == "1.2.3"
    assert r["published"] is True
    assert r["repo"] == "alexander-zierhut/agent-tool-drone-cli"
    assert r["issues"].endswith("/issues")
    assert "/issues/new?" in r["newIssue"]
    # The version is pre-filled into the issue body, not just reported alongside.
    assert "1.2.3" in parse_qs(urlparse(r["newIssue"]).query)["body"][0]
    assert r["gh"].startswith("gh issue create --repo alexander-zierhut/agent-tool-drone-cli")
    assert any("drone-cli --version" in item for item in r["include"])


def test_build_report_without_a_repo_still_succeeds():
    r = build_report(NOREPO, "0.0.1")
    assert r["published"] is False
    assert r["repo"] is None
    assert r["issues"] is None
    assert r["newIssue"] is None
    assert r["gh"] is None
    # Still tells the caller *why* there's no link, rather than looking broken.
    assert "no published repository" in r["note"]
