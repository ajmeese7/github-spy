"""Collect and normalize public events for a GitHub user."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from github_spy.models import CollectionResult, NormalizedEvent

if TYPE_CHECKING:
    from github_spy.client import GitHubClient
    from github_spy.storage import Storage

GITHUB_WEB = "https://github.com"


def _first_line(text: str | None, max_len: int = 120) -> str:
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    if len(line) > max_len:
        line = line[: max_len - 1].rstrip() + "…"
    return line


def _derive_url_and_details(
    event_type: str,
    repo_name: str | None,
    action: str | None,
    payload: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return (url, details) best-effort derivations for a given event.

    Defensive: unexpected shapes fall through to a generic repo URL and the
    event type as details. Never raises.
    """
    repo_url = f"{GITHUB_WEB}/{repo_name}" if repo_name else None

    if event_type == "PushEvent":
        commits = payload.get("commits") or []
        ref = (payload.get("ref") or "").removeprefix("refs/heads/")
        head_sha = payload.get("head") or (commits[-1].get("sha") if commits else None)
        before = payload.get("before")
        head_msg = _first_line(commits[-1].get("message")) if commits else ""
        # The public events API trims the commits array; fall back to size/distinct_size.
        count = (
            len(commits) if commits else payload.get("distinct_size") or payload.get("size") or 0
        )
        url = None
        if repo_name and before and head_sha:
            url = f"{GITHUB_WEB}/{repo_name}/compare/{before}...{head_sha}"
        elif repo_name and head_sha:
            url = f"{GITHUB_WEB}/{repo_name}/commit/{head_sha}"
        details = f"pushed {count} commit{'s' if count != 1 else ''}" if count else "pushed"
        if ref:
            details = f"{details} to {ref}"
        if head_msg:
            details = f"{details} — {head_msg}"
        return url, details

    if event_type == "PullRequestEvent":
        pr = payload.get("pull_request") or {}
        number = pr.get("number") or payload.get("number")
        title = _first_line(pr.get("title"))
        url = pr.get("html_url")
        if not url and repo_name and number:
            url = f"{GITHUB_WEB}/{repo_name}/pull/{number}"
        url = url or repo_url
        details = f"{action or 'updated'} PR #{number}" if number else action or "pull request"
        if title:
            details = f"{details}: {title}"
        return url, details

    if event_type == "PullRequestReviewEvent":
        review = payload.get("review") or {}
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        state = review.get("state") or action
        url = review.get("html_url") or pr.get("html_url")
        if not url and repo_name and number:
            url = f"{GITHUB_WEB}/{repo_name}/pull/{number}"
        url = url or repo_url
        details = f"review {state} on PR #{number}" if number else f"review {state}"
        return url, details

    if event_type == "PullRequestReviewCommentEvent":
        comment = payload.get("comment") or {}
        pr = payload.get("pull_request") or {}
        number = pr.get("number")
        body = _first_line(comment.get("body"))
        url = comment.get("html_url") or pr.get("html_url")
        if not url and repo_name and number:
            url = f"{GITHUB_WEB}/{repo_name}/pull/{number}"
        url = url or repo_url
        details = f"review comment on PR #{number}" if number else "review comment"
        if body:
            details = f"{details}: {body}"
        return url, details

    if event_type == "IssuesEvent":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = _first_line(issue.get("title"))
        url = issue.get("html_url") or repo_url
        details = f"{action or 'updated'} issue #{number}" if number else action or "issue"
        if title:
            details = f"{details}: {title}"
        return url, details

    if event_type == "IssueCommentEvent":
        comment = payload.get("comment") or {}
        issue = payload.get("issue") or {}
        number = issue.get("number")
        body = _first_line(comment.get("body"))
        url = comment.get("html_url") or issue.get("html_url") or repo_url
        details = f"commented on #{number}" if number else "comment"
        if body:
            details = f"{details}: {body}"
        return url, details

    if event_type == "WatchEvent":
        return repo_url, f"starred {repo_name}" if repo_name else "starred"

    if event_type == "ForkEvent":
        forkee = payload.get("forkee") or {}
        forkee_full = forkee.get("full_name")
        url = forkee.get("html_url") or repo_url
        details = f"forked to {forkee_full}" if forkee_full else "forked"
        return url, details

    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type")
        ref = payload.get("ref")
        if ref_type == "repository":
            return repo_url, f"created repository {repo_name}"
        if ref_type in {"branch", "tag"} and ref and repo_name:
            tree = f"{GITHUB_WEB}/{repo_name}/tree/{ref}"
            return tree, f"created {ref_type} {ref}"
        return repo_url, f"created {ref_type or 'ref'}"

    if event_type == "DeleteEvent":
        ref_type = payload.get("ref_type")
        ref = payload.get("ref")
        return repo_url, f"deleted {ref_type or 'ref'} {ref or ''}".strip()

    if event_type == "ReleaseEvent":
        release = payload.get("release") or {}
        tag = release.get("tag_name")
        url = release.get("html_url") or repo_url
        details = f"{action or 'released'} {tag}" if tag else action or "release"
        return url, details

    if event_type == "CommitCommentEvent":
        comment = payload.get("comment") or {}
        body = _first_line(comment.get("body"))
        url = comment.get("html_url") or repo_url
        details = "commit comment"
        if body:
            details = f"{details}: {body}"
        return url, details

    if event_type == "MemberEvent":
        member = payload.get("member") or {}
        login = member.get("login")
        details = f"{action or 'updated'} member {login}" if login else action or "member"
        return repo_url, details

    if event_type == "PublicEvent":
        return repo_url, f"made {repo_name} public" if repo_name else "made public"

    if event_type == "GollumEvent":
        pages = payload.get("pages") or []
        count = len(pages)
        details = f"wiki: {count} page{'s' if count != 1 else ''} updated"
        first_title = pages[0].get("title") if pages else None
        if first_title:
            details = f"{details} ({first_title})"
        return repo_url, details

    # Fallback: generic repo URL + event type.
    return repo_url, event_type


