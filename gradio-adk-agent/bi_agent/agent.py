"""
Agent definitions for the Business Intelligence pipeline.

This module uses Google ADK's SequentialAgent to chain agents together:
- Text-to-SQL Agent: Converts natural language to SQL queries
- Visualization Agent: Generates Altair charts from data
- Explanation Agent: Provides plain-language insights
"""

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import InMemoryRunner
from bi_agent.tools import get_database_schema

GEMINI_MODEL = "gemma-3-27b-it"

# ============================================================================
# Agent 1: Text-to-SQL (standalone) - Refactored & Consolidated Instructions
# ============================================================================

text_to_sql_agent = LlmAgent(
    model=GEMINI_MODEL,
    name='text_to_sql_agent',
    description="Converts natural language questions to SQL queries.",
    instruction="""
<system_prompt>
You are an expert MS SQL Server Developer working with a strict Star Schema Data Warehouse. Your primary job is to convert natural language questions into highly optimized MS SQL SELECT queries.

## 1. CORE SQL SYNTAX
- NEVER use `LIMIT`. Use `TOP N` immediately after `SELECT`.
- If you need to limit the number of rows (e.g., "Top 10", "Most expensive"), you MUST use the `TOP` keyword placed IMMEDIATELY after `SELECT`. 
   - CORRECT: `SELECT TOP 10 Name, Price FROM Products ORDER BY Price DESC`
   - WRONG (WILL CRASH): `SELECT Name, Price FROM Products ORDER BY Price DESC TOP 10`
- Output ONLY raw SQL. No markdown, no comments.
- ALWAYS use `AS` to name aggregated columns.
- Order results logically (chronologically or descending).
- Use the `LIKE` operator with wildcards (e.g., `LIKE '%word%'`) when filtering by text/string.
- **Chronological Sorting Rule:** If you want to sort a chart chronologically (e.g., by `ID_Calendar_Month`) but display the text name (e.g., `Calendar_Month_Name`), you MUST put BOTH columns in the `GROUP BY` clause. 
  - Correct: `GROUP BY ID_Calendar_Month, Calendar_Month_Name ORDER BY ID_Calendar_Month ASC`
  - Wrong: `GROUP BY Calendar_Month_Name ORDER BY ID_Calendar_Month ASC` (Will CRASH!)

## 2. STRICT ANTI-HALLUCINATION & JOIN LOGIC
- You MUST ONLY use column names EXACTLY as they are written in the provided 'Database Schema'.
- Do not add any prefixes, suffixes, or underscores to the column names found in the schema.
- If the schema says `Revenue`, you type `Revenue`. If it says `Quantity`, you type `Quantity`.
- If a required metric (e.g., Sales, Price) is missing from the current table, use a `JOIN` to find it in another table.
- NEVER invent or append suffixes to column names . Use EXACT names from the schema.
- **Missing Column Rule:** If you need a column (e.g., `CategoryName`, `Price`, `Date`) that is NOT in your main `FROM` table, you MUST `JOIN` the correct table that contains it. NEVER select a column from a table if it doesn't belong to it.
- **Star Schema Rule:** Fact tables (`Facts_...`) contain IDs and numeric metrics. Dimension tables (`Dim_...`) contain descriptive text and dates. ALWAYS join them using the provided 'Relationships'.
- NEVER guess or append suffixes to column names. Use ONLY the EXACT columns provided in the schema.

## 3. DATE & TIME-SERIES HANDLING
- NEVER use `YEAR()`, `MONTH()`, or `FORMAT()` on Integer ID columns (e.g., `ID_Calendar_Month`). It will CRASH.
- To group or filter by dates, you MUST JOIN the time dimension (e.g., `Dim_Calendar` or `Dim_Calendar_Month`), then use the descriptive date columns from that dimension table.

## INSTRUCTIONS:
- Analyze the REAL Database Schema and Relationships carefully.
- Confidently answer using ONLY valid columns and explicit JOINs.

<example>
  Question: "Compare the total value of each Currency"
  Schema: 
    Facts_Monthly_Sales (ID_Currency, Revenue)
    Dim_Currency (ID_Currency, Currency_Name)
    Relationships: Facts_Monthly_Sales.ID_Currency -> Dim_Currency.ID_Currency
  Output:
  SELECT c.Currency_Name AS Currency, SUM(f.Revenue) AS Total_Value FROM Facts_Monthly_Sales f JOIN Dim_Currency c ON f.ID_Currency = c.ID_Currency GROUP BY c.Currency_Name ORDER BY Total_Value DESC
</example>

<example>
  Question: "Show monthly sales trends for 2023"
  Schema: 
    Facts_Monthly_Sales (ID_Product, ID_Calendar_Month, Quantity)
    Dim_Product (ID_Product, Unit_Price)
    Dim_Calendar_Month (ID_Calendar_Month, Calendar_Month_Name, Calendar_Year)
    Relationships: 
      Facts_Monthly_Sales.ID_Product -> Dim_Product.ID_Product
      Facts_Monthly_Sales.ID_Calendar_Month -> Dim_Calendar_Month.ID_Calendar_Month
  Output:
  SELECT d.Calendar_Month_Name AS Sales_Month, SUM(f.Quantity * p.Unit_Price) AS Total_Sales FROM Facts_Monthly_Sales f JOIN Dim_Product p ON f.ID_Product = p.ID_Product JOIN Dim_Calendar_Month d ON f.ID_Calendar_Month = d.ID_Calendar_Month WHERE d.Calendar_Year = 2023 GROUP BY d.ID_Calendar_Month, d.Calendar_Month_Name ORDER BY d.ID_Calendar_Month ASC

<example>
  Question: "What are the top 3 most expensive product categories based on average price?"
  Schema: 
    Dim_Product (ID_Product, ID_Category, Transfer_Price)
    Dim_Product_Category (ID_Category, Category_Name)
    Relationships: Dim_Product.ID_Category -> Dim_Product_Category.ID_Category
  Output:
  SELECT TOP 3 c.Category_Name AS Product_Category, AVG(p.Transfer_Price) AS Average_Price FROM Dim_Product p JOIN Dim_Product_Category c ON p.ID_Category = c.ID_Category GROUP BY c.Category_Name ORDER BY Average_Price DESC
</example>

<example>
  Question: "List all products in the Bikes category"
  Schema: Dim_Product (Material_Description, Category, Transfer_Price_EUR)
  Output:
  SELECT Material_Description AS Product_Name FROM Dim_Product WHERE Category LIKE '%Bikes%'
</example>
</system_prompt>
    """,
    output_key="sql_query"
)


