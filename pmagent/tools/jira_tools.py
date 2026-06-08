"""
Jira integration: a thin client, the LangChain `@tool`s the agents call, and
the deterministic sprint-metric functions.

Key implementation notes:
- Jira issue search uses Jira Cloud enhanced JQL search: /rest/api/3/search/jql
- Sprint reporting accepts the user-visible Supply Chain Sprint number, then
  resolves it to Jira's hidden internal sprint ID.
- Story points are read from JIRA_STORY_POINTS_FIELD, defaulting to Sigma's
  "Story point estimate" field: customfield_10052.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from langchain_core.tools import tool

from pmagent import env


# ---------------------------------------------------------------------------
# Mock data location
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MOCK_PATH = os.path.join(_PROJECT_ROOT, "sample_data", "mock_jira.json")


def get_story_points_field() -> str:
    """Return the Jira custom field id used for story points.

    For your Jira instance, "Story point estimate" is customfield_10052.
    You can override it in .env:

        JIRA_STORY_POINTS_FIELD=customfield_10052
    """
    return os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10052").strip()


def _jira_read_fields() -> list[str]:
    """Fields we ask Jira to return for read/search/sprint calls."""
    return [
        "summary",
        "status",
        "issuetype",
        "labels",
        "components",
        get_story_points_field(),
        "assignee",
        "created",
        "updated",
    ]


class JiraClient:
    """Owns all Jira I/O.

    In mock mode the dataset is loaded once into memory.
    In real mode we use Jira Cloud REST v3 + Jira Agile API.
    """

    def __init__(self) -> None:
        self.mock = env.JIRA_MOCK
        self._data: dict[str, Any] = {}

        if self.mock:
            with open(_MOCK_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._session = requests.Session()
            self._session.auth = (env.JIRA_EMAIL, env.JIRA_API_TOKEN)
            self._session.headers.update(
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )
            self._base = env.JIRA_BASE_URL.rstrip("/")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def search_issues(self, jql: str, max_results: int = 25) -> list[dict]:
        """Return issues matching a JQL query as simplified flat dicts."""
        if self.mock:
            import re

            issues = self._data["issues"]

            quoted = re.findall(r'"([^"]+)"', jql)
            if quoted:
                terms = [q.lower() for q in quoted]
            else:
                stop_words = {
                    "project",
                    "text",
                    "and",
                    "or",
                    "order",
                    "by",
                    "in",
                    "status",
                    "created",
                    "desc",
                    "asc",
                }
                terms = [
                    t.lower()
                    for t in jql.split()
                    if len(t) > 2 and t.lower() not in stop_words and t.isalpha()
                ]

            if terms:
                filtered = [
                    i
                    for i in issues
                    if any(
                        t in i.get("summary", "").lower()
                        or t in " ".join(i.get("labels", [])).lower()
                        for t in terms
                    )
                ]
                issues = filtered or issues

            return issues[:max_results]

        # Always prefer newest Jira issues first unless the caller already
        # supplied an ORDER BY clause.
        if "ORDER BY" not in jql.upper():
            jql = f"({jql}) ORDER BY created DESC"

        fields = _jira_read_fields()

        resp = self._session.post(
            f"{self._base}/rest/api/3/search/jql",
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": fields,
            },
        )
        resp.raise_for_status()

        return [self._normalise_issue(i) for i in resp.json().get("issues", [])]

    def get_issue(self, key: str) -> dict:
        """Get one Jira issue by issue key and return a simplified flat dict."""
        if self.mock:
            for issue in self._data["issues"]:
                if issue.get("key", "").upper() == key.upper():
                    return issue
            raise ValueError(f"Issue {key} not found in mock data.")

        resp = self._session.get(
            f"{self._base}/rest/api/3/issue/{key}",
            params={"fields": ",".join(_jira_read_fields())},
        )
        resp.raise_for_status()
        return self._normalise_issue(resp.json())

    def get_myself(self) -> dict:
        """Return the Jira account represented by the current API token."""
        if self.mock:
            return {"mock": True}

        resp = self._session.get(f"{self._base}/rest/api/3/myself")
        resp.raise_for_status()
        return resp.json()

    def list_projects(self) -> list[dict]:
        """List projects visible to the current Jira API user."""
        if self.mock:
            project = self._data.get("project", {})
            return [{"key": project.get("key"), "name": project.get("name")}]

        resp = self._session.get(f"{self._base}/rest/api/3/project/search")
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": p.get("id"),
                "key": p.get("key"),
                "name": p.get("name"),
            }
            for p in data.get("values", [])
        ]

    def get_sprint_issues(self, sprint_id: int) -> dict:
        """Return sprint metadata and normalised sprint issues.

        Important:
        Jira Agile returns raw issues. We normalise them here so downstream
        metric functions do not need to know about Jira's nested field shape.
        """
        if self.mock:
            return {
                "sprint": self._data.get("sprint", {"id": sprint_id, "name": "Mock Sprint"}),
                "issues": self._data.get("issues", []),
            }

        # Fetch sprint metadata so compute_sprint_metrics has sprint["name"].
        sprint_resp = self._session.get(
            f"{self._base}/rest/agile/1.0/sprint/{sprint_id}"
        )
        sprint_resp.raise_for_status()
        sprint = sprint_resp.json()

        fields = _jira_read_fields()
        all_raw_issues: list[dict] = []
        start_at = 0
        max_results = 100

        while True:
            resp = self._session.get(
                f"{self._base}/rest/agile/1.0/sprint/{sprint_id}/issue",
                params={
                    "fields": ",".join(fields),
                    "startAt": start_at,
                    "maxResults": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("issues", [])
            all_raw_issues.extend(batch)

            # Jira Agile normally returns total/startAt/maxResults; some
            # responses also include isLast. Handle both shapes.
            if data.get("isLast") is True:
                break

            total = data.get("total")
            if total is not None:
                start_at += len(batch)
                if start_at >= int(total) or not batch:
                    break
            else:
                if len(batch) < max_results:
                    break
                start_at += len(batch)

        return {
            "sprint": sprint,
            "issues": [self._normalise_issue(i) for i in all_raw_issues],
        }

    def list_boards(self, project_key: str | None = None) -> list[dict]:
        """List Agile boards visible to the current Jira API user."""
        if self.mock:
            return [{"id": 1, "name": "Mock Board", "type": "scrum"}]

        params: dict[str, Any] = {"maxResults": 50}
        if project_key:
            params["projectKeyOrId"] = project_key

        resp = self._session.get(
            f"{self._base}/rest/agile/1.0/board",
            params=params,
        )
        resp.raise_for_status()

        data = resp.json()
        return [
            {
                "id": b.get("id"),
                "name": b.get("name"),
                "type": b.get("type"),
            }
            for b in data.get("values", [])
        ]

    def list_board_sprints(
        self,
        board_id: int,
        state: str = "active,future,closed",
    ) -> list[dict]:
        """List sprints for a Jira board."""
        if self.mock:
            sprint = self._data.get("sprint", {})
            return [
                {
                    "id": sprint.get("id", 1),
                    "name": sprint.get("name", "Mock Sprint"),
                    "state": sprint.get("state", "active"),
                    "startDate": sprint.get("startDate"),
                    "endDate": sprint.get("endDate"),
                    "completeDate": sprint.get("completeDate"),
                }
            ]

        all_sprints: list[dict] = []
        start_at = 0
        max_results = 50

        while True:
            resp = self._session.get(
                f"{self._base}/rest/agile/1.0/board/{board_id}/sprint",
                params={
                    "state": state,
                    "startAt": start_at,
                    "maxResults": max_results,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("values", [])
            all_sprints.extend(batch)

            if data.get("isLast") is True:
                break

            total = data.get("total")
            if total is not None:
                start_at += len(batch)
                if start_at >= int(total) or not batch:
                    break
            else:
                if len(batch) < max_results:
                    break
                start_at += len(batch)

        return [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "state": s.get("state"),
                "startDate": s.get("startDate"),
                "endDate": s.get("endDate"),
                "completeDate": s.get("completeDate"),
            }
            for s in all_sprints
        ]

    def find_sprint_id_by_name(
        self,
        project_key: str,
        sprint_name: str,
    ) -> int:
        """Resolve user-visible sprint name to Jira's internal sprint id."""
        boards = self.list_boards(project_key)
        matches: list[dict] = []

        for board in boards:
            board_id = board["id"]
            sprints = self.list_board_sprints(board_id)

            for sprint in sprints:
                name = sprint.get("name") or ""
                if name.lower() == sprint_name.lower():
                    matches.append(
                        {
                            "board_id": board_id,
                            "board_name": board["name"],
                            "sprint_id": sprint["id"],
                            "sprint_name": name,
                            "state": sprint.get("state"),
                        }
                    )

        if not matches:
            available = []
            for board in boards:
                for sprint in self.list_board_sprints(board["id"]):
                    available.append(sprint.get("name", ""))

            sample = ", ".join([s for s in available if s][:10])
            raise ValueError(
                f"No sprint found with name '{sprint_name}' in project {project_key}. "
                f"Sample visible sprints: {sample}"
            )

        # Prefer active sprint, then future, then closed.
        state_priority = {"active": 0, "future": 1, "closed": 2}
        matches.sort(key=lambda x: state_priority.get(x.get("state"), 99))

        return int(matches[0]["sprint_id"])

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def create_issue(self, draft: dict) -> dict:
        """Create a Jira issue from a TicketDraft-shaped dict."""
        if self.mock:
            project_key = self._data["project"]["key"]
            new_num = 900 + sum(
                1 for i in self._data["issues"] if i["key"].startswith(project_key)
            )
            new_key = f"{project_key}-{new_num}"
            issue = {
                "key": new_key,
                "summary": draft["summary"],
                "type": draft.get("issue_type", "Task"),
                "status": "To Do",
                "story_points": draft.get("story_points"),
                "assignee": None,
                "blocked": False,
                "flagged": False,
                "days_in_status": 0,
                "labels": draft.get("labels", []),
                "components": draft.get("components", []),
            }
            self._data["issues"].append(issue)
            return {"key": new_key, "mock": True}

        fields: dict[str, Any] = {
            "project": {"key": draft["project_key"]},
            "summary": draft["summary"],
            "issuetype": {"name": draft.get("issue_type", "Task")},
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": draft["description"]}],
                    }
                ],
            },
            "labels": draft.get("labels", []),
        }

        # Only set story points if the user supplied them.
        if draft.get("story_points") is not None:
            fields[get_story_points_field()] = draft["story_points"]

        if draft.get("components"):
            fields["components"] = [{"name": c} for c in draft["components"]]

        payload = {"fields": fields}

        resp = self._session.post(f"{self._base}/rest/api/3/issue", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_issue(raw: dict) -> dict:
        """Flatten a raw Jira REST issue into the simple shape our code uses."""
        f = raw.get("fields", {})
        status = (f.get("status") or {}).get("name", "")

        story_points_raw = f.get(get_story_points_field())
        try:
            story_points = float(story_points_raw) if story_points_raw is not None else None
        except (TypeError, ValueError):
            story_points = None

        return {
            "key": raw.get("key", ""),
            "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "status": status,
            "story_points": story_points,
            "assignee": ((f.get("assignee") or {}) or {}).get("displayName"),
            "created": f.get("created"),
            "updated": f.get("updated"),
            "blocked": status.lower() in ("blocked", "impediment"),
            "flagged": bool(f.get("customfield_10021")),
            "days_in_status": 0,  # would require changelog expansion to compute
            "labels": f.get("labels", []),
            "components": [c.get("name") for c in f.get("components", [])],
        }


# Single shared instance, created at import time.
client = JiraClient()



# Deterministic analytics

def compute_sprint_metrics(sprint_payload: dict, stuck_threshold_days: int = 3) -> dict:
    """Compute sprint-health metrics from normalised issue data."""
    issues = sprint_payload.get("issues", [])
    sprint = sprint_payload.get("sprint", {})

    def is_done(issue: dict) -> bool:
        return issue.get("status", "").lower() in {"done", "closed", "resolved"}

    total_points = sum(issue.get("story_points") or 0 for issue in issues)
    done_points = sum(issue.get("story_points") or 0 for issue in issues if is_done(issue))

    blocked = [
        issue
        for issue in issues
        if (
            issue.get("blocked")
            or issue.get("flagged")
            or (
                not is_done(issue)
                and issue.get("days_in_status", 0) >= stuck_threshold_days
            )
        )
    ]

    completion_rate = round(done_points / total_points, 3) if total_points else 0.0

    if completion_rate >= 0.7 and len(blocked) <= 1:
        risk = "LOW"
    elif completion_rate >= 0.4:
        risk = "MODERATE"
    else:
        risk = "HIGH"

    return {
        "sprint_name": sprint.get("name", "Active Sprint"),
        "total_points": total_points,
        "done_points": done_points,
        "completion_rate": completion_rate,
        "issue_count": len(issues),
        "done_count": sum(1 for issue in issues if is_done(issue)),
        "blocked_tickets": [
            {
                "key": issue["key"],
                "summary": issue["summary"],
                "status": issue["status"],
                "story_points": issue.get("story_points") or 0,
                "days_in_status": issue.get("days_in_status", 0),
            }
            for issue in blocked
        ],
        "risk_level": risk,
    }



# LangChain tools

@tool
def search_jira_issues(jql: str) -> str:
    """Search Jira for existing issues.

    Use this to find duplicates, gather context for a new ticket, or answer a
    user's read-only question.

    Args:
        jql: A Jira Query Language string, e.g.
             'project = CSCI AND text ~ "DFIO"'.
    """
    issues = client.search_issues(jql)
    if not issues:
        return "No matching issues found."

    return "\n".join(
        f"- {i['key']} [{i['type']}/{i['status']}] "
        f"{i.get('created', '')[:10]} "
        f"({i.get('story_points') or 0} pts) — {i['summary']}"
        for i in issues
    )


@tool
def get_sprint_status(sprint_id: int) -> str:
    """Get sprint progress by Jira internal sprint ID.

    Important:
    This uses Jira's hidden internal sprint ID, not the visible sprint number.
    Do NOT use this when the user says "Sprint 26" or "Supply Chain Sprint 26".

    For visible Supply Chain sprint numbers, use get_supply_chain_sprint_status.
    """
    payload = client.get_sprint_issues(sprint_id)
    metrics = compute_sprint_metrics(payload)
    return json.dumps(metrics, indent=2)


@tool
def create_jira_issue(
    summary: str,
    description: str,
    issue_type: str = "Task",
    story_points: int | None = None,
    labels: list[str] | None = None,
    components: list[str] | None = None,
) -> str:
    """Create a Jira issue.

    ONLY call this AFTER the user has explicitly confirmed the drafted ticket.
    Never call it to preview.
    """
    draft = {
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "story_points": story_points,
        "labels": labels or [],
        "components": components or [],
        "project_key": (
            client._data.get("project", {}).get("key")
            if client.mock
            else os.getenv("JIRA_PROJECT_KEY", "CSCI")
        ),
    }
    result = client.create_issue(draft)
    suffix = " (mock — not sent to a real Jira)" if result.get("mock") else ""
    return f"Created issue {result['key']}{suffix}."


@tool
def get_supply_chain_sprint_status(sprint_number: int) -> str:
    """Get story point progress for a Supply Chain sprint.

    Use this when the user asks about a sprint by human-readable number, e.g.:
    - "sprint 26"
    - "Supply Chain Sprint 26"
    - "progress in sprint 26 by story point"

    Jira sprint IDs are internal and not visible to users.
    This tool resolves "Supply Chain Sprint xx" to the internal sprint ID first.
    """
    local_client = JiraClient()

    project_key = os.getenv("JIRA_PROJECT_KEY", "CSCI")
    sprint_name = f"Supply Chain Sprint {sprint_number}"

    sprint_id = local_client.find_sprint_id_by_name(
        project_key=project_key,
        sprint_name=sprint_name,
    )

    payload = local_client.get_sprint_issues(sprint_id)
    issues = payload.get("issues", [])

    total_points = 0.0
    done_points = 0.0
    in_progress_points = 0.0
    todo_points = 0.0
    blocked_points = 0.0

    rows = []

    for issue in issues:
        points = issue.get("story_points") or 0
        status = issue.get("status", "")
        status_lower = status.lower()

        total_points += points

        if status_lower in ("done", "closed", "resolved"):
            done_points += points
        elif status_lower in ("blocked", "impediment"):
            blocked_points += points
        elif status_lower in {"in progress", "in review", "qa", "testing"}:
            in_progress_points += points
        else:
            todo_points += points

        rows.append(
            f"- {issue['key']} [{status}] {points:g} pts — {issue['summary']}"
        )

    progress_pct = round((done_points / total_points) * 100, 1) if total_points else 0

    summary = [
        f"Supply Chain Sprint {sprint_number}",
        f"Jira internal sprint ID: {sprint_id}",
        "",
        f"Total story points: {total_points:g}",
        f"Done story points: {done_points:g}",
        f"In-progress story points: {in_progress_points:g}",
        f"Blocked story points: {blocked_points:g}",
        f"To-do / other story points: {todo_points:g}",
        f"Completion by story points: {progress_pct}%",
        "",
        "Issues:",
        *rows,
    ]

    return "\n".join(summary)
