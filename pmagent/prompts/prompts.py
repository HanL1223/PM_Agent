"""
Loads system prompts from the `.md` files next to this module.

Keeping prompts in Markdown for easy
to read, diff, and iterate on without touching code
"""

import os

_DIR = os.path.dirname(os.path.abspath(__file__))

def _load(name:str) -> str:
    with open (os.path.join(_DIR,  name),"r",encoding='utf-8') as f:
        return f.read()
    
orchestrator_system_prompt = _load("orchestrator.md")
ticket_agent_system_prompt = _load("ticket_agent.md")
sprint_agent_system_prompt = _load("sprint_agent.md")
requirements_writer_system_prompt = _load("requirements_writer.md")
requirements_reviewer_system_prompt = _load("requirements_reviewer.md")