"""Tool implementations.

Each function returns plain text for the model. Expected failures raise a
`UserFacingError` subclass, which the MCP layer turns into a tool error result.
All text that originates from Toggl is passed through `clean()`.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .client import CREATED_WITH
from .errors import InvalidArgument, LookupFailed, TogglError
from .service import Account, TogglService
from .text import clean
from .timeutil import (
    format_duration,
    format_rfc3339_utc,
    local_day_bounds,
    parse_api_datetime,
    resolve_date_range,
)

# GET /me/time_entries returns at most this many entries per request.
MAX_ENTRIES_PER_REQUEST = 1000

DEFAULT_LIST_DAYS = 7
DEFAULT_SEARCH_DAYS = 30


@dataclass(frozen=True)
class EntryView:
    start: datetime  # in the account timezone
    stop: datetime | None
    seconds: float
    running: bool
    project_id: int | None
    workspace_id: int | None
    project: str
    description: str


def _entry_view(entry: dict[str, Any], account: Account, now: datetime) -> EntryView | None:
    start = parse_api_datetime(entry.get("start"))
    if start is None:
        return None
    duration = entry.get("duration")
    is_number = isinstance(duration, int | float) and not isinstance(duration, bool)
    if is_number and duration < 0:
        running, seconds, stop = True, max((now - start).total_seconds(), 0.0), None
    else:
        running = False
        seconds = float(duration) if is_number else 0.0
        stop = parse_api_datetime(entry.get("stop")) or start + timedelta(seconds=seconds)
    project_id = entry.get("project_id")
    project_id = project_id if isinstance(project_id, int) else None
    project = account.projects.get(project_id) if project_id is not None else None
    workspace_id = (project or {}).get("workspace_id") or entry.get("workspace_id")
    tz = account.timezone
    return EntryView(
        start=start.astimezone(tz),
        stop=stop.astimezone(tz) if stop else None,
        seconds=seconds,
        running=running,
        project_id=project_id,
        workspace_id=workspace_id if isinstance(workspace_id, int) else None,
        project=account.project_label(project_id, entry.get("project_name")),
        description=clean(entry.get("description"), default="(no description)"),
    )


def _entry_line(view: EntryView) -> str:
    if view.running:
        until, duration = "now", f"{format_duration(view.seconds)} (running)"
    else:
        until = view.stop.strftime("%H:%M") if view.stop else "?"
        duration = format_duration(view.seconds)
    return f"  - {view.start:%H:%M}-{until} | {view.project} | {view.description} | {duration}"


def _total_line(label: str, views: list[EntryView]) -> str:
    total = format_duration(sum(v.seconds for v in views))
    running = " (includes the running timer)" if any(v.running for v in views) else ""
    return f"{label}: {total}{running}"


def _render_by_day(views: list[EntryView]) -> list[str]:
    by_day: dict[date, list[EntryView]] = defaultdict(list)
    for view in views:
        by_day[view.start.date()].append(view)
    lines: list[str] = []
    for day in sorted(by_day):
        lines.append("")
        lines.append(f"{day:%Y-%m-%d (%a)}")
        lines.extend(_entry_line(v) for v in by_day[day])
        lines.append("  " + _total_line("Day total", by_day[day]))
    return lines


def _truncation_note(raw_count: int) -> list[str]:
    if raw_count < MAX_ENTRIES_PER_REQUEST:
        return []
    return [
        "",
        f"Note: Toggl returned {raw_count} entries, its per-request maximum, so this list "
        "may be incomplete. Use a shorter date range.",
    ]


async def _entries_in_range(
    service: TogglService, account: Account, start_date: str, end_date: str, default_days: int
) -> tuple[date, date, list[EntryView], int]:
    start_day, end_day = resolve_date_range(
        start_date, end_date, default_days=default_days, today=service.today(account)
    )
    lower, upper = local_day_bounds(start_day, end_day, account.timezone)
    raw = await service.time_entries(lower, upper)
    now = service.now()
    views = [v for entry in raw if (v := _entry_view(entry, account, now)) is not None]
    views = [v for v in views if start_day <= v.start.date() <= end_day]
    views.sort(key=lambda v: v.start)
    return start_day, end_day, views, len(raw)


def _range_text(start_day: date, end_day: date) -> str:
    if start_day == end_day:
        return f"on {start_day.isoformat()}"
    return f"from {start_day.isoformat()} to {end_day.isoformat()}"


# -- read-only tools ------------------------------------------------------------------


async def get_workspaces(service: TogglService) -> str:
    account = await service.account()
    if not account.workspaces:
        return "No workspaces found in your Toggl Track account."
    lines = ["Your Toggl Track workspaces:"]
    for ws_id, ws in sorted(account.workspaces.items(), key=lambda kv: str(kv[1].get("name"))):
        default = " - default" if ws_id == account.default_workspace_id else ""
        lines.append(f"- {clean(ws.get('name'), default='(unnamed)')} (ID {ws_id}){default}")
    return "\n".join(lines)


async def get_projects(service: TogglService, workspace_name: str = "") -> str:
    account = await service.account()
    projects = list(account.projects.values())
    if workspace_name.strip():
        ws = account.find_workspace(workspace_name)
        projects = [p for p in projects if p.get("workspace_id") == ws["id"]]
    if not projects:
        return "No projects found."
    by_ws: dict[object, list[dict[str, Any]]] = defaultdict(list)
    for project in projects:
        by_ws[project.get("workspace_id")].append(project)
    lines = ["Your Toggl Track projects:"]
    for ws_id in sorted(by_ws, key=lambda w: account.workspace_label(w)):
        lines.append("")
        lines.append(f"{account.workspace_label(ws_id)} (workspace ID {ws_id}):")
        for project in sorted(by_ws[ws_id], key=lambda p: str(p.get("name"))):
            details = []
            client_name = account.client_label(project)
            if client_name:
                details.append(f"client {client_name}")
            if project.get("active") is False:
                details.append("archived")
            if project.get("is_private"):
                details.append("private")
            suffix = f" - {', '.join(details)}" if details else ""
            name = clean(project.get("name"), default="(unnamed)")
            lines.append(f"- {name} (project ID {project.get('id')}){suffix}")
    return "\n".join(lines)


async def get_time_entries(
    service: TogglService,
    start_date: str = "",
    end_date: str = "",
    project_name: str = "",
    workspace_name: str = "",
) -> str:
    account = await service.account()
    project = account.find_project(project_name, workspace_name) if project_name.strip() else None
    start_day, end_day, views, raw_count = await _entries_in_range(
        service, account, start_date, end_date, DEFAULT_LIST_DAYS
    )
    scope = ""
    if project is not None:
        views = [v for v in views if v.project_id == project["id"]]
        scope = f" for project {account.project_label(project['id'])!r}"
    if not views:
        return f"No time entries {_range_text(start_day, end_day)}{scope}."
    lines = [
        f"Time entries {_range_text(start_day, end_day)}{scope} (times in {account.timezone_name}):"
    ]
    lines.extend(_render_by_day(views))
    lines.append("")
    lines.append(_total_line("Total", views))
    lines.extend(_truncation_note(raw_count))
    return "\n".join(lines)


async def get_time_summary(
    service: TogglService,
    start_date: str = "",
    end_date: str = "",
    project_name: str = "",
    workspace_name: str = "",
) -> str:
    account = await service.account()
    project = account.find_project(project_name, workspace_name) if project_name.strip() else None
    start_day, end_day, views, raw_count = await _entries_in_range(
        service, account, start_date, end_date, DEFAULT_LIST_DAYS
    )
    scope = ""
    if project is not None:
        views = [v for v in views if v.project_id == project["id"]]
        scope = f" for project {account.project_label(project['id'])!r}"
    if not views:
        return f"No time entries {_range_text(start_day, end_day)}{scope}."

    totals: dict[int | None, float] = defaultdict(float)
    labels: dict[int | None, str] = {}
    workspaces: dict[int | None, int | None] = {}
    for view in views:
        totals[view.project_id] += view.seconds
        labels[view.project_id] = view.project
        workspaces[view.project_id] = view.workspace_id
    label_counts: dict[str, int] = defaultdict(int)
    for label in labels.values():
        label_counts[label] += 1
    for pid, label in labels.items():
        if pid is not None and label_counts[label] > 1:
            labels[pid] = f"{label} ({account.workspace_label(workspaces[pid])})"

    grand_total = sum(totals.values())
    lines = [f"Time summary {_range_text(start_day, end_day)}{scope}:"]
    for pid, seconds in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        share = seconds / grand_total * 100 if grand_total > 0 else 0.0
        lines.append(f"- {labels[pid]}: {format_duration(seconds)} ({share:.1f}%)")
    lines.append("")
    lines.append(_total_line("Total", views))
    lines.extend(_truncation_note(raw_count))
    return "\n".join(lines)


async def search_time_entries(
    service: TogglService, query: str, start_date: str = "", end_date: str = ""
) -> str:
    needle = " ".join(query.split()).casefold()
    if not needle:
        raise InvalidArgument("query must not be empty.")
    account = await service.account()
    start_day, end_day, views, raw_count = await _entries_in_range(
        service, account, start_date, end_date, DEFAULT_SEARCH_DAYS
    )
    matches = [v for v in views if needle in v.description.casefold()]
    shown_query = clean(query, max_length=100)
    if not matches:
        return f"No time entries matching {shown_query!r} {_range_text(start_day, end_day)}."
    lines = [
        f"Time entries matching {shown_query!r} {_range_text(start_day, end_day)} "
        f"(times in {account.timezone_name}):"
    ]
    lines.extend(_render_by_day(matches))
    lines.append("")
    lines.append(_total_line(f"Total for {shown_query!r}", matches))
    lines.extend(_truncation_note(raw_count))
    return "\n".join(lines)


async def get_current_timer(service: TogglService) -> str:
    entry = await service.client.get_current_time_entry()
    if not entry:
        return "No timer is currently running."
    account = await service.account()
    view = _entry_view(entry, account, service.now())
    if view is None:
        raise TogglError("Toggl returned a running time entry without a start time.")
    return "\n".join(
        [
            "Currently running timer:",
            f"- Description: {view.description}",
            f"- Project: {view.project}",
            f"- Workspace: {account.workspace_label(entry.get('workspace_id'))}",
            f"- Started: {view.start:%Y-%m-%d %H:%M} ({account.timezone_name})",
            f"- Elapsed: {format_duration(view.seconds)}",
            f"- Time entry ID: {entry.get('id')}",
        ]
    )


async def get_project_tasks(
    service: TogglService, project_name: str, workspace_name: str = ""
) -> str:
    account = await service.account()
    project = account.find_project(project_name, workspace_name)
    workspace_id = project.get("workspace_id")
    if not isinstance(workspace_id, int):
        raise LookupFailed("Could not determine the workspace of that project.")
    tasks = await service.client.list_project_tasks(workspace_id, project["id"])
    label = account.project_label(project["id"])
    if not tasks:
        return f"No tasks in project {label!r}."
    lines = [f"Tasks in project {label!r} ({account.workspace_label(workspace_id)}):"]
    lines.extend(_task_line(task) for task in sorted(tasks, key=lambda t: str(t.get("name"))))
    return "\n".join(lines)


def _task_line(task: dict[str, Any], indent: str = "") -> str:
    details = ["active" if task.get("active", True) else "done"]
    estimate = task.get("estimated_seconds")
    if isinstance(estimate, int | float) and estimate > 0:
        details.append(f"estimate {format_duration(estimate)}")
    tracked = task.get("tracked_seconds")
    if isinstance(tracked, int | float) and tracked > 0:
        details.append(f"tracked {format_duration(tracked)}")
    name = clean(task.get("name"), default="(unnamed)")
    return f"{indent}- {name} (task ID {task.get('id')}) - {', '.join(details)}"


async def get_all_tasks(service: TogglService) -> str:
    tasks = await service.client.list_my_tasks()
    if not tasks:
        return "No tasks found in any of your projects."
    account = await service.account()
    by_project: dict[object, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        by_project[task.get("project_id")].append(task)

    def heading(pid: object) -> str:
        first = by_project[pid][0]
        project = account.project_label(pid, first.get("project_name"))
        workspace = account.workspace_label(first.get("workspace_id"))
        return f"{project} ({workspace})"

    lines = [f"Tasks across your projects ({len(tasks)} total):"]
    for pid in sorted(by_project, key=heading):
        lines.append("")
        lines.append(heading(pid) + ":")
        group = sorted(by_project[pid], key=lambda t: str(t.get("name")))
        lines.extend(_task_line(task, indent="  ") for task in group)
    return "\n".join(lines)


async def get_api_quota(service: TogglService) -> str:
    items = await service.client.get_quota()
    if not items:
        return "Toggl did not report any API quota."
    account = service.cached_account()
    org_names: dict[int, list[str]] = defaultdict(list)
    if account is not None:
        for ws in account.workspaces.values():
            if isinstance(ws.get("organization_id"), int):
                org_names[ws["organization_id"]].append(clean(ws.get("name")))
    lines = ["Toggl API quota (sliding one-hour window):"]
    for item in items:
        org = item.get("organization_id")
        if org is None:
            label = "Personal requests (/me endpoints)"
        else:
            names = ", ".join(org_names.get(org, []))
            label = f"Organization {org}" + (f" ({names})" if names else "")
        remaining = item.get("remaining")
        resets = item.get("resets_in_secs")
        resets_text = f", resets in {format_duration(resets)}" if isinstance(resets, int) else ""
        lines.append(f"- {label}: {remaining} requests remaining{resets_text}")
    return "\n".join(lines)


# -- tools that change data -----------------------------------------------------------


async def start_timer(
    service: TogglService, description: str, project_name: str = "", workspace_name: str = ""
) -> str:
    account = await service.account()
    project = None
    if project_name.strip():
        project = account.find_project(project_name, workspace_name)
        workspace_id = project.get("workspace_id")
        if not isinstance(workspace_id, int):
            raise LookupFailed("Could not determine the workspace of that project.")
    else:
        workspace_id = account.pick_workspace_for_new_entry(workspace_name)

    body: dict[str, Any] = {
        "created_with": CREATED_WITH,
        "description": description.strip(),
        "workspace_id": workspace_id,
        "start": format_rfc3339_utc(service.now()),
        "duration": -1,
        "billable": False,
        "tags": [],
    }
    if project is not None:
        body["project_id"] = project["id"]
    entry = await service.client.create_time_entry(workspace_id, body)
    service.invalidate_time_entries()

    view = _entry_view(entry, account, service.now())
    started = f"{view.start:%Y-%m-%d %H:%M} ({account.timezone_name})" if view else "now"
    return "\n".join(
        [
            "Timer started:",
            f"- Description: {clean(description, default='(no description)')}",
            f"- Project: {account.project_label(project['id']) if project else 'No project'}",
            f"- Workspace: {account.workspace_label(workspace_id)}",
            f"- Started: {started}",
            f"- Time entry ID: {entry.get('id')}",
        ]
    )


async def stop_current_timer(service: TogglService) -> str:
    entry = await service.client.get_current_time_entry()
    if not entry:
        return "No timer is currently running."
    workspace_id, entry_id = entry.get("workspace_id"), entry.get("id")
    if not isinstance(workspace_id, int) or not isinstance(entry_id, int):
        raise TogglError("Toggl returned a running time entry without workspace or entry ID.")
    stopped = await service.client.stop_time_entry(workspace_id, entry_id)
    service.invalidate_time_entries()
    account = await service.account()
    view = _entry_view(stopped, account, service.now())
    if view is None:
        return f"Timer stopped (time entry ID {entry_id})."
    stopped_at = f"{view.stop:%Y-%m-%d %H:%M}" if view.stop else "unknown"
    return "\n".join(
        [
            "Timer stopped:",
            f"- Description: {view.description}",
            f"- Project: {view.project}",
            f"- Started: {view.start:%Y-%m-%d %H:%M}",
            f"- Stopped: {stopped_at} ({account.timezone_name})",
            f"- Duration: {format_duration(view.seconds)}",
        ]
    )


async def create_project_task(
    service: TogglService,
    project_name: str,
    task_name: str,
    estimated_hours: float = 0.0,
    workspace_name: str = "",
) -> str:
    name = task_name.strip()
    if not name:
        raise InvalidArgument("task_name must not be empty.")
    if not math.isfinite(estimated_hours) or estimated_hours < 0:
        raise InvalidArgument("estimated_hours must be a non-negative number.")
    estimated_seconds = round(estimated_hours * 3600) or None

    account = await service.account()
    project = account.find_project(project_name, workspace_name)
    workspace_id = project.get("workspace_id")
    if not isinstance(workspace_id, int):
        raise LookupFailed("Could not determine the workspace of that project.")
    task = await service.client.create_task(workspace_id, project["id"], name, estimated_seconds)
    lines = [
        "Task created:",
        f"- Name: {clean(task.get('name') or name)}",
        f"- Project: {account.project_label(project['id'])}",
        f"- Workspace: {account.workspace_label(workspace_id)}",
        f"- Task ID: {task.get('id')}",
    ]
    if estimated_seconds:
        lines.append(f"- Estimate: {format_duration(estimated_seconds)}")
    return "\n".join(lines)
