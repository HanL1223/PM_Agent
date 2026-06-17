"""
Loads skill files (Markdown knowledge bundles) from `pmagent/skills/`.

A "skill" here is just a folder of Markdown that encodes domain knowledge the
agents need — kept separate from code so it can be edited without touching
Python. Right now there's one: the PRD skill used by the Requirements Agent.
"""

import os

_DIR = os.path.dirname(os.path.abspath(__file__))


def load_skill(name: str, file: str = "SKILL.md") -> str:
    """Return the text of a skill file, e.g. load_skill('prd')."""
    with open(os.path.join(_DIR, name, file), "r",encoding='utf-8') as f:
        return f.read()
