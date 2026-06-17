# -*- coding: utf-8 -*-
"""
Database connection manager for MySQL - Shared core module.
Provides reusable functions for all sectors.
"""

import os
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
import pymysql

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def get_db_config():
    """
    Gets DB configuration from environment variables.
    
    Returns:
        dict: Configuration with 'db_url' or with manual connection (host, port, user, password, database)
    """
    db_url = os.getenv('DB_URL')
    if db_url:
        return {'db_url': db_url}

    required_env_vars = ['DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME']
    missing_env_vars = [var_name for var_name in required_env_vars if not os.getenv(var_name)]
    if missing_env_vars:
        raise RuntimeError(
            'Missing environment variables for database connection: '
            + ', '.join(missing_env_vars)
        )

    return {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME'),
    }


def build_sqlalchemy_engine(db_config):
    """
    Builds a SQLAlchemy engine from configuration.
    
    Args:
        db_config (dict): DB configuration
        
    Returns:
        Engine: SQLAlchemy engine connected
    """
    if 'db_url' in db_config:
        return create_engine(db_config['db_url'])

    return create_engine(
        URL.create(
            drivername='mysql+pymysql',
            username=db_config['user'],
            password=db_config['password'],
            host=db_config['host'],
            port=db_config['port'],
            database=db_config['database'],
        )
    )


def inyectar_a_mysql(df, table_name):
    """
    Injects data into a MySQL table.
    
    Args:
        df (pd.DataFrame): Data to inject
        table_name (str): Name of the destination table
        
    Returns:
        bool: True if successful, False if error
    """
    try:
        db_config = get_db_config()
        engine = build_sqlalchemy_engine(db_config)
        
        df.to_sql(name=table_name, con=engine, if_exists='append', index=False)
        
        print(f"Data successfully injected into table '{table_name}'!")
        return True
    except Exception as e:
        print(f"Error injecting data into MySQL: {e}")
        return False


def obtener_maestro_conceptos():
    """
    Gets the Conceptos_Maestro table from the database.
    
    Returns:
        pd.DataFrame: Master concepts table, or None if error
    """
    try:
        db_config = get_db_config()

        if 'db_url' in db_config:
            engine = build_sqlalchemy_engine(db_config)
            query = "SELECT * FROM Conceptos_Maestro"
            df_maestro = pd.read_sql(query, engine)
            print("Connected to database using SQLAlchemy (DB_URL).")
            return df_maestro

        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            port=db_config['port'],
        )
        print("Connected to database using pymysql.")
        
        query = "SELECT * FROM Conceptos_Maestro"
        df_maestro = pd.read_sql(query, conn)
        conn.close()
        return df_maestro
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None


def execute_query(query, fetch=True):
    """
    Executes a direct SQL query on the database.
    
    Args:
        query (str): SQL query
        fetch (bool): If True, returns results; if False, only executes
        
    Returns:
        list or None: Query results if fetch=True
    """
    try:
        db_config = get_db_config()
        
        if 'db_url' in db_config:
            engine = build_sqlalchemy_engine(db_config)
            if fetch:
                return pd.read_sql(query, engine)
            else:
                with engine.connect() as connection:
                    connection.execute(query)
                    connection.commit()
                return True
        
        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            port=db_config['port'],
        )
        
        if fetch:
            result = pd.read_sql(query, conn)
            conn.close()
            return result
        else:
            cursor = conn.cursor()
            cursor.execute(query)
            conn.commit()
            conn.close()
            return True
            
    except Exception as e:
        print(f"Error executing query: {e}")
        return None
