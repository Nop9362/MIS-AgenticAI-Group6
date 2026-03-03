import gradio as gr
import asyncio
import pandas as pd
import altair as alt
import json
import time
import sqlparse  
from datetime import datetime
from google.genai import types
from dotenv import load_dotenv

# นำเข้า Agent และ Runner 
from bi_agent.agent import text_to_sql_runner, visualization_agent, explanation_agent
from bi_agent.tools import execute_sql_and_format, get_database_schema
from google.adk.runners import InMemoryRunner

load_dotenv(dotenv_path='bi_agent/.env')

# ============================================================================
# Caching, Runners Setup & Preload Schema
# ============================================================================
SQL_CACHE = {}

viz_runner = InMemoryRunner(agent=visualization_agent, app_name='viz_app')
exp_runner = InMemoryRunner(agent=explanation_agent, app_name='exp_app')

# 🌟 1. ดึง Schema เก็บไว้ในหน่วยความจำตั้งแต่เริ่มรันเซิร์ฟเวอร์
print("⏳ Loading Database Schema on startup...")
PRELOADED_SCHEMA = get_database_schema()
print("✅ Database Schema Loaded Successfully!")

# ============================================================================
# Core Pipeline (Async)
# ============================================================================
async def run_bi_pipeline_async(user_question: str):
    normalized_question = user_question.strip().lower()
    
    if normalized_question in SQL_CACHE:
        sql_query = SQL_CACHE[normalized_question]
    else:
        session_sql = await text_to_sql_runner.session_service.create_session(user_id='user', app_name='text_to_sql')
        
        # 🌟 2. ใช้ PRELOADED_SCHEMA ที่โหลดไว้แล้ว แทนการดึงใหม่
        enhanced_prompt = f"Database Schema:\n{PRELOADED_SCHEMA}\n\nUser Question: {user_question}"
        content_sql = types.Content(role='user', parts=[types.Part(text=enhanced_prompt)])

        events_sql = text_to_sql_runner.run_async(user_id='user', session_id=session_sql.id, new_message=content_sql)
        sql_query = ""
        
        async for event in events_sql:
            if event.actions and event.actions.state_delta and 'sql_query' in event.actions.state_delta:
                sql_query = event.actions.state_delta['sql_query']

        if not sql_query: return {'sql_query': '-- Execution failed'}

        sql_query = sql_query.strip()
        if sql_query.startswith("```sql"): sql_query = sql_query.replace("```sql", "").replace("```", "").strip()
        elif sql_query.startswith("```"): sql_query = sql_query.replace("```", "").strip()
        SQL_CACHE[normalized_question] = sql_query

    query_results_str = execute_sql_and_format(sql_query)
    try: query_results = json.loads(query_results_str)
    except: query_results = {'success': False, 'data': [], 'error': 'Failed to parse results'}

    if not query_results.get('success', False):
        return {'sql_query': sql_query, 'query_results': query_results_str, 'error': query_results.get('error', 'Execution failed')}

    data_list = query_results.get('data', [])
    columns = query_results.get('columns', [])
    row_count = query_results.get('row_count', 0)
    
    df = pd.DataFrame(data_list)
    if not df.empty:
        # Detect column types
        numeric_cols = df.select_dtypes(include='number').columns.tolist()
        categorical_cols = df.select_dtypes(exclude='number').columns.tolist()

        # Full data (up to 200 rows) for agents to reason over
        sample_data = df.head(200).to_json(orient='records')

        # Descriptive stats on numeric columns
        summary_stats = df[numeric_cols].describe().to_json() if numeric_cols else "{}"

        # Per-group aggregation: group by all categorical cols, sum all numeric cols
        # This gives agents a clean rolled-up view of the data
        if categorical_cols and numeric_cols:
            agg_df = df.groupby(categorical_cols, sort=False)[numeric_cols].sum().reset_index()
            agg_summary = agg_df.to_json(orient='records')
            agg_note = f"Grouped Aggregation ({' x '.join(categorical_cols)} -> sum of {numeric_cols}):\n{agg_summary}"
        else:
            agg_note = "No categorical grouping available."

        # Value ranges per numeric column
        value_ranges = {col: {"min": float(df[col].min()), "max": float(df[col].max()),
                               "mean": round(float(df[col].mean()), 2),
                               "total": float(df[col].sum())}
                        for col in numeric_cols}
    else:
        sample_data, summary_stats = "[]", "{}"
        numeric_cols, categorical_cols = [], []
        agg_note = "No data."
        value_ranges = {}

    formatted_data = (
        f"Original User Question: {user_question}\n\n"
        f"SQL Query Executed:\n{sql_query}\n\n"
        f"=== DATA PROFILE ===\n"
        f"Total Rows: {row_count}\n"
        f"All Columns: {columns}\n"
        f"Numeric Columns: {numeric_cols}\n"
        f"Categorical Columns: {categorical_cols}\n"
        f"Value Ranges: {value_ranges}\n\n"
        f"=== FULL DATA (up to 200 rows) ===\n{sample_data}\n\n"
        f"=== AGGREGATED SUMMARY ===\n{agg_note}\n\n"
        f"=== DESCRIPTIVE STATISTICS ===\n{summary_stats}"
    )
    content_insight = types.Content(role='user', parts=[types.Part(text=formatted_data)])
    
    async def get_chart():
        session_viz = await viz_runner.session_service.create_session(user_id='user', app_name='viz_app')
        events = viz_runner.run_async(user_id='user', session_id=session_viz.id, new_message=content_insight)
        chart_spec = ""
        async for event in events:
            if event.actions and event.actions.state_delta and 'chart_spec' in event.actions.state_delta:
                chart_spec = event.actions.state_delta['chart_spec']
        return chart_spec

    async def get_explanation():
        session_exp = await exp_runner.session_service.create_session(user_id='user', app_name='exp_app')
        events = exp_runner.run_async(user_id='user', session_id=session_exp.id, new_message=content_insight)
        explanation_text = ""
        async for event in events:
            if event.actions and event.actions.state_delta and 'explanation_text' in event.actions.state_delta:
                explanation_text = event.actions.state_delta['explanation_text']
        return explanation_text

    chart_spec, explanation_text = await asyncio.gather(get_chart(), get_explanation())
    return {'sql_query': sql_query, 'query_results': query_results_str, 'chart_spec': chart_spec, 'explanation_text': explanation_text}

