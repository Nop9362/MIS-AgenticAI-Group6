# 🚀 Nexus BI: Enterprise-Grade AI Business Intelligence Agent

Nexus BI is an intelligent, multi-agent AI system designed to translate natural language into highly optimized MS SQL Server queries (Text-to-SQL). It automatically queries databases, generates visual Altair charts, and provides business insights through a seamless Gradio interactive UI.

---

## 🧠 Model Selection
This project leverages **Google Gemini Models** via the `google.genai` SDK. Gemini was selected for its massive context window (essential for injecting full database schemas and business metadata) and its advanced reasoning capabilities for complex logical deductions, such as mapping Foreign Keys across Star Schemas without hallucinating.

---

## 🧬 Project Evolution & Architecture

The system has evolved significantly across multiple versions to tackle the inherent challenges of Large Language Models (LLMs) in Enterprise Data environments.

### 1. V1: The Foundation (Demo 1 - `kirenz/gradio-adk-agent`)
- **Workflow:** The initial prototype was built directly upon the `kirenz/gradio-adk-agent` template. It utilized a single conversational AI agent powered by Google GenAI's Agent Development Kit (ADK) to process user queries.
- **UX/UI:** A standard, out-of-the-box Gradio Chatbot interface. It handled basic conversational back-and-forth but lacked advanced UI elements like progress trackers, data tables, or dedicated visual chart rendering areas.
- **Prompt Engineering:** Relied on very basic system instructions. The LLM was given the database schema and asked to return SQL queries without strict syntactical constraints.
- **Challenges:** The single-agent ADK approach was too generic. Without a structured, step-by-step pipeline, it suffered from a high rate of AI "Hallucinations" (e.g., making up column names like `Calendar_Year`) and possessed no capabilities to visualize the data automatically.

### 2. V2.5: Multi-Agent Architecture & UI Overhaul (Demo 2)
- **Workflow:** Transitioned to a **Multi-Agent Architecture**. The workflow split into three specialized AI roles:
  1. **SQL Agent:** Converts text to executable MS SQL.
  2. **Visualization Agent:** Generates executable Python code for Altair charts.
  3. **Explanation Agent:** Summarizes the results into actionable business insights.
- **UX/UI:** Introduced Gradio `gr.Progress()` to show real-time execution steps. Added Chat History to track successful/failed queries and execution times.
- **Challenges:** Still struggled with complex Date/Time functions (e.g., applying `YEAR()` to Integer IDs) and blind `JOIN` operations.

### 3. V3: Schema Context & Advanced Prompting (Demo 3)
- **Workflow:** Implemented **Schema Pre-loading**. The database schema is fetched once upon server startup to drastically reduce inference latency. 
- **Prompt Engineering:** Addressed the "Pink Elephant Problem" (Negative Prompting). Instead of telling the AI *what not to do* (which causes it to fixate on the forbidden words), the prompt was refactored using **Positive Constraints** ("Strict Copy-Paste Column Names Rule").
- **Challenges:** The AI still lacked business context (e.g., distinguishing between Quantity and Revenue).

### 4. Latest Version: Semantic Layer & Star Schema Mastery (`gradio-adk-agent`)
- **Workflow:** The ultimate Enterprise-grade setup. Implemented a dynamic **Semantic Layer (Data Dictionary)** directly into `db_config.py` using `BUSINESS_METADATA`. This injects real-world meaning into the schema (e.g., explaining that `Sales_Amount` means Quantity, and `ID_Calendar_Month` is a YYYYMM integer).
- **Foreign Key Mapping:** The system now automatically extracts `sys.foreign_keys` from MS SQL and feeds the exact relationships to the AI, completely eliminating `JOIN` hallucinations.
- **Prompt Engineering:** - **Star Schema Mastery:** Strict rules forcing the AI to join `Facts_` tables (metrics) with `Dim_` tables (descriptive data) rather than inventing text columns in Fact tables.
  - **Chronological Sorting Fix:** Taught the AI MS SQL's strict `GROUP BY` + `ORDER BY` rules to ensure time-series charts (Altair Line Charts) are sorted chronologically by ID, not alphabetically by Month Name.
- **UX/UI:** Integrated `sqlparse` to beautifully format and indent the generated SQL queries in the UI's Technical Details tab, making debugging a breeze.

*(Note: The "One Big Table" (OBT) technique was explored during development but is intentionally excluded from this version, keeping the focus strictly on mastering complex Star Schema queries.)*

---

## ⚙️ How It Works (The Core Workflow)

1. **User Input:** The user asks a question via the Gradio Chat Interface.
2. **Schema Injection:** The pre-loaded MS SQL Schema + Foreign Keys + Business Metadata are combined with the user's prompt.
3. **SQL Generation (Agent 1):** The LLM generates a strictly formatted SQL query.
4. **Database Execution:** The Python backend securely executes the query via `pyodbc`/`SQLAlchemy` and returns a Pandas DataFrame.
5. **Visualization (Agent 2):** If data is returned, Agent 2 analyzes the DataFrame shape and generates Altair Python code to render the optimal chart (Line, Bar, or KPI Card).
6. **Insight Generation (Agent 3):** Agent 3 reads the data context and outputs a concise business summary.
7. **UI Render:** Gradio updates the screen with the formatted SQL, Chart, DataFrame, and Text Explanation.

---

## 🚀 Quick Start & Installation

We use `uv`, the extremely fast Python package and project manager, to ensure seamless dependency synchronization.

### 1. Install `uv` (If not already installed)
- **Windows**: 
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm [https://astral.sh/uv/install.ps1](https://astral.sh/uv/install.ps1) | iex"
