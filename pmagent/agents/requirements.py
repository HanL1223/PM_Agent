"""
Requirements Agent — a Writer ↔ Reviewer reflection loop.

This is a self-contained LangGraph *subgraph* that takes raw input (a
requirement or meeting notes) and produces a finished PRD in the Atlassian
template. It is plugged into the main graph as one node (`requirements_node`).

The loop (actor-critic / reflection pattern):

        START → writer → reviewer ──approved?──► render → END
                  ▲                  │ no (and iterations left)
                  └──────────────────┘

Why this is genuinely an *agent*, not a script: the Reviewer's judgement at
runtime decides whether the Writer runs again. The number of passes, and what
the Writer fixes each time, depend on the content — none of it is hard-coded.
That runtime-decided control flow is the whole point.

Split of responsibilities (the recurring principle):
  - Writer / Reviewer  → LLM (writing and judging are language tasks).
  - render_prd_markdown → plain Python (formatting must be exact, never guessed).
"""
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel

from pmagent.llm import get_llm
from pmagent.prompts import prompts
from pmagent.skills import load_skill
from pmagent.state import PMState, PRD, ReviewResult
from pmagent.tools.company_knowledge import retrieve_company_context


# The PRD skill text is injected into both prompts so the writing principles and
# the review checklist come from one source. Loaded once at import.
_PRD_SKILL = load_skill("prd")
_WRITER_PROMPT = prompts.requirements_writer_system_prompt.format(skill=_PRD_SKILL)
_REVIEWER_PROMPT = prompts.requirements_reviewer_system_prompt.format(skill=_PRD_SKILL)


# Subgraph state (private to this workflow — kept out of the parent PMState)
class RequirementsState(BaseModel):
    