"""
Database configuration and connection management for SQL Server.

This module provides utilities for connecting to Microsoft SQL Server.
The schema extraction has been optimized into a Dense String to save LLM tokens.
"""

import urllib.parse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

def create_db_engine(server: str, database: str, username: str, password: str, driver: str = "ODBC Driver 18 for SQL Server") -> Engine:
    """
    Create a SQLAlchemy engine for MS SQL Server connection.
    """
    odbc_string = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
    )
    params = urllib.parse.quote_plus(odbc_string)
    connection_string = f"mssql+pyodbc:///?odbc_connect={params}"
    
    return create_engine(connection_string, echo=False)


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
    Return a highly dense, token-optimized representation of the database schema.
    Bypasses dynamic DB querying to save ~1,500+ tokens per request, while enforcing strict rules.
    """
    dense_schema = """
=== CRITICAL DATABASE SCHEMA & STRICT BUSINESS RULES ===
(Copy column names EXACTLY. NEVER invent tables or columns)

[1. DIMENSION TABLES (Descriptive Data)]
- dbo.Dim_Product -> columns: ID_Product (PK), Material_Description (Product Name), Material_Number, Product_Category (Text, e.g. Bikes), Product_Line, Transfer_Price_EUR (Unit cost), Product_Price_EUR, Price_Segment, Days_for_Shipping
  🚨 RULE: NO Dim_Product_Category table exists. To group by category, use `p.Product_Category` directly from Dim_Product.
- dbo.Dim_Calendar_Month -> columns: ID_Calendar_Month, Calendar_Month_ISO (YYYY-MM), Calendar_Month_Name (e.g. January), Calendar_Month_Number (1-12), Calendar_Year, Calendar_Quarter
  🚨 RULE: NEVER apply YEAR()/MONTH() to ID_Calendar_Month. JOIN this table and use Calendar_Year instead.
- dbo.Dim_Sales_Office -> columns: ID_Sales_Office, Sales_Office, Local_Currency, Sales_Region, Sales_Country, Global_Region
- dbo.Dim_Currency -> columns: ID_Currency, Currency_Name

[2. FACT TABLES (Metrics & Foreign Keys)]
- dbo.Facts_Monthly_Sales -> columns: ID_Calendar_Month, ID_Currency, ID_Product, ID_Sales_Channel, ID_Sales_Office, Discount, Revenue (Sales Revenue), Sales_Amount (Units sold), Transfer_Price
  🚨 RULE: Contains ONLY numeric metrics and FKs. ALWAYS JOIN Dim_ tables to get names. DO NOT add _EUR to Revenue.
- dbo.Facts_Daily_Sales -> columns: ID_Order_Date, ID_Shipping_Date, ID_Currency, ID_Product, ID_Sales_Channel, ID_Sales_Office, Revenue, Discount, Sales_Amount
- dbo.Facts_Monthly_Sales_Quota -> columns: ID_Calendar_Month, ID_Product_Category, Revenue_Quota, Sales_Amount_Quota

[3. PRE-JOINED FLAT TABLES (For Simple Queries)]
- dbo.DataSet_Monthly_Sales -> columns: Calendar_Year, Calendar_Quarter, Calendar_Month_ISO, Product_Category, Product_Line, Material_Description, Revenue, Revenue_EUR, Discount, Sales_Amount, Transfer_Price_EUR
  🚨 RULE: EASIEST for simple queries. If user just asks for "Revenue by Category", use this table instead of doing JOINs!
- dbo.DataSet_Monthly_Sales_and_Quota -> Note: has spaces in column names, MUST use [brackets] like [Sales Amount Quota]

[4. STANDARD CALCULATED METRICS (Business Logic Layer)]
🚨 USE THESE FORMULAS when asked. DO NOT invent your own math.
- Total Cost = SUM(f.Transfer_Price * f.Sales_Amount)
- Gross Profit = SUM(f.Revenue) - SUM(f.Transfer_Price * f.Sales_Amount)
- Gross Margin (%) = (SUM(f.Revenue) - SUM(f.Transfer_Price * f.Sales_Amount)) / NULLIF(SUM(f.Revenue), 0) * 100
- Average Selling Price (ASP) = SUM(f.Revenue) / NULLIF(SUM(f.Sales_Amount), 0)
- Return on Investment (ROI) = (SUM(f.Revenue) - Total Cost) / NULLIF(Total Cost, 0) * 100

[5. RELATIONSHIPS (How to JOIN)]
- f.ID_Product = p.ID_Product (Facts to Dim_Product)
- f.ID_Calendar_Month = d.ID_Calendar_Month (Facts to Dim_Calendar_Month)
- f.ID_Sales_Office = s.ID_Sales_Office (Facts to Dim_Sales_Office)
- f.ID_Currency = c.ID_Currency (Facts to Dim_Currency)
"""
    return dense_schema