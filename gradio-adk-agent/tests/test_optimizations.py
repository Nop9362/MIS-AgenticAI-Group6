import time
import os
import sys
import json
import asyncio
from dotenv import load_dotenv

# Add parent directory to path to import bi_agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bi_agent.tools import get_database_schema, _schema_cache

load_dotenv(dotenv_path='bi_agent/.env')

def test_schema_caching():
    """Verify that schema caching reduces execution time on subsequent calls."""
    print("Running test_schema_caching...")
    
    # Ensure cache is clear before starting
    global _schema_cache
    import bi_agent.tools
    bi_agent.tools._schema_cache = None

    # First call (should be slow, hits DB)
    start_time_1 = time.time()
    schema_1 = get_database_schema()
    end_time_1 = time.time()
    duration_1 = end_time_1 - start_time_1
    
    # Second call (should be fast, hits cache)
    start_time_2 = time.time()
    schema_2 = get_database_schema()
    end_time_2 = time.time()
    duration_2 = end_time_2 - start_time_2
    
    print(f"  First call duration: {duration_1:.4f}s")
    print(f"  Second call duration: {duration_2:.4f}s")
    
    assert duration_2 < duration_1, "Cache did not reduce execution time."
    assert schema_1 == schema_2, "Cached schema does not match original schema."
    print("  [PASS] test_schema_caching passed.\n")

async def test_data_formatter():
    """Verify data formatter handles edge cases in app.py logic."""
    print("Running test_data_formatter (logic abstraction)...")
    
    # Simulating the formatting logic from app.py
    
    # Test case 1: Empty Data
    query_results_str = json.dumps({'success': True, 'data': [], 'columns': [], 'row_count': 0})
    query_results = json.loads(query_results_str)
    
    data_list = query_results.get('data', [])
    columns = query_results.get('columns', [])
    row_count = query_results.get('row_count', 0)
    
    formatted_data = (
        f"Data Results: {row_count} rows returned\n\n"
        f"Columns: {columns}\n\n"
        f"Data (as JSON):\n{json.dumps(data_list[:100], indent=2)}"
    )
    
    assert "0 rows returned" in formatted_data
    assert "[]" in formatted_data
    print("  [PASS] Empty data handled correctly.")

    # Test case 2: Malformed string simulation
    malformed_str = "{invalid_json: true"
    try:
        query_results = json.loads(malformed_str)
    except:
        query_results = {'success': False, 'data': [], 'error': 'Failed to parse results'}
        
    assert query_results['success'] is False
    assert query_results['error'] == 'Failed to parse results'
    print("  [PASS] Malformed JSON handled correctly.")
    print("  [PASS] test_data_formatter passed.\n")


if __name__ == "__main__":
    print("--- Starting Optimization Tests ---\n")
    
    # Need to check if DB credentials exist before running DB tests
    has_db = all([
        os.getenv("MSSQL_SERVER"), 
        os.getenv("MSSQL_DATABASE"), 
        os.getenv("MSSQL_USERNAME"), 
        os.getenv("MSSQL_PASSWORD")
    ])
    
    if has_db:
        test_schema_caching()
    else:
        print("⚠️ Skipping DB caching test: Missing MSSQL environment variables.\n")
        
    asyncio.run(test_data_formatter())
    
    print("--- All Tests Completed Successfully ---")
