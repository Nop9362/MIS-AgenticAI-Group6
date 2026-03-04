# AGENTS.md — Nexus BI Developer Guide

This document describes the full agent architecture, module responsibilities, prompt engineering decisions, and development conventions for the Nexus BI project.

---

## Context

Nexus BI is a sequential multi-agent pipeline built on **Google's Agent Development Kit (ADK)** with a **Gradio** web interface. It converts natural language business questions into MS SQL queries, executes them against a Star Schema Data Warehouse, and returns charts + executive summaries.

The project runs in two modes:
```bash
uv run app.py              # Gradio UI at http://127.0.0.1:7860  (primary)
uv run adk web . --port 8000   # ADK web UI at http://127.0.0.1:8000
```

Both must be run from the **project root directory**.

---

## Project Structure

```
gradio-adk-agent/
├── bi_agent/
│   ├── __init__.py         # Package exports for all agents, tools, and services
│   ├── agent.py            # Agent 1 (SQL), Agent 2 (Viz), Agent 3 (Explanation)
│   ├── tools.py            # Tool functions exposed to agents + _SafeEncoder
│   ├── bi_service.py       # BIService class — DB connect/schema/execute
│   ├── db_config.py        # Dense hardcoded schema + create_db_engine()
│   ├── sql_executor.py     # SQL validation + safe execution + serialization utils
│   └── .env                # Credentials (never commit)
├── app.py                  # Gradio UI + pipeline orchestration + token tracker
├── pyproject.toml          # uv-managed Python dependencies
├── README.md               # User-facing documentation
└── AGENTS.md               # This file — developer architecture reference
```

---

## Agent Pipeline Overview

Agents run **sequentially** in a request-scoped pipeline orchestrated by `app.py`. Agent 2 and Agent 3 run **concurrently** via `asyncio.gather()`.

```
User Question
     │
     ▼
Agent 1: text_to_sql_agent          ← InMemoryRunner (text_to_sql_runner)
     │  output_key: "sql_query"
     ▼
sql_executor.py → pandas DataFrame
     │
     ├─────────────────────────────────┐
     ▼                                 ▼
Agent 2: visualization_agent     Agent 3: explanation_agent
  output_key: "chart_spec"         output_key: "explanation_text"
     │                                 │
     └────────────┬────────────────────┘
                  ▼
           Gradio UI render
```

Each agent is an `LlmAgent` with a dedicated `InMemoryRunner`. Sessions are created fresh per request — agents are stateless between queries.

---

## Module Reference

### `bi_agent/agent.py`

Defines all three agents and their runners. The only file that changes when prompt engineering or adding new agents.

#### Agent 1 — `text_to_sql_agent`
- **Model:** `gemma-3-27b-it`
- **output_key:** `sql_query`
- **Runner:** `text_to_sql_runner = InMemoryRunner(agent=text_to_sql_agent, app_name='text_to_sql')`

**Prompt sections:**

| Section | Purpose |
|---|---|
| `CRITICAL RULES` | TOP N syntax, SELECT-only, AS aliases, JOIN rules, GROUP BY aggregation |
| `LONG FORMAT PREFERENCE` | Forces Year/Month as dimension columns, never pivoted |
| `TIME-SERIES RULES` | Never YEAR()/MONTH() on IDs; always include viz helper cols; ORDER BY ID not name |
| `DERIVED METRIC RULES` | Lookup table of user intent → exact SQL formula |

**Derived Metric Formulas (written directly into SQL SELECT):**

```sql
-- Gross Profit
SUM(f.Revenue) - SUM(f.Transfer_Price * f.Sales_Amount) AS Gross_Profit

-- Gross Margin %
ROUND((SUM(f.Revenue) - SUM(f.Transfer_Price * f.Sales_Amount))
      / NULLIF(SUM(f.Revenue), 0) * 100, 2) AS Gross_Margin_Pct

-- Total Cost
SUM(f.Transfer_Price * f.Sales_Amount) AS Total_Cost

-- Revenue per Unit
SUM(f.Revenue) / NULLIF(SUM(f.Sales_Amount), 0) AS Revenue_per_Unit

-- Discount Rate %
ROUND(SUM(f.Discount) / NULLIF(SUM(f.Revenue) + SUM(f.Discount), 0) * 100, 2) AS Discount_Rate_Pct

-- Net Revenue
SUM(f.Revenue) - SUM(f.Discount) AS Net_Revenue
```

> All division operations use `NULLIF(..., 0)` to prevent divide-by-zero SQL errors.

**Time-series visualization helper columns (always included for trend queries):**
```sql
-- Monthly
d.Calendar_Year, d.ID_Calendar_Month, d.Calendar_Month_Number, d.Calendar_Month_Name AS Month

-- Sorting (always by ID, never alphabetically by name)
ORDER BY d.Calendar_Year ASC, d.ID_Calendar_Month ASC
```

