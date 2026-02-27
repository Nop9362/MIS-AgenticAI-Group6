"""
Database configuration and connection management for SQL Server.

This module provides utilities for connecting to Microsoft SQL Server
and retrieving schema information for the LLM context.
"""

import urllib.parse
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import Engine

# ============================================================================
# Semantic Layer (Business Metadata) - Optimized for Token Efficiency
# ============================================================================
BUSINESS_METADATA = {
    # -----------------------------------------------------
    # 🏢 TABLE LEVEL (กระชับ เน้นบังคับ JOIN)
    # -----------------------------------------------------
    "Facts_Monthly_Sales": "Monthly sales facts. ALWAYS JOIN Dim_ tables for text/dates.",
    "Facts_Daily_Sales": "Daily sales facts. ALWAYS JOIN Dim_ tables.",
    "Facts_Monthly_Sales_Quota": "Sales targets/quotas.",
    "Dim_Product": "Product details. Use 'Material_Description' for Product Name.",
    "Dim_Calendar_Month": "Monthly calendar (Year, Month Name). JOIN via ID_Calendar_Month.",
    "Dim_Calendar": "Daily calendar. JOIN via ID_Calendar or ID_Order_Date.",
    "Dim_Sales_Office": "Geography (Country, Region, Office).",
    "Dim_Currency": "Currency names/codes.",

    # -----------------------------------------------------
    # 📊 COLUMN LEVEL (กระชับ ดักทางคำศัพท์ที่ AI ชอบมโน)
    # -----------------------------------------------------
    # Metrics
    "Revenue": "Actual sales revenue. DO NOT add _EUR suffix.",
    "Revenue_Quota": "Target/Goal revenue.",
    "Sales_Amount": "Quantity / Units sold.",
    "Transfer_Price": "Cost price (in Facts_ tables).",
    "Transfer_Price_EUR": "Cost price (in Dim_Product table).",
    
    # Identifiers & Keys
    "ID_Calendar_Month": "Date key. DO NOT use YEAR()/MONTH(). JOIN Dim_Calendar_Month.",
    "ID_Order_Date": "Date key. JOIN Dim_Calendar.",
    
    # Descriptive Columns
    "Material_Description": "Product Name.",
    "Sales_Country": "Country name.",
    "Calendar_Year": "Year (e.g., 2023).",
    "Calendar_Month_Name": "Month Name (e.g., January)."
}

def create_db_engine(server: str, database: str, username: str, password: str, driver: str = "ODBC Driver 18 for SQL Server") -> Engine:
    """
    Create a SQLAlchemy engine for MS SQL Server connection.
    """
    # Build ODBC connection string (not URL-encoded yet)
    odbc_string = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
    )

    # URL-encode the entire ODBC connection string
    params = urllib.parse.quote_plus(odbc_string)

    # Build SQLAlchemy connection URL
    connection_string = f"mssql+pyodbc:///?odbc_connect={params}"

    # Create engine
    engine = create_engine(connection_string, echo=False)

    return engine


def validate_connection(engine: Engine) -> tuple[bool, str]:
    """
    Validate database connection.
    """
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT @@VERSION AS version"))
            version = result.scalar()
            return True, f"Connected successfully. SQL Server version: {version[:50]}..."
    except Exception as e:
        return False, f"Connection failed: {str(e)}"


def get_schema_info(engine: Engine, limit_tables: list[str] = None, max_tables: int = 20) -> str:
    """
    Retrieve database schema AND Relationships formatted for LLM context.
    """
    try:
        with engine.connect() as connection:
            # 1. Query to get table and column information
            query_columns = text("""
                SELECT 
                    t.TABLE_SCHEMA, 
                    t.TABLE_NAME, 
                    c.COLUMN_NAME, 
                    c.DATA_TYPE, 
                    c.IS_NULLABLE
                FROM INFORMATION_SCHEMA.TABLES t
                INNER JOIN INFORMATION_SCHEMA.COLUMNS c 
                    ON t.TABLE_SCHEMA = c.TABLE_SCHEMA 
                    AND t.TABLE_NAME = c.TABLE_NAME
                WHERE t.TABLE_TYPE = 'BASE TABLE'
                ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME, c.ORDINAL_POSITION
            """)
            
            # 2. Query to get Foreign Key Relationships
            query_fks = text("""
                SELECT 
                    SCHEMA_NAME(tp.schema_id) + '.' + tp.name AS ParentTable,
                    cp.name AS ParentColumn,
                    SCHEMA_NAME(tr.schema_id) + '.' + tr.name AS ReferencedTable,
                    cr.name AS ReferencedColumn
                FROM sys.foreign_keys fk
                INNER JOIN sys.tables tp ON fk.parent_object_id = tp.object_id
                INNER JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
                INNER JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
                INNER JOIN sys.tables tr ON fk.referenced_object_id = tr.object_id
                INNER JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
            """)

            # Fetch Data
            result_cols = connection.execute(query_columns)
            rows_cols = result_cols.fetchall()
            
            result_fks = connection.execute(query_fks)
            rows_fks = result_fks.fetchall()

            # Organize columns by table
            tables = {}
            for row in rows_cols:
                schema_name = row[0]
                table_name = row[1]
                full_table_name = f"{schema_name}.{table_name}"

                if limit_tables and full_table_name not in limit_tables:
                    continue

                if full_table_name not in tables:
                    tables[full_table_name] = []

                column_info = {
                    'name': row[2],
                    'type': row[3],
                    'nullable': row[4]
                }
                tables[full_table_name].append(column_info)

            # Format Schema Output with Metadata
            table_names = list(tables.keys())[:max_tables]
            schema_text = "Database Schema with Business Metadata:\n\n"

            for table_name in table_names:
                # 1. แทรกคำอธิบายตาราง (ถ้ามี)
                clean_table_name = table_name.split('.')[-1] # ตัด dbo. ออก
                table_desc = BUSINESS_METADATA.get(clean_table_name, "")
                if table_desc:
                    schema_text += f"Table: {table_name}  -- 📝 {table_desc}\nColumns:\n"
                else:
                    schema_text += f"Table: {table_name}\nColumns:\n"

                # 2. แทรกคำอธิบายคอลัมน์ (ถ้ามี)
                for col in tables[table_name]:
                    col_name = col['name']
                    col_desc = BUSINESS_METADATA.get(col_name, "")
                    nullable = "NULL" if col['nullable'] == 'YES' else "NOT NULL"
                    
                    if col_desc:
                        schema_text += f"  - {col_name} ({col['type']}, {nullable}) -- 📝 Context: {col_desc}\n"
                    else:
                        schema_text += f"  - {col_name} ({col['type']}, {nullable})\n"
                schema_text += "\n"

            return schema_text

    except Exception as e:
        return f"Error retrieving schema: {str(e)}"