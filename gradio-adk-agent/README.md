# 🚀 Nexus BI: Enterprise-Grade AI Business Intelligence Agent

Nexus BI is an intelligent, multi-agent AI system that translates natural language into highly optimized MS SQL Server queries. It automatically executes queries, computes derived business metrics, generates interactive Altair charts, and delivers executive-level insights — all through a seamless Gradio UI.

---

## 🧠 Model Selection

This project uses **Gemma 3 27B** (`gemma-3-27b-it`) via Google's Agent Development Kit (ADK). The model was selected for:

- **Large context window** — essential for injecting full database schemas, business metadata, and multi-example prompt libraries without truncation.
- **Advanced reasoning** — handles complex Star Schema `JOIN` logic, Foreign Key traversal, and derived metric formulas without hallucinating column names.
- **Code generation** — reliably produces both valid MS SQL and executable Python/Altair chart code in a single inference pass.

---

## 🧬 Project Evolution & Architecture

The system evolved across multiple versions to overcome the core challenges of applying LLMs to Enterprise Data Warehouses.

### V1 — Foundation (`kirenz/gradio-adk-agent` template)
- Single conversational agent, basic Gradio chatbot UI.
- No structured pipeline; high hallucination rate (invented columns like `Calendar_Year` in Fact tables).
- No automatic chart rendering.

### V2.5 — Multi-Agent Pipeline & UI Overhaul
- Split into three specialized agents: **SQL → Visualization → Explanation**.
- Added `gr.Progress()` real-time steps and query history tracking.
- Still struggled with date functions (applying `YEAR()` to Integer IDs) and blind `JOIN` operations.

### V3 — Schema Pre-loading & Positive Constraints
- Schema fetched **once on startup** (`PRELOADED_SCHEMA`) and reused for every request — eliminates per-query DB round-trips.
- Refactored prompts from negative constraints ("don't do X") to **positive rules** ("copy exact column names") to avoid the Pink Elephant problem.

### V4 — Semantic Layer, Star Schema Mastery & Metric Engine (Current)
- **Dense Schema (`db_config.py`)** — hardcoded, token-optimized schema string with business metadata, verified column lists, and crash-prevention rules injected into every prompt.
- **Derived Metric SQL** — Agent 1 now computes Gross Profit, Gross Margin %, Cost, Revenue per Unit, Discount Rate, and Net Revenue directly in SQL using `NULLIF`-protected formulas.
- **Visualization overhaul** — Agent 2 covers 9 chart types (KPI Card, Line single/multi-year, Vertical Bar, Horizontal Bar, Grouped Bar, Stacked Bar, Stacked Area, Scatter) with chronological month sorting via `ID_Calendar_Month`.
- **Token Usage Tracker** — every request logs estimated input/output tokens per agent and displays a live table in the UI.
- **Type-safe JSON serialization** — `_SafeEncoder` in `tools.py` handles `date`, `datetime`, `Decimal`, and numpy types that would otherwise crash `json.dumps`.

---

## ⚙️ How It Works — Core Pipeline

```
User Question
     │
     ▼
[Schema Injection]  ←── PRELOADED_SCHEMA (dense hardcoded string, loaded once at startup)
     │
     ▼
[Agent 1: Text-to-SQL]
  • Converts question to MS SQL SELECT
  • Applies Star Schema JOIN rules
  • Computes derived metrics inline:
    Gross Profit = SUM(Revenue) - SUM(Transfer_Price × Sales_Amount)
    Gross Margin % = Gross Profit / NULLIF(Revenue, 0) × 100
    Revenue per Unit = SUM(Revenue) / NULLIF(SUM(Sales_Amount), 0)
    Discount Rate % = SUM(Discount) / NULLIF(Revenue + Discount, 0) × 100
  • Always includes viz helper columns for trends:
    Calendar_Year, ID_Calendar_Month, Calendar_Month_Number, Calendar_Month_Name
     │
     ▼
[SQL Executor — sql_executor.py]
  • Validates query (SELECT-only whitelist, blacklist: DROP/DELETE/UPDATE/...)
  • Enforces max 1,000 rows, 30s timeout
  • Returns pandas DataFrame
     │
     ▼
[Token Counter]  ←── logs estimated input + output tokens for all 3 agents
     │
     ├──────────────────────────────────────┐
     ▼                                      ▼
[Agent 2: Visualization]          [Agent 3: Explanation]
  • Selects chart type by data shape   • Writes executive summary
  • Sorts months by ID (Jan→Dec)       • Infers correct units ($/units/%)
  • Renders Altair chart object        • Highlights gaps & strategic advice
     │                                      │
     └──────────────┬───────────────────────┘
                    ▼
[Gradio UI — app.py]
  • Tab 1: Chart + Executive Summary + Token Usage
  • Tab 2: Formatted SQL (sqlparse) + Raw DataFrame
  • Tab 3: Session Logs (expandable history per query)
```

---

## 📁 Project Structure