def normalize_event(
    raw: dict[str, Any], username: str, source_page: int, source_rank: int
) -> NormalizedEvent:
    """Convert a raw GitHub event dict into a typed NormalizedEvent."""
    payload = raw.get("payload") or {}
    repo = raw.get("repo") or {}
    actor = raw.get("actor") or {}
    event_type = raw.get("type", "Unknown")
    repo_name = repo.get("name")
    action = payload.get("action")
    url, details = _derive_url_and_details(event_type, repo_name, action, payload)
    return NormalizedEvent(
        event_id=str(raw.get("id", "")),
        username=username,
        event_type=event_type,
        created_at=raw.get("created_at"),
        repo_name=repo_name,
        actor_login=actor.get("login"),
        action=action,
        is_public=bool(raw.get("public", True)),
        raw_json=json.dumps(raw, sort_keys=True),
        source_page=source_page,
        source_rank=source_rank,
        url=url,
        details=details,
    )


def fetch_events(
    client: GitHubClient,
    storage: Storage,
    username: str,
    max_pages: int = 3,
) -> CollectionResult:
    """Fetch public events and store new ones. Returns collection summary."""
    all_events: list[NormalizedEvent] = []
    changed = False

    for status, items, page in client.paginate(
        f"/users/{username}/events/public",
        max_pages=max_pages,
        cache_getter=storage.get_cache_entry,
        cache_setter=storage.upsert_cache_entry,
    ):
        if status == 304:
            continue
        changed = True
        for rank, raw in enumerate(items, start=1):
            all_events.append(normalize_event(raw, username, source_page=page, source_rank=rank))

    new_events = storage.insert_events(all_events) if all_events else []

    return CollectionResult(
        username=username,
        collector_name="events",
        changed=changed,
        new_count=len(new_events),
        details={"new_events": [_event_summary(e) for e in new_events[:20]]},
    )


def _event_summary(ev: NormalizedEvent) -> dict[str, Any]:
    """Minimal dict for display purposes."""
    return {
        "created_at": ev.created_at,
        "type": ev.event_type,
        "repo": ev.repo_name,
        "action": ev.action,
    }
