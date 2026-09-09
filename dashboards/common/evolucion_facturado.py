# -*- coding: utf-8 -*-
"""Evolución de Total Facturado por período (común a todos los sectores)."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
from sqlalchemy import bindparam, text

DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"


def fetch_totales_por_periodos(engine, periodos, servicio):
    """
    Totales facturados por período para un servicio de facturacion_conceptos.

    Returns:
        DataFrame con columnas ``periodo`` (YYYY-MM-DD) y ``total_facturado``.
    """
    cols = ["periodo", "total_facturado"]
    if not periodos or not servicio:
        return pd.DataFrame(columns=cols)

    try:
        query = text(
            f"""
            SELECT
                periodo,
                COALESCE(SUM(total), 0) AS total_facturado
            FROM {DB_SCHEMA}.{TABLA_FACTURACION}
            WHERE periodo IN :periodos
              AND servicio = :servicio
            GROUP BY periodo
            ORDER BY periodo
            """
        ).bindparams(bindparam("periodos", expanding=True))
        df = pd.read_sql(
            query,
            engine,
            params={"periodos": list(periodos), "servicio": servicio},
        )
    except Exception:
        return pd.DataFrame(columns=cols)

    if df is None or df.empty:
        ordered = pd.DataFrame({"periodo": list(periodos), "total_facturado": 0.0})
        return ordered[cols]

    df["periodo"] = pd.to_datetime(df["periodo"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["total_facturado"] = pd.to_numeric(df["total_facturado"], errors="coerce").fillna(0.0)
    ordered = pd.DataFrame({"periodo": list(periodos)})
    df = ordered.merge(df, on="periodo", how="left")
    df["total_facturado"] = df["total_facturado"].fillna(0.0)
    return df[cols]


def historico_desde_getter(periodos, getter):
    """Arma el histórico llamando ``getter(periodo)`` por cada período (útil con SPs cacheados)."""
    cols = ["periodo", "total_facturado"]
    if not periodos:
        return pd.DataFrame(columns=cols)
    rows = []
    for periodo in periodos:
        try:
            valor = float(getter(periodo) or 0)
        except (TypeError, ValueError):
            valor = 0.0
        rows.append({"periodo": periodo, "total_facturado": valor})
    return pd.DataFrame(rows)[cols]


def build_evolucion_total_fig(df, height=360):
    """Gráfico de líneas: X=periodo (MM-YYYY), Y=total_facturado."""
    plot_df = df.copy()
    plot_df["periodo_label"] = pd.to_datetime(plot_df["periodo"], errors="coerce").dt.strftime("%m-%Y")
    plot_df = plot_df.dropna(subset=["periodo_label"])

    fig = px.line(
        plot_df,
        x="periodo_label",
        y="total_facturado",
        markers=True,
        labels={
            "periodo_label": "Período",
            "total_facturado": "Total Facturado ($)",
        },
    )
    fig.update_traces(
        line=dict(width=2.5),
        marker=dict(size=8),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Total Facturado: $%{y:,.0f}<extra></extra>"
        ),
    )
    fig.update_layout(
        height=height,
        margin=dict(t=8, b=40, l=0, r=0),
        xaxis_title="Período",
        yaxis_title="Total Facturado ($)",
        yaxis=dict(tickprefix="$", tickformat=",.0f"),
        showlegend=False,
    )
    return fig