# ==========================================
# Gradio UI Functions & Logic
# ==========================================
async def process_request_async(message: str, history_state: list, progress=gr.Progress()):
    start_time = time.time()
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        if not message.strip():
            return "Error: Empty input", None, None, "No question provided", history_state, "⏱️ Error: Empty input", format_session_log(history_state)

        progress(0.2, desc="🧠 Analyzing schema & Generating SQL...")
        results = await run_bi_pipeline_async(message)
        
        raw_sql_query = results.get('sql_query', '')
        
        # จัดฟอร์แมต SQL ให้อ่านง่าย
        if raw_sql_query and not raw_sql_query.startswith("--"):
            formatted_sql = sqlparse.format(raw_sql_query, reindent=True, keyword_case='upper')
        else:
            formatted_sql = raw_sql_query
            
        query_results_str = results.get('query_results', '{}')
        
        progress(0.5, desc="🗄️ Executing query on MS SQL Server...")
        try: 
            query_results = json.loads(query_results_str) if isinstance(query_results_str, str) else query_results_str
        except: 
            query_results = {'success': False, 'data': [], 'error': 'Failed to parse'}

        if not query_results.get('success', False):
            error_msg = query_results.get('error', 'Unknown error')
            exec_time = f"{round(time.time() - start_time, 2)}s"
            error_text = f"❌ SQL Error: {error_msg}"
            # บันทึกประวัติกรณี Error
            history_state.insert(0, [timestamp, message, exec_time, "❌ Error", error_text, formatted_sql])
            return f"-- Error\n{formatted_sql}", None, None, error_text, history_state, f"⏱️ Failed in {exec_time}", format_session_log(history_state)

        progress(0.8, desc="📊 Designing visualizations & Extracting insights...")
        data_list = query_results.get('data', [])
        df = pd.DataFrame(data_list)
        
        chart_spec_str = results.get('chart_spec', '')
        chart = None
        if chart_spec_str:
            try:
                # Strip markdown fences if model accidentally included them
                import re
                chart_spec_str = re.sub(r"^```(?:python)?\s*", "", chart_spec_str.strip(), flags=re.MULTILINE)
                chart_spec_str = re.sub(r"```\s*$", "", chart_spec_str.strip(), flags=re.MULTILINE)
                # Inject configure for visual consistency
                chart_spec_str = chart_spec_str.replace(
                    "chart = alt.Chart(df)",
                    "chart = alt.Chart(df).configure_view(strokeWidth=0).configure_axis(grid=False)"
                )
                # Use a fully isolated namespace — pass df and all helpers explicitly
                # so variables like label_val are always defined in the same scope
                local_vars = {'pd': pd, 'alt': alt, 'df': df.copy()}
                exec(compile(chart_spec_str, "<chart_spec>", "exec"), local_vars, local_vars)
                chart = local_vars.get('chart')
                if chart is not None:
                    chart.to_dict()  # validate the chart object immediately
            except Exception as e:
                print(f"Chart generation error: {e}")
                # Render a visible error card instead of silently returning None
                error_df = pd.DataFrame({'msg': [f"Chart error: {str(e)}"]})
                chart = alt.Chart(error_df).mark_text(
                    size=14, color='#ef4444', baseline='middle'
                ).encode(text='msg:N').properties(
                    title='Visualization Error', width=500, height=200
                )

        explanation_text = results.get('explanation_text', '*No explanation generated.*')
        exec_time = f"{round(time.time() - start_time, 2)}s"
        
        # บันทึกประวัติลง Session Logs
        history_state.insert(0, [timestamp, message, exec_time, "✅ Success", explanation_text, formatted_sql])
        
        progress(1.0, desc="✨ Done!")
        return formatted_sql, df, chart, explanation_text, history_state, f"**⏱️ Processing time:** {exec_time}", format_session_log(history_state)
        
    except Exception as e:
        exec_time = f"{round(time.time() - start_time, 2)}s"
        error_text = f"System Error: {str(e)}"
        history_state.insert(0, [timestamp, message, exec_time, "❌ System Error", error_text, "N/A"])
        return str(e), None, None, error_text, history_state, f"⏱️ Error in {exec_time}", format_session_log(history_state)

