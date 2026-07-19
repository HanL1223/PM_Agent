# Spreadsheet Change Agent: Snowflake Production Design

The local PM Agent tool is the Microsoft Graph executor. A Snowflake stored
procedure must not call Graph directly: the current production track has no
outbound network access, and OAuth credentials must not be embedded in a
procedure.

## Boundary

```text
Snowflake Cortex Agent -> proposal record -> approved external worker -> Microsoft Graph -> Excel approval queue
```

1. A Snowflake Cortex Agent turns a PM request into a structured proposal.
2. Store it in `PM_AGENT.SPREADSHEET_CHANGE_REQUESTS` with a stable request
   ID, workbook ID, sheet, target, expected value, proposed value, and status.
3. A small, externally hosted version of `pmagent.tools.spreadsheet_tools`
   writes the proposal to the workbook's `PM Agent Approvals` table.
4. A human approves in Excel. The worker applies only when **both** the
   Snowflake record and Excel queue row are `Approved`, and the target still
   equals `expected_value`.
5. The worker writes `Applied`, `Conflicted`, or `Failed` back to both audit
   stores.

## Registry and request contract

```sql
CREATE TABLE PM_AGENT.SPREADSHEETS (
  workbook_id STRING PRIMARY KEY,
  drive_id STRING NOT NULL,
  item_id STRING NOT NULL,
  display_name STRING NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE PM_AGENT.SPREADSHEET_CHANGE_REQUESTS (
  request_id STRING PRIMARY KEY,
  workbook_id STRING NOT NULL,
  operation STRING NOT NULL,
  sheet_name STRING NOT NULL,
  target STRING NOT NULL,
  expected_value STRING,
  proposed_value STRING NOT NULL,
  status STRING NOT NULL,
  approved_by STRING,
  created_at TIMESTAMP_TZ NOT NULL,
  applied_at TIMESTAMP_TZ,
  result STRING
);
```

`operation` starts with `set_cell`. Add row or table operations only after
their exact payloads and pre-apply validations are specified.

## External connection

Use a Cortex Agent with an External MCP Server or an approved external API
integration for the worker. Store Microsoft OAuth configuration in Snowflake
integrations/secrets, not in tables, prompts, or stored-procedure source.
The worker retains the local tool's delegated user authentication and all
human-approval checks.