```
gradio-adk-agent/
├── bi_agent/
│   ├── __init__.py         # Package exports
│   ├── agent.py            # All 3 agent definitions (SQL, Viz, Explanation)
│   ├── tools.py            # execute_sql_and_format(), get_database_schema(), _SafeEncoder
│   ├── bi_service.py       # BIService class (connect, load_schema, execute_sql)
│   ├── db_config.py        # Dense schema string + create_db_engine()
│   ├── sql_executor.py     # validate_sql(), execute_query(), serialize_dataframe()
│   └── .env                # API keys and DB credentials (not committed)
├── app.py                  # Gradio UI + pipeline orchestration + token tracker
├── pyproject.toml          # uv-managed dependencies
├── README.md               # This file
└── AGENTS.md               # Architecture & dev guide
```

---

## 🗄️ Database — AdventureBikes Sales DataMart

The system connects to an MS SQL Server Star Schema Data Warehouse.

### Fact Tables
| Table | Key Metrics | Description |
|---|---|---|
| `Facts_Monthly_Sales` | Revenue, Sales_Amount, Transfer_Price, Discount | Core monthly sales facts |
| `Facts_Daily_Sales` | Revenue, Sales_Amount, Discount | Daily granularity facts |
| `Facts_Monthly_Sales_Quota` | Revenue_Quota, Sales_Amount_Quota | Targets vs actuals |

### Dimension Tables
| Table | Key Columns | Description |
|---|---|---|
| `Dim_Product` | ID_Product, Material_Description, Product_Category, Transfer_Price_EUR | Products & pricing |
| `Dim_Calendar_Month` | ID_Calendar_Month, Calendar_Year, Calendar_Month_Number, Calendar_Month_Name | Monthly calendar |
| `Dim_Sales_Office` | ID_Sales_Office, Sales_Country, Sales_Region, Global_Region | Geography |
| `Dim_Currency` | ID_Currency, Currency_Name | Currency |

### Pre-joined Flat Tables
| Table | Use Case |
|---|---|
| `DataSet_Monthly_Sales` | Simple queries without JOINs (Revenue by Category, etc.) |
| `DataSet_Monthly_Sales_and_Quota` | Quota vs actuals (note: column names have spaces — use `[brackets]`) |

---

## 🔢 Supported Derived Metrics (computed in SQL)

| User asks for | SQL Formula | Output Column |
|---|---|---|
| Profit / Gross Profit | `SUM(Revenue) - SUM(Transfer_Price × Sales_Amount)` | `Gross_Profit` |
| Gross Margin % | `Gross_Profit / NULLIF(Revenue, 0) × 100` | `Gross_Margin_Pct` |
| Total Cost | `SUM(Transfer_Price × Sales_Amount)` | `Total_Cost` |
| Revenue per Unit | `SUM(Revenue) / NULLIF(SUM(Sales_Amount), 0)` | `Revenue_per_Unit` |
| Discount Rate % | `SUM(Discount) / NULLIF(Revenue + Discount, 0) × 100` | `Discount_Rate_Pct` |
| Net Revenue | `SUM(Revenue) - SUM(Discount)` | `Net_Revenue` |

All division formulas use `NULLIF(..., 0)` to prevent divide-by-zero crashes.

---

## 🚀 Quick Start

We use `uv` for fast dependency management.

### 1. Install `uv`

**Windows:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone & Install Dependencies

```bash
git clone <repo-url>
cd gradio-adk-agent
uv sync
```

### 3. Configure Environment

Create `bi_agent/.env`:

```env
GOOGLE_API_KEY=your_google_api_key_here

# SQL Server
MSSQL_SERVER=your.server.address
MSSQL_DATABASE=YourDatabase
MSSQL_USERNAME=your_username
MSSQL_PASSWORD=your_password
MSSQL_DRIVER=ODBC Driver 18 for SQL Server
TRUST_SERVER_CERTIFICATE=true
```

> ⚠️ Requires **ODBC Driver 18 for SQL Server** installed on the host machine.  
> Download: https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server

### 4. Run

**Option A — Gradio UI (recommended):**
```bash
uv run app.py
```
Opens at: http://127.0.0.1:7860

**Option B — ADK Web Interface:**
```bash
uv run adk web . --port 8000
```
Opens at: http://127.0.0.1:8000

---

## 💡 Example Questions

```
Show monthly sales trends for products in the 'Bikes' category during 2023
Compare the total revenue of each Currency
What are the top 3 most expensive product categories based on average price?
Show gross profit and margin % by product category for 2023
Compare revenue, profit and gross margin % by category in 2023
Show profit trend by month comparing 2023 and 2024
What is the discount rate by sales region?
Show the total sales for Q1 2023 grouped by product category
```

---

## 🔒 Security

- **SQL Injection Protection** — `sql_executor.py` validates all queries against a keyword blacklist (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `CREATE`, `TRUNCATE`, `EXEC`, `xp_`, `sp_`).
- **SELECT-only** — Any query not starting with `SELECT` is rejected before execution.
- **Row Cap** — Queries are capped at 1,000 rows and 30-second timeout by default.
- **Credentials** — Stored in `.env`, never hardcoded or committed.

---

## 📦 Key Dependencies

| Package | Purpose |
|---|---|
| `google-adk` | Agent Development Kit — LlmAgent, InMemoryRunner |
| `google-genai` | Gemma/Gemini model inference |
| `gradio` | Web UI |
| `altair` | Declarative chart rendering |
| `sqlalchemy` + `pyodbc` | MS SQL Server connection |
| `pandas` | DataFrame operations |
| `sqlparse` | SQL formatting for display |
| `python-dotenv` | Environment variable loading |
