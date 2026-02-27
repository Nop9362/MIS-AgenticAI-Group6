import gradio as gr
import asyncio
import pandas as pd
import altair as alt
import json
import time
import sqlparse  # <--- 🌟 1. เพิ่มบรรทัดนี้
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
        sample_data = df.head(5).to_json(orient='records') 
        summary_stats = df.describe(include='all').to_json()
    else:
        sample_data, summary_stats = "[]", "{}"
    
    formatted_data = f"Data Results: {row_count} rows returned\n\nColumns: {columns}\n\nSample Data:\n{sample_data}\n\nStats:\n{summary_stats}"
    content_insight = types.Content(role='user', parts=[types.Part(text=f"Please analyze these query results:\n\n{formatted_data}")])
    
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
# Gradio UI Functions (With Progress & History)
# ==========================================
async def process_request_async(message: str, history_state: list, progress=gr.Progress()):
    start_time = time.time()
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        if not message.strip():
            return "Error: Empty input", None, None, "No question provided", history_state, "⏱️ Error: Empty input"

        progress(0.2, desc="🧠 Analyzing schema & Generating SQL...")
        results = await run_bi_pipeline_async(message)
        
        raw_sql_query = results.get('sql_query', '') # รับ SQL ดิบมา
        
        # 🌟 2. จัดฟอร์แมต SQL ให้อ่านง่าย มีการย่อหน้าและปัดบรรทัด
        if raw_sql_query and not raw_sql_query.startswith("--"):
            formatted_sql = sqlparse.format(raw_sql_query, reindent=True, keyword_case='upper')
        else:
            formatted_sql = raw_sql_query
            
        query_results_str = results.get('query_results', '{}')
        
        progress(0.5, desc="🗄️ Executing query on MS SQL Server...")
        try: query_results = json.loads(query_results_str) if isinstance(query_results_str, str) else query_results_str
        except: query_results = {'success': False, 'data': [], 'error': 'Failed to parse'}

        if not query_results.get('success', False):
            error_msg = query_results.get('error', 'Unknown error')
            exec_time = round(time.time() - start_time, 2)
            history_state.insert(0, [timestamp, message, f"{exec_time}s", "❌ SQL Error"])
            # ส่ง formatted_sql กลับไปตอน Error ด้วย
            return f"-- Error\n{formatted_sql}", None, None, f"Error: {error_msg}", history_state, f"⏱️ Failed in {exec_time}s"

        progress(0.8, desc="📊 Designing visualizations & Extracting insights...")
        data_list = query_results.get('data', [])
        df = pd.DataFrame(data_list)
        
        chart_spec = results.get('chart_spec', '')
        explanation_text = results.get('explanation_text', '')

        chart = None
        if chart_spec:
            try:
                import re
                match = re.search(r'```(?:python)?(.*?)```', chart_spec, re.DOTALL)
                chart_spec_clean = match.group(1).strip() if match else chart_spec.replace("```python", "").replace("```", "").strip()
                namespace = {'alt': alt, 'pd': pd, 'df': df.copy()}
                exec(chart_spec_clean, namespace)
                if 'chart' in namespace:
                    chart = namespace['chart']
                    chart.to_dict() 
            except Exception as e:
                error_df = pd.DataFrame({'error': [f"Viz Error: {str(e)}"]})
                chart = alt.Chart(error_df).mark_text(size=14, color='#ff6b6b').encode(text='error').properties(width=500, height=300)

        exec_time = round(time.time() - start_time, 2)
        history_state.insert(0, [timestamp, message, f"{exec_time}s", "✅ Success"])
        
        progress(1.0, desc="✨ Done!")
        
        # 🌟 3. เปลี่ยนจากคืนค่า raw_sql_query เป็น formatted_sql
        return formatted_sql, df, chart, explanation_text, history_state, f"⏱️ Completed in **{exec_time}s**"

    except Exception as e:
        exec_time = round(time.time() - start_time, 2)
        history_state.insert(0, [timestamp, message, f"{exec_time}s", "❌ Sys Error"])
        return f"Error: {str(e)}", None, None, f"Error: {str(e)}", history_state, f"⏱️ System Error in {exec_time}s"

