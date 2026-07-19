"""
Shared state for the multi-agent graph, plus the structured-output schemas the
specialist agents produce.

Design note (from the blueprint): agents should exchange *structured data*, not
giant text blobs. So alongside the conversational `messages` channel we keep
typed slots (`ticket_draft`, `sprint_report`) that an agent fills in and the
frontend can render. This is the single most important habit for keeping a
multi-agent system debuggable as it grows.

The state object is shared by every node in the graph. LangGraph merges each
node's returned partial state into this object using the per-field "reducers"
(e.g. `add_messages` appends rather than overwrites the message list).
"""

from typing import Annotated, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TicketDraft(BaseModel):
    """
    List Jira issue produced by the Ticket Agent.

    Agent shows the user for approval *before* anything is
    created in Jira. The field names map directly onto the Jira create payload
    built in `pmagent/tools/jira_tools.py`.

    """

    summary:str = Field(description="Concise issue title, written as an action.")
    issue_type: Literal["Story", "Bug", "Task", "Spike"] = Field(
        default="Task", description="Jira issue type."
    )
    description: str = Field(
        description="Body in the team's house style: business context, scope, "
        "and any technical notes."
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Testable, unambiguous acceptance criteria.",
    )
    story_points: int | None = Field(
        default=None, description="Rough estimate of effort or null."
    )
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)




# Requirements Agent schemas (Writer produces a PRD; Reviewer critiques it)
# These mirror the sections of the Atlassian "Product requirements" template.
# The Writer fills them in (LLM); a deterministic renderer turns them into the
# template markdown (no LLM). The Reviewer reasons over this *structure* to
# check that every source requirement was captured.

#REQUIRE BUSINESS PO REVIEW
class SuccessMetric(BaseModel):
    """One row of the PRD 'Success metrics' table."""
    goal: str = Field(description="The business goal, e.g. 'Reduce reporting errors'.")
    metric: str = Field(description="How it's measured, e.g. 'FX variance < 0.1% MoM'.")


class PRDRequirement(BaseModel):
    """One row of the PRD 'Requirements' table."""
    user_story: str = Field(
        description="Written as 'As a <role>, I want <capability>, so that <benefit>'."
    )
    importance: Literal["High", "Medium", "Low"] = "Medium"
    notes: str = ""
    jira_issue: str = Field(default="", description="Optional Jira key/link if one exists.")

class OpenQuestion(BaseModel):
    """One row of the Atlassian PRD 'Open questions' table."""
    question: str
    answer: str = ""

class PRD(BaseModel):
    """A structured Product Requirements Document (Atlassian template shape).

    The Writer agent produces this; `render_prd_markdown` formats it into the
    template. Keeping it typed is what lets the Reviewer do precise coverage
    checks instead of eyeballing prose.
    """
    title: str = Field(description="Document title / feature name.")
    objective: str = Field(description="A few sentences on what we're doing and why.")
    target_release: str = ""
    owner: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    background: str = ""
    success_metrics: list[SuccessMetric] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requirements: list[PRDRequirement] = Field(default_factory=list)
    user_interaction_design: str = Field(
        default="", description="Notes or links to UX/design, if any."
    )
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    out_of_scope: list[str] = Field(
        default_factory=list, description="Explicitly what we are NOT doing."
    )


class ReviewResult(BaseModel):
    """The Reviewer agent's verdict on a PRD draft.

    `missing_requirements` is the heart of "ensure all requirements are
    captured": the Reviewer lists any requirement implied by the source notes
    that the draft failed to reflect. While that list is non-empty (and we have
    iterations left), the draft goes back to the Writer.
    """
    approved: bool = Field(description="True only if the PRD captures everything and is well-formed.")
    missing_requirements: list[str] = Field(
        default_factory=list,
        description="Requirements present in the source notes but absent/garbled in the PRD.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Quality problems: ambiguity, untestable metrics, vague stories.",
    )
    revision_notes: str = Field(
        default="", description="Concrete, actionable guidance for the Writer's next pass."
    )


class DiagramNode(BaseModel):
    """One node in a `DiagramBrief`."""
    id: str
    label: str


class DiagramEdge(BaseModel):
    """One edge in a `DiagramBrief`."""
    from_: str = Field(alias="from")
    to: str
    label: str = ""

    model_config = {"populate_by_name": True}


class DiagramBrief(BaseModel):
    """Structured output of the Diagram Agent's `draft_diagram_brief` tool.

    Mirrors `snowflake/procs/draft_diagram_brief.py`'s JSON shape exactly, so
    the two tracks stay interchangeable even though this one gets its
    structure via `with_structured_output` instead of prompted JSON + manual
    parsing (Cortex there has no structured-output binding; LangChain here
    does, so we use it — see CLAUDE.md's "LLM does judgment, plain Python
    does arithmetic/formatting" principle: `render_diagram_brief` is the
    deterministic formatting step, this model is just the judgment).
    """
    diagram_type: Literal["flowchart", "entity_relationship", "architecture", "sequence"]
    title: str
    description: str = Field(
        description="Plain-English description of every node and how they "
        "connect, precise enough to hand to a diagramming tool with no other "
        "context."
    )
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)


class RouteDecision(BaseModel):
    """The Orchestrator's intent-classification output.

    We use the model's *structured output* feature to force the routing decision
    into one of three known buckets, instead of parsing free text. This is the
    deterministic 'router' the blueprint recommends keeping separate from the
    work itself.
    """
    route: Literal["ticket", "sprint", "query", "requirements", "spreadsheet"] = Field(
        description=(
            "ticket = user wants to create/draft a Jira issue; "
            "sprint = user asks about sprint health/progress/blockers/standup; "
            "requirements = user provides requirements/meeting notes and wants a "
            "PRD / requirements document written; "
            "spreadsheet = user wants to propose or apply an approved update to a "
            "project-control spreadsheet; "
            "query = any other read-only question (search issues, look something up, chit-chat)."
        )
    )



class PMState(BaseModel):
    """State threaded through every node of the graph.

    Attributes:
        messages: The running conversation. `add_messages` is a reducer that
            appends new messages (and reconciles tool calls) rather than
            replacing the list — this is what gives the agent memory within a
            thread.
        route: Set by the orchestrator's classifier; read by the conditional
            edge that dispatches to a specialist lane.
        ticket_draft: Last ticket proposed by the Ticket Agent (for the UI).
        sprint_report: Last computed sprint analysis (for the UI).
        prd: Last PRD produced by the Requirements Agent (structured form).
        diagram_brief: Last brief produced by the Diagram Agent (for the UI,
            and as the `description` handed to Lucid's MCP `create_diagram`
            tool once the user confirms it).
    """
    messages: Annotated[list[BaseMessage], add_messages] = []
    route: str = ""
    ticket_draft: dict = {}
    sprint_report: dict = {}
    prd: dict = {}
    diagram_brief: dict = {}
    spreadsheet_update: dict = {}