# ฟังก์ชันจัดรูปแบบ HTML สำหรับหน้า Session Logs
def format_session_log(history):
    if not history:
        return "<p style='color: #6b7280; text-align: center;'><i>No session history yet. Ask a question to start logging!</i></p>"

    log_html = ""
    for item in history:
        timestamp, prompt, exec_time, status, explanation, sql = item
        status_color = "#16a34a" if "Success" in status else "#dc2626"
        log_html += f"""
        <div style="border: 1px solid #cbd5e1; padding: 16px; margin-bottom: 16px; border-radius: 10px;
                    background-color: #ffffff; box-shadow: 0 2px 6px rgba(0,0,0,0.08);">
            <h4 style="margin: 0 0 8px 0; font-size: 14px; color: #111827;">
                🕒 <span style="color: #111827;">{timestamp}</span> &nbsp;|&nbsp;
                <span style="color: {status_color}; font-weight: 700;">{status}</span> &nbsp;|&nbsp;
                <span style="color: #111827;">⏱️ {exec_time}</span>
            </h4>
            <p style="margin: 0 0 12px 0; font-size: 15px; font-weight: 600; color: #1f2937;">
                💬 <i>"{prompt}"</i>
            </p>
            <details style="margin-bottom: 10px;">
                <summary style="cursor: pointer; font-weight: 600; color: #2563eb; font-size: 14px; user-select: none;">
                    💡 View Insight Summary
                </summary>
                <div style="margin-top: 10px; padding: 12px; background-color: #f0f9ff;
                            border-left: 4px solid #3b82f6; border-radius: 0 6px 6px 0;
                            font-size: 14px; color: #1e3a5f; line-height: 1.6;">
                    {explanation}
                </div>
            </details>
            <details>
                <summary style="cursor: pointer; font-weight: 600; color: #7c3aed; font-size: 14px; user-select: none;">
                    ⚙️ View SQL Query
                </summary>
                <pre style="margin-top: 10px; padding: 14px; background-color: #1e1e2e;
                            color: #cdd6f4; border-radius: 6px; font-size: 13px;
                            overflow-x: auto; line-height: 1.5; white-space: pre-wrap; word-break: break-word;">{sql}</pre>
            </details>
        </div>
        """
    return log_html