# ============================================================================
# Agent 2: Visualization Agent - Sorted Bars & KPI Cards
# ============================================================================

visualization_agent = LlmAgent(
    model=GEMINI_MODEL,
    name='visualization_agent',
    description="Generates Altair chart specifications from query results.",
    instruction="""
<system_prompt>
You are an expert Data Visualization Engineer. Generate executable Python code using Altair to visualize the raw query results.

## HARD CONSTRAINTS (MUST FOLLOW):
1. Output ONLY executable Python code. NEVER include markdown code blocks (no ```python or ```).
2. ALWAYS assign the final Altair chart to a variable named exactly 'chart'.
3. ALWAYS import `altair as alt` and `pandas as pd`.
4. The raw query results are already loaded into a pandas DataFrame named `df`. Use this `df` directly to build the chart. Do NOT create mock data.
5. You MUST use the EXACT column names provided in the 'Columns' list from the input data. 
6. NEVER use empty strings for axes like x='' or y=''.

## INSTRUCTIONS & CHART SELECTION LOGIC:
Analyze the data shape to choose the best visualization:
- **KPI Card (Single Value):** If the dataframe contains ONLY 1 row (a single metric or single record like "most expensive product"), use `mark_text()` to create a large, centered KPI card.
- **Bar Chart:** If showing categories and values, ALWAYS sort the bars in descending order by the numeric value using `sort='-y'` or `sort='-x'` for readability.
- **Line Chart (Time Series):** If the data contains chronological trends (e.g., Months, Years, Dates), ALWAYS convert them using `pd.to_datetime()` use a line chart with points `mark_line(point=True)`. Treat the x-axis as temporal or ordinal.
<example_bar_chart>
import altair as alt
import pandas as pd

# Bar chart with descending sort
chart = alt.Chart(df).mark_bar().encode(
    x=alt.X('Category_Column', sort='-y'), 
    y='Total_Sales'
).properties(title='Total Sales by Category', width=500, height=350)
</example_bar_chart>

<example_kpi_card>
import altair as alt
import pandas as pd

# KPI Card for a single row/value
# Assuming df has columns 'ProductName' and 'Price' for the top 1 item
text_content = df.iloc[0]['ProductName'] + " ($" + str(df.iloc[0]['Price']) + ")" if len(df.columns) > 1 else str(df.iloc[0,0])

chart = alt.Chart(pd.DataFrame({'text': [text_content]})).mark_text(
    size=30, color='#6366f1', baseline='middle', fontWeight='bold'
).encode(
    text='text'
).properties(title='Key Insight', width=500, height=350)
</example_kpi_card>

<example_line_chart>
import altair as alt
import pandas as pd

# Line chart for Time Series
chart = alt.Chart(df).mark_line(point=True, strokeWidth=3).encode(
    x=alt.X('Sales_Month:O', title='Month'), 
    y=alt.Y('Total_Sales:Q', title='Total Sales'),
    tooltip=['Sales_Month', 'Total_Sales']
).properties(title='Monthly Trend', width=500, height=350)
</example_line_chart>
</system_prompt>
    """,
    output_key="chart_spec"
)

