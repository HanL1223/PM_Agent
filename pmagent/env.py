""" 
Central location for reading environment variables

load the `.env` file once, expose
the values as module-level constants, and validate that the *minimum* required
variables are present. Every other module imports from here instead of calling
`os.getenv` all over the codebase, so there is a single source of truth.

"""

from dotenv import load_dotenv
import os

#Load variables from .env files

load_dotenv()

#LLM configuration
# Which provider to use: "anthropic" (default) or "openai".
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()

# Default model per provider. Override with LLM_MODEL if you want something else.
# These defaults are intentionally the cheaper/faster tiers — good enough for an
# MVP and keeps your token bill low while you iterate.
_DEFAULT_MODELS = {
    "anthropic": "claude-3-5-sonnet-latest",
    "openai": "gpt-4.1-mini",
}
LLM_MODEL = os.getenv("LLM_MODEL", _DEFAULT_MODELS.get(LLM_PROVIDER, "claude-3-5-sonnet-latest"))

# Provider API keys. Only the key for the *selected* provider is required.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


#Jira Confirguration
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
# Atlassian account email, should be a service account later on
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "CSCI")
# Custom field id for story points. Sigma's "Story point estimate" field is
# customfield_10052 — override if your Jira instance uses a different id.
JIRA_STORY_POINTS_FIELD = os.getenv("JIRA_STORY_POINTS_FIELD", "customfield_10052").strip()



#Testing mode
JIRA_MOCK = (
    os.getenv("JIRA_MOCK", "").lower() in ("1", "true", "yes")
    or not JIRA_BASE_URL
)


# Optional observability via LangSmith
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")


# MCP (Model Context Protocol) servers.
# Lucid's hosted MCP server (diagram search/create) — see
# pmagent/tools/mcp_tools.py for the client that uses these, and
# ../snowflake/sql/lucid_mcp_setup.sql for the equivalent Snowflake-side setup.
# Unset LUCID_MCP_AUTH_TOKEN is fine if your Lucid plan relies on Dynamic
# Client Registration (per-user OAuth) rather than a static client secret.
LUCID_MCP_URL = os.getenv("LUCID_MCP_URL", "https://mcp.lucid.app/mcp")
LUCID_MCP_AUTH_TOKEN = os.getenv("LUCID_MCP_AUTH_TOKEN")


# Microsoft Graph delegated access for the approval-gated spreadsheet tool.
# A public-client app registration is required; no client secret is used.
SPREADSHEET_TENANT_ID = os.getenv("SPREADSHEET_TENANT_ID")
SPREADSHEET_CLIENT_ID = os.getenv("SPREADSHEET_CLIENT_ID")



def validate() -> None:
    """Fail fast if the minimum config for the selected provider is missing.

    """
    if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        raise ValueError(
            "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. "
            "Add it to .env file."
        )
    if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
        raise ValueError(
            "LLM_PROVIDER=openai but OPENAI_API_KEY is not set. "
            "Add it to  .env file."
        )