# ==========================================
# Gradio App Layout (Enterprise UI/UX)
# ==========================================
with gr.Blocks(theme=gr.themes.Soft(primary_hue="blue", secondary_hue="slate"), title="Nexus BI") as demo:
    
    # 1. Title & Header
    gr.HTML(
        """
        <div style="text-align: center; margin-bottom: 30px; margin-top: 10px;">
            <h1 style="font-size: 2.5em; font-weight: 800; color: #1e3a8a; margin-bottom: 5px;">🤖 Nexus BI: Multimodal SQL Analyst Agent</h1>
            <p style="font-size: 16px; color: #64748b;">Transform natural language into Enterprise SQL, visualize trends, and extract business insights instantly.</p>
        </div>
        """
    )
    
    # 2. Input Section & Buttons
    with gr.Row():
        with gr.Column(scale=5):
            user_input = gr.Textbox(
                show_label=False,
                placeholder="Ask any business question here... (e.g., 'Show monthly sales trends for 2023')",
                lines=2,
                container=False
            )
        with gr.Column(scale=1, min_width=180):
            submit_btn = gr.Button("🔍 Analyze", variant="primary", size="lg")
            clear_btn = gr.Button("🗑️ Clear", variant="secondary", size="lg")

    # 3. Quick Examples
    gr.Examples(
        examples=[
            "Show monthly sales trends for products in the 'Bikes' category during the year 2023",
            "Compare the total revenue of each Currency",
            "What are the top 3 most expensive product categories based on average price?",
            "Show the total sales for the first quarter (January to March) of 2023 grouped by product category",
            "Show me the product categories and their average prices"
        ],
        inputs=user_input,
        label="💡 Quick Examples (Click to auto-fill)"
    )

    gr.Markdown("---")

    # 4. Sub-navbar (Tabs)
    with gr.Tabs() as tabs:
        
        # Tab 1: Insight & Analysis
        with gr.TabItem("📊 Insight & Analysis", id=1):
            with gr.Row():
                with gr.Column(scale=2):
                    chart_output = gr.Plot(label="Interactive Visualization")
                with gr.Column(scale=1):
                    exec_summary = gr.Markdown("### 📝 Executive Summary\n*Results will appear here.*")
                    process_time = gr.Markdown("**⏱️ Processing time:** -")
                    
        # Tab 2: Technical Details
        with gr.TabItem("⚙️ Technical Details", id=2):
            with gr.Row():
                with gr.Column(scale=1):
                    sql_output = gr.Code(label="Generated SQL Query", language="sql")
                with gr.Column(scale=1):
                    data_output = gr.Dataframe(label="Raw Query Results", interactive=False, max_height=400)
                    
        # Tab 3: Session Logs
        with gr.TabItem("📜 Session Logs", id=3):
            gr.Markdown("### 🗂️ Previous Results History\n*View your past queries, SQL, and insights without re-running them.*")
            session_log_output = gr.HTML(format_session_log([]))

    # State variables
    history_state = gr.State([])

    # Event Listeners (Callbacks)
    # ผูกปุ่ม Analyze
    submit_btn.click(
        fn=process_request_async,
        inputs=[user_input, history_state],
        outputs=[sql_output, data_output, chart_output, exec_summary, history_state, process_time, session_log_output]
    )
    
    # ผูกปุ่ม Enter จากคีย์บอร์ด
    user_input.submit(
        fn=process_request_async,
        inputs=[user_input, history_state],
        outputs=[sql_output, data_output, chart_output, exec_summary, history_state, process_time, session_log_output]
    )

    # ผูกปุ่ม Clear
    clear_btn.click(
        lambda: ("", None, None, None, "### 📝 Executive Summary\n*Results will appear here.*", "**⏱️ Processing time:** -"),
        outputs=[user_input, sql_output, data_output, chart_output, exec_summary, process_time]
    )

if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860)