"""Reusable prompt templates (carried over from the upstream project)."""

from mcp.server.mcpserver import AssistantMessage, MCPServer, Message, UserMessage


def register_prompts(mcp: MCPServer, *, include_write: bool) -> None:
    """Register prompts; prompts that ask for changes are skipped in read-only mode."""

    @mcp.prompt()
    def weekly_time_report() -> str:
        """Ask for a weekly time report."""
        return (
            "Please generate a weekly time report showing my time entries, total hours "
            "worked, and project breakdown for this week using my Toggl Track data."
        )

    @mcp.prompt()
    def project_time_analysis(project_name: str) -> list[Message]:
        """Start a conversation analysing the time spent on one project."""
        return [
            UserMessage(f"I need to analyze my time tracking for the project '{project_name}'."),
            AssistantMessage(
                "I'll help you analyze your time tracking data. Let me first get your "
                "projects and recent time entries for this project."
            ),
            UserMessage(
                "Please show me the total hours, daily breakdown, and any patterns in my "
                "work schedule for this project."
            ),
        ]

    @mcp.prompt()
    def optimize_workflow() -> str:
        """Ask for workflow suggestions based on tracked time."""
        return (
            "Based on my Toggl Track time tracking data, please analyze my work patterns "
            "and suggest ways to optimize my workflow and improve productivity."
        )

    @mcp.prompt()
    def project_overview() -> str:
        """Ask for an overview of all projects and workspaces."""
        return (
            "Please show me all my Toggl Track projects and workspaces, organized in a "
            "clear format with project details and current status."
        )

    @mcp.prompt()
    def detailed_time_report(start_date: str, end_date: str = "", project_name: str = "") -> str:
        """Ask for detailed time entries in a date range."""
        prompt = f"Please show me detailed time entries from {start_date}"
        if end_date:
            prompt += f" to {end_date}"
        if project_name:
            prompt += f" for project '{project_name}'"
        return prompt + ". Include descriptions, durations, and daily breakdowns."

    @mcp.prompt()
    def time_summary_report(days: str = "7", project_name: str = "") -> str:
        """Ask for a time summary with project totals."""
        prompt = f"Please give me a time summary for the last {days} days"
        if project_name:
            prompt += f" for project '{project_name}'"
        return prompt + ". Show total hours by project with percentages."

    @mcp.prompt()
    def productivity_analysis(period: str = "week") -> list[Message]:
        """Start a conversation analysing productivity over a period."""
        return [
            UserMessage(f"I want to analyze my productivity for this {period}."),
            AssistantMessage(
                "I'll help you analyze your productivity patterns. Let me get your time "
                "tracking data and current timer status."
            ),
            UserMessage(
                "Please show me my time distribution, most productive periods, and suggest "
                "improvements."
            ),
        ]

    @mcp.prompt()
    def current_status_check() -> str:
        """Ask for the running timer and today's entries."""
        return "Please check my current timer status and show me what I've been working on today."

    @mcp.prompt()
    def project_deep_dive(project_name: str, days: str = "30") -> str:
        """Ask for an in-depth analysis of one project."""
        return (
            f"Please provide a detailed analysis of my work on '{project_name}' over the last "
            f"{days} days. Include time patterns, task descriptions, and productivity insights."
        )

    @mcp.prompt()
    def search_by_description(query: str, days: str = "30") -> str:
        """Ask to search time entries by description."""
        return (
            f"Please search my time entries for '{query}' over the last {days} days and show "
            "me the total time spent on related activities."
        )

    @mcp.prompt()
    def view_project_tasks(project_name: str) -> str:
        """Ask for the tasks of one project."""
        return (
            f"Please show me all tasks for the project '{project_name}' with their current "
            "status and estimated time."
        )

    @mcp.prompt()
    def project_task_overview() -> str:
        """Ask for an overview of tasks across projects."""
        return (
            "Please give me an overview of tasks across all my projects. Show me which "
            "projects have tasks, what needs attention, and help me prioritize my work."
        )

    @mcp.prompt()
    def list_all_tasks() -> str:
        """Ask for all tasks across all projects."""
        return (
            "Please show me all tasks across all my projects, organized by project. Include "
            "task status and estimated time for each task."
        )

    if not include_write:
        return

    @mcp.prompt()
    def start_time_tracking(project_name: str, description: str = "") -> str:
        """Ask to start tracking time for a project."""
        prompt = f"I want to start tracking time for the project '{project_name}'"
        if description:
            prompt += f" with the description '{description}'"
        return prompt + ". Please start a new time entry in my Toggl Track account."

    @mcp.prompt()
    def quick_start_timer(description: str, project_name: str = "") -> str:
        """Ask to start a timer."""
        prompt = f"Please start a timer with description '{description}'"
        if project_name:
            prompt += f" for project '{project_name}'"
        return prompt + ". Confirm when the timer has started."

    @mcp.prompt()
    def stop_and_start_new(new_description: str, project_name: str = "") -> str:
        """Ask to stop the running timer and start a new one."""
        prompt = (
            f"Please stop my current timer and start a new one with description '{new_description}'"
        )
        if project_name:
            prompt += f" for project '{project_name}'"
        return prompt + (
            ". Show me the duration of the stopped timer and confirm the new timer has started."
        )

    @mcp.prompt()
    def timer_status_and_control() -> str:
        """Ask for the timer status and offer to start or stop it."""
        return (
            "Please check my current timer status. If I have a timer running, show me the "
            "details and ask if I want to stop it. If no timer is running, ask if I want to "
            "start one."
        )

    @mcp.prompt()
    def work_session_timer(project_name: str, duration: str = "1 hour") -> str:
        """Ask to start a focused work session."""
        return (
            f"I want to start a focused {duration} work session on '{project_name}'. Please "
            "start a timer and remind me to take breaks."
        )

    @mcp.prompt()
    def create_new_task(project_name: str, task_name: str, estimated_hours: str = "") -> str:
        """Ask to create a task in a project."""
        prompt = f"Please create a new task called '{task_name}' for project '{project_name}'"
        if estimated_hours:
            prompt += f" with an estimated time of {estimated_hours} hours"
        return prompt + ". Confirm when the task has been created."

    @mcp.prompt()
    def task_planning_session(project_name: str) -> str:
        """Ask for help planning tasks for a project."""
        return (
            f"I want to plan out tasks for the project '{project_name}'. Please show me "
            "existing tasks and help me create new ones based on the project requirements."
        )
