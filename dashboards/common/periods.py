# -*- coding: utf-8 -*-
"""Períodos de facturación disponibles (común a todos los sectores)."""

from sqlalchemy import text
import pandas as pd

DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"


def fetch_periodos_disponibles(engine, schema=DB_SCHEMA, tabla=TABLA_FACTURACION):
    """
    Lista de períodos con facturación, ordenados descendente (YYYY-MM-DD).

    Fuente canónica: {schema}.{tabla} (misma tabla para energía, internet, gas, etc.).
    """
    try:
        query = text(
            f"""
            SELECT periodo
            FROM {schema}.{tabla}
            WHERE periodo IS NOT NULL
            GROUP BY periodo
            ORDER BY periodo DESC
            """
        )
        df_periodos = pd.read_sql(query, engine)
        if df_periodos is None or df_periodos.empty:
            return []
        periodos = pd.to_datetime(df_periodos["periodo"], errors="coerce").dropna()
        return periodos.dt.strftime("%Y-%m-%d").tolist()
    except Exception:
        return []