---

#### Agent 2 — `visualization_agent`
- **Model:** `gemma-3-27b-it`
- **output_key:** `chart_spec`
- **Runner:** `viz_runner = InMemoryRunner(agent=visualization_agent, app_name='viz_app')`

Outputs **raw executable Python code** that assigns an Altair chart to `chart`. The code is executed inside `app.py` with `exec()` inside an isolated `local_vars` dict.

**Chart selection decision tree:**

| Data shape | Chart type |
|---|---|
| 1 row, 1–2 cols | KPI Card (`mark_text`) |
| Month/Year/Date columns present, 1 group | Line + Area fill (single series) |
| Month/Year columns, multiple years | Multi-line colored by year (`xOffset` not used — `color=Year`) |
| 2 categorical dims + 1 metric, comparing side-by-side | Grouped Bar (`xOffset=Group:N`) |
| 2 categorical dims + 1 metric, part-of-whole | Stacked Bar (`stack="zero"`) |
| 1 categorical + 1 metric, long labels or many rows | Horizontal Bar |
| 1 categorical + 1 metric, short labels | Vertical Bar |
| 2 numeric columns | Scatter Plot (`mark_circle`) |

**Critical encoding rule — no f-strings inside Altair encode():**
```python
# ❌ CRASHES — Altair treats this as a literal field name
alt.X(f"{_month_col}:O")

# ✅ CORRECT — resolve to plain string first, then pass
_x_enc = _month_col + ":O"
alt.X(_x_enc, sort=_month_order)
```

**Chronological sort pattern:**
```python
# Step 1: find numeric sort column
_id_col = "ID_Calendar_Month" if "ID_Calendar_Month" in df.columns else "Calendar_Month_Number"
# Step 2: sort df
df = df.sort_values(_id_col)
# Step 3: derive ordered label list
_mon_order = list(df["Month"].astype(str))
# Step 4: pass to Altair (not f-string)
_x_enc = "Month:O"
alt.X(_x_enc, sort=_mon_order)
```

---

#### Agent 3 — `explanation_agent`
- **Model:** `gemma-3-27b-it`
- **output_key:** `explanation_text`
- **Runner:** `exp_runner = InMemoryRunner(agent=explanation_agent, app_name='exp_app')`

Produces a structured Markdown business narrative. Key behaviors:

- **Unit inference from column name** — `Revenue`/`Price`/`Profit` → `$`; `Sales_Amount`/`Units` → count; `Margin`/`Pct` → `%`
- **No technical jargon** — never says "SQL", "DataFrame", "rows", "query"
- **Always bold** specific numbers and category names
- **HTML underline** (`<u>`) on the single most critical recommendation

**Required output structure:**
```
**[One-sentence headline]**

- **Key Performance Indicators (KPIs):**
  - [Top metric with bold numbers]
    - *Insight:* [What this implies]
  - [Secondary metric or lowest performer]
    - *Insight:* [Gap or comparison]
- **Strategic Recommendation:**
  - <u>[Actionable advice]</u>
```

---

### `bi_agent/db_config.py`

Provides the schema string and DB engine — no dynamic schema querying at runtime.

```python
def get_schema_info(engine, ...) -> str:
    # Returns a hardcoded dense string — no DB round-trip
    return dense_schema
```

**Why hardcoded?** Dynamic `INFORMATION_SCHEMA` queries added ~1,500 tokens per request and introduced latency. The dense string is manually maintained and includes:
- Verified column lists per table (from actual DB inspection)
- Business metadata annotations (`Product_Category = text, e.g. Bikes`)
- Critical crash-prevention rules (e.g. `NO Dim_Product_Category table exists`)
- Correct JOIN relationships

**To update schema:** Edit `dense_schema` in `get_schema_info()` when the DB schema changes.

---

### `bi_agent/sql_executor.py`

Safe query execution layer. Never bypass this — all queries must go through it.

**`validate_sql(query)`**
- Rejects anything not starting with `SELECT`
- Blocks: `DROP DELETE UPDATE INSERT ALTER CREATE TRUNCATE EXEC EXECUTE GRANT REVOKE sp_ xp_`
- Rejects multiple statements (`;` mid-query)

**`execute_query(engine, query, timeout=30, max_rows=1000)`**
- Auto-injects `TOP 1000` if query has no `TOP`/`LIMIT`
- Returns `{'success', 'data' (DataFrame), 'error', 'row_count', 'columns'}`

---

### `bi_agent/tools.py`

Standalone functions callable by agents or the pipeline directly.

**`execute_sql_and_format(sql_query) -> str`**
- Reads DB credentials from env, creates engine, calls `execute_query`, returns JSON string
- Uses `_SafeEncoder` for serialization

**`_SafeEncoder(json.JSONEncoder)`**
Handles all types that MS SQL Server returns that standard `json.dumps` crashes on:

