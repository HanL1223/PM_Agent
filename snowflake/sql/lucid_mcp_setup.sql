-- Wires Lucid's official hosted MCP server into a Snowflake Cortex Agent so
-- diagram creation happens entirely through Cortex AI's native MCP connector
-- capability — no Python `requests` call, no External Access Integration on
-- a Snowpark proc. See ../LUCID_DIAGRAM_AGENT.md for the full walkthrough.
--
-- Run this with a role that can create account-level objects (API
-- INTEGRATION, EXTERNAL MCP SERVER, AGENT) — typically ACCOUNTADMIN or a role
-- granted CREATE INTEGRATION / CREATE AGENT privileges. This is NOT part of
-- `snow snowpark deploy` (that only handles the procedure entities in
-- ../snowflake.yml) — run it separately, e.g.:
--   snow sql -f snowflake/sql/lucid_mcp_setup.sql
-- or paste it into a Snowsight worksheet.
--
-- Prerequisite: a Lucid account admin must enable MCP access for your
-- workspace in the Lucid Admin Panel first — this SQL only registers the
-- connector on the Snowflake side.

USE DATABASE PO_AI_DEV;
USE SCHEMA PO_AGENT;

-- 1. API integration: tells Snowflake how to OAuth-authenticate against
--    Lucid's MCP endpoint. Lucid's MCP server (https://mcp.lucid.app/mcp)
--    uses OAuth with Dynamic Client Registration (DCR) — no client
--    id/secret to obtain up front, Snowflake registers itself with Lucid on
--    first connect.
CREATE OR REPLACE API INTEGRATION lucid_mcp_integration
  API_PROVIDER = external_mcp
  API_ALLOWED_PREFIXES = ('https://mcp.lucid.app/mcp')
  API_USER_AUTHENTICATION = (
    TYPE = OAUTH_DYNAMIC_CLIENT
    OAUTH_RESOURCE_URL = 'https://mcp.lucid.app/mcp'
  )
  ENABLED = TRUE;

-- If Lucid instead issues your org a static OAuth client id/secret (check
-- the Lucid Admin Panel), replace the block above with:
--   API_USER_AUTHENTICATION = (
--     TYPE = OAUTH2
--     OAUTH_CLIENT_ID = '<client_id_from_lucid>'
--     OAUTH_CLIENT_SECRET = '<client_secret_from_lucid>'
--     OAUTH_TOKEN_ENDPOINT = '<token_endpoint_from_lucid>'
--     OAUTH_AUTHORIZATION_ENDPOINT = '<authorization_endpoint_from_lucid>'
--   )

-- 2. External MCP server object: the callable handle an Agent references.
CREATE OR REPLACE EXTERNAL MCP SERVER lucid_mcp_server
  WITH DISPLAY_NAME = 'Lucid MCP Server'
  URL = 'https://mcp.lucid.app/mcp'
  API_INTEGRATION = lucid_mcp_integration;

-- 3. The Agent itself: combines the Lucid MCP server with the
--    draft_diagram_brief custom tool (DRAFT_DIAGRAM_BRIEF must already be
--    deployed via `snow snowpark deploy` before this runs). Replace
--    <YOUR_WAREHOUSE> with a warehouse the agent's role can use.
CREATE OR REPLACE AGENT diagram_agent
  COMMENT = 'Diagram Brief + Lucid MCP agent'
  FROM SPECIFICATION
  $$
  instructions:
    response: >
      When asked to create or update a Lucid diagram, first call the
      draft_diagram_brief tool and show the resulting brief (diagram type,
      nodes, edges, description) to the user verbatim. Do NOT call any Lucid
      tool that creates, edits, or shares a document until the user has
      explicitly confirmed the brief is correct. Search and summarize calls
      against existing Lucid documents do not need confirmation.
    orchestration: >
      Use draft_diagram_brief to turn PRD/Reporting Intent text into a
      diagram description. Only pass that description to Lucid's
      create-diagram tool after explicit user confirmation — never chain
      draft_diagram_brief directly into a Lucid write tool in the same turn.
    sample_questions:
      - question: "Draw a diagram of the checkout flow from this PRD."

  tools:
    - tool_spec:
        type: "generic"
        name: "draft_diagram_brief"
        description: >
          Turns an approved PRD (and optional Reporting Intent brief) into a
          plain-language diagram description ready to hand to a diagramming
          tool. Does not create or modify anything outside Snowflake.
        input_schema:
          type: object
          properties:
            prd_text:
              type: string
              description: "The approved PRD text."
            reporting_intent_text:
              type: string
              description: "Optional Reporting Intent brief text. Pass \"\" if none exists."
          required:
            - prd_text

  tool_resources:
    draft_diagram_brief:
      type: "procedure"
      identifier: "PO_AI_DEV.PO_AGENT.DRAFT_DIAGRAM_BRIEF"
      execution_environment:
        warehouse: "<YOUR_WAREHOUSE>"

  mcp_servers:
    - server_spec:
        name: "PO_AI_DEV.PO_AGENT.LUCID_MCP_SERVER"
  $$;