# ==========================================
# Gradio UI Layout (Upgraded UX)
# ==========================================
CUSTOM_CSS = """
body { background: linear-gradient(135deg, #1e1e2f 0%, #11111a 100%); color: #e0e0e0; font-family: 'Inter', sans-serif; }
.gradio-container { background: transparent !important; }
.gr-box, .gr-panel, .gr-form, .gr-block { background: rgba(40, 42, 54, 0.6) !important; backdrop-filter: blur(12px) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; border-radius: 16px !important; }
.gr-button-primary { background: linear-gradient(90deg, #6366f1 0%, #8b5cf6 100%) !important; border: none !important; color: white !important; font-weight: 600 !important; }
"""

with gr.Blocks(title="NextGen BI Dashboard", css=CUSTOM_CSS) as demo:
    history_state = gr.State([]) # เก็บประวัติคำถาม
    
    gr.HTML("<div style='text-align: center; margin-bottom: 2rem;'><h1 style='font-size: 2.5rem; background: linear-gradient(90deg, #6366f1, #8b5cf6); -webkit-background-clip: text; -webkit-text-fill-color: transparent;'>Nexus Business Intelligence</h1><p style='color: #9ca3af;'>AI-Powered Data Analysis Workspace</p></div>")

    with gr.Row():
        user_input = gr.Textbox(label="What would you like to know about your data?", placeholder="e.g., 'What are the top 10 products by price?'", lines=2, scale=4)
        with gr.Column(scale=1, min_width=150):
            submit_btn = gr.Button("✨ Analyze", variant="primary")
            clear_btn = gr.Button("🗑️ Clear", variant="secondary")

    # ปรับ Layout ใหม่ แบ่งเป็น 2 คอลัมน์ (เนื้อหาหลัก 3 ส่วน / ประวัติ 1 ส่วน)
    with gr.Row():
        with gr.Column(scale=3):
            with gr.Tabs():
                with gr.TabItem("📊 Insights & Analysis"):
                    with gr.Row():
                        with gr.Column(scale=2):
                            chart_output = gr.Plot(label="Interactive Visualization")
                        with gr.Column(scale=1):
                            status_output = gr.Markdown("⏱️ Ready to analyze")
                            gr.Markdown("### Executive Summary")
                            explanation_output = gr.Markdown(value="*Waiting for input...*")

                with gr.TabItem("⚙️ Technical Details"):
                    with gr.Row():
                        with gr.Column(scale=1):
                            sql_output = gr.Code(label="Generated SQL Query", language="sql")
                        with gr.Column(scale=1):
                            data_output = gr.DataFrame(label="Raw Query Results", wrap=True)
                            
        with gr.Column(scale=1):
            gr.Markdown("### 📜 Session Logs")
            history_table = gr.Dataframe(
                headers=["Time", "Query", "Duration", "Status"], 
                interactive=False, 
                wrap=True,
                value=[]
            )

    # เราใช้ .click() เรียกฟังก์ชัน Async ได้โดยตรงเลย (Gradio 4+ รองรับ)
    submit_btn.click(
        fn=process_request_async, 
        inputs=[user_input, history_state], 
        outputs=[sql_output, data_output, chart_output, explanation_output, history_state, status_output]
    ).then(
        fn=lambda h: h, # อัปเดตตารางประวัติบนหน้า UI
        inputs=history_state, 
        outputs=history_table
    )
    
    clear_btn.click(
        fn=lambda: ("", "-- Waiting for input...", None, None, "*Waiting for input...*", "⏱️ Ready to analyze"), 
        inputs=None, 
        outputs=[user_input, sql_output, data_output, chart_output, explanation_output, status_output]
    )
    
    gr.Examples(
        examples=[
            ["What are the top 10 products by transfer price?"],
            ["Show me the product categories and their average prices"],
            ["List all products in the Bikes category"],
            ["How many products are there in each category?"],
            ["What is the most expensive product?"],
        ],
        inputs=user_input
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Monochrome(text_size=gr.themes.sizes.text_md))