| Type | Serialized as |
|---|---|
| `datetime`, `date` | ISO string `"2023-01-15"` |
| `time` | ISO string `"14:30:00"` |
| `Decimal` | `float` |
| `NaN` / `Inf` | `null` |
| numpy scalar | native Python via `.item()` |
| numpy array | list via `.tolist()` |

**`get_database_schema() -> str`**
- Cached after first call (`_schema_cache`) — only hits DB once per server lifetime
- In practice superseded by `PRELOADED_SCHEMA` in `app.py`

---

### `bi_agent/bi_service.py`

`BIService` class wraps DB operations as an object with state (engine, schema). Used when you need a persistent connection across multiple operations. Not used in the main Gradio pipeline (which uses the stateless tool functions), but available for scripts and testing.

```python
svc = BIService(server, database, username, password)
svc.connect()
svc.load_schema()
result = svc.execute_sql("SELECT ...")
```

---

### `app.py`

Pipeline orchestration + Gradio UI + token tracking.

**Startup:**
```python
PRELOADED_SCHEMA = get_database_schema()  # fetched once, reused forever
SQL_CACHE = {}                             # normalized question → sql_query
```

**Per-request pipeline (`run_bi_pipeline_async`):**
1. Normalize question → check `SQL_CACHE`
2. If cache miss: call Agent 1 → cache result
3. Call `execute_sql_and_format` → parse JSON → build DataFrame
4. Build `formatted_data` string (200-row sample + value ranges + agg summary)
5. `asyncio.gather(get_chart(), get_explanation())` → Agent 2 + Agent 3 in parallel
6. Return all results + `token_report`

**Token Tracker:**
```python
TOKEN_LOG = {
    'sql_prompt': 0, 'sql_output': 0,
    'viz_prompt': 0, 'viz_output': 0,
    'exp_prompt': 0, 'exp_output': 0,
}
# Estimation: len(text) // 4  (~4 chars per token)
```
Displayed as a markdown table in the UI after every query.

**Chart execution (security note):**
```python
local_vars = {'df': df, 'alt': alt, 'pd': pd}
exec(compile(chart_spec_str, "<chart_spec>", "exec"), local_vars, local_vars)
chart = local_vars.get('chart')
```
Chart code runs in an isolated `local_vars` namespace. If execution fails, a visible red error card is rendered instead of a silent crash.

---

## Environment Variables

File: `bi_agent/.env`

```env
GOOGLE_API_KEY=...           # Google AI API key for Gemma inference

MSSQL_SERVER=...             # SQL Server hostname
MSSQL_DATABASE=...           # Database name (quote if it contains spaces)
MSSQL_USERNAME=...           # SQL login username
MSSQL_PASSWORD=...           # SQL login password
MSSQL_DRIVER=ODBC Driver 18 for SQL Server
TRUST_SERVER_CERTIFICATE=true
```

> Requires **ODBC Driver 18 for SQL Server** on the host.

---

## Development Workflows

### Adding a new derived metric

1. Open `bi_agent/agent.py` → `text_to_sql_agent` instruction
2. Add a row to the `## DERIVED METRIC RULES` table:
   ```
   | User asks for X | SUM(f.ColA) / NULLIF(SUM(f.ColB), 0) | AS X_Metric |
   ```
3. Add a matching example in `<examples>` showing the full SQL

### Adding a new chart type

1. Open `bi_agent/agent.py` → `visualization_agent` instruction
2. Add to the `## CHART SELECTION` section with the trigger condition
3. Add a working code example in `<examples>` — follow the f-string-safe pattern:
   ```python
   _col = "ActualColumnName"
   _enc = _col + ":Q"
   alt.Y(_enc)   # NOT alt.Y(f"{_col}:Q")
   ```

### Updating the database schema

1. Open `bi_agent/db_config.py` → `get_schema_info()` → edit `dense_schema`
2. Update table/column entries to match actual DB
3. Restart the server — `PRELOADED_SCHEMA` is loaded at startup

### Adding a new table the agent can query

1. Add to `dense_schema` in `db_config.py` with the `🚨 RULE:` annotation pattern
2. Add a JOIN relationship in section `[4. RELATIONSHIPS]`
3. Add an example query to `text_to_sql_agent` examples if the join pattern is novel

### Debugging a wrong SQL query

1. Check **Tab 2 → Technical Details** in the UI for the raw generated SQL
2. Check **Session Logs** for the explanation and SQL side by side
3. If the agent hallucinated a column: update `dense_schema` to explicitly state that column does NOT exist
4. If join order is wrong: add a specific example to the SQL agent with a `Note:` explaining the correct path

---

## Package Management

```bash
# Add a dependency
uv add <package-name>

# Sync all dependencies
uv sync

# Run the app
uv run app.py

# Run ADK web
uv run adk web . --port 8000
```