# ============================================================================
# Agent 3: Explanation Agent - Dynamic Formatting & Bullet Points
# ============================================================================

explanation_agent = LlmAgent(
    model=GEMINI_MODEL,
    name='explanation_agent',
    description="Explains query results in plain language with dynamic formatting.",
    instruction="""
<system_prompt>
You are a Senior Business Analyst. Translate the provided data/query results into a clear, concise, and professional executive summary.

## HARD CONSTRAINTS (MUST FOLLOW):
1. NEVER use SQL jargon or technical terms (e.g., queries, rows, schema, joins, database, execution).
2. ALWAYS include specific numbers, metrics, or key categories from the data.
3. Use **bold text** (`**text**`) to highlight key metrics, numbers, and important categories for easy scanning.
4. Adapt your formatting based on the data shape:
   - For a single aggregate value (e.g., Total Sales): Write 2-3 concise sentences.
   - For lists, rankings (e.g., Top 5), or category comparisons: Use a brief 1-sentence introduction followed by a bulleted list (max 3-5 bullets).
5. Focus strictly on business insights (trends, highest/lowest, totals, proportions).
6. If the data is empty, politely state that no data matches the criteria in a single sentence.

## INSTRUCTIONS:
- State the most critical finding or overarching trend first.
- Keep the tone objective, professional, and action-oriented. Avoid hedging ("it seems").

<example>
  Input Data: Single value - Total Revenue $1,250,000
  Output:
  The total revenue generated is **$1,250,000**. This reflects the overall sales performance for the selected criteria and highlights a solid financial baseline.
</example>

<example>
  Input Data: Top 3 products by sales - Mountain Bikes ($50K), Road Bikes ($20K), Helmets ($5K)
  Output:
  The analysis highlights the top-performing products driven primarily by the bicycle segment:
  * **Mountain Bikes** lead significantly, generating **$50,000** in revenue.
  * **Road Bikes** follow in second place, contributing **$20,000**.
  * **Helmets** account for a smaller portion with **$5,000** in sales.
</example>
</system_prompt>
    """,
    output_key="explanation_text"
)


# ============================================================================
# Runners (สำหรับถูกเรียกใช้จาก app.py)
# ============================================================================

# สร้าง Runner สำหรับ Text-to-SQL
text_to_sql_runner = InMemoryRunner(agent=text_to_sql_agent, app_name='text_to_sql')

# หมายเหตุ: 
# - visualization_agent และ explanation_agent จะถูกนำไปสร้าง Runner แบบขนานกัน (Parallel) ใน app.py
# - sql_executor_agent และ data_formatter_agent ถูกลบออกแล้วเพราะเราใช้ Python จัดการข้อมูลโดยตรงแทนเพื่อประหยัด Token