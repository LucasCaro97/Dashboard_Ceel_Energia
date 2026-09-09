import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.io as pio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from html import escape
import json
import os

from common.periods import fetch_periodos_disponibles
from common.empty_state import NO_RECORDS_MESSAGE, sin_datos_periodo
from common.evolucion_facturado import build_evolucion_total_fig, fetch_totales_por_periodos

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# 1. Configuración de página
st.set_page_config(page_title="Dashboard Agua - CEEL", layout="wide")

# 2. Constantes
TOP_N_TARIFAS_DEFAULT = 6
MESES_EVOLUCION_DEFAULT = 6
MESES_EVOLUCION_MIN = 2
MESES_EVOLUCION_MAX = 12
DIST_CHART_HEIGHT = 360
PIE_DOMAIN_X = (0.0, 0.58)
PIE_DOMAIN_Y = (0.02, 0.98)
PIE_CENTER_X = (PIE_DOMAIN_X[0] + PIE_DOMAIN_X[1]) / 2

# `facturacion_conceptos.servicio` debe coincidir con `servicios.nombre_servicio`
SERVICIO_FC = "Agua Potable"

# Para consultar conceptos por servicio usamos LOWER(s.nombre_servicio)
SERVICIO_CONCEPT_QUERY = "agua potable"
TARIFA_SIN_ASIGNAR = "Sin Tarifa Asignada"
MENSAJE_CORRELACION_SIN_TARIFAS = (
    "La correlación Consumo m³ vs Costo Unitario requiere tarifas cargadas "
    "(socio_historial_tarifas y tarifas_base para servicio Agua). "
    "Cuando estén disponibles, podrá filtrar por tarifa y ver el scatter por factura."
)


# 3. Motor de conexión
if load_dotenv is not None:
    load_dotenv()

db_url = os.getenv("DB_URL")

if db_url:
    engine = create_engine(db_url)
else:
    required_env_vars = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    missing_env_vars = [v for v in required_env_vars if not os.getenv(v)]
    if missing_env_vars:
        st.error(
            "Faltan variables de entorno para conectar a la base de datos: "
            + ", ".join(missing_env_vars)
        )
        st.info("Cree un archivo .env basado en .env.example y vuelva a ejecutar la app.")
        st.stop()
    engine = create_engine(
        URL.create(
            drivername="mysql+pymysql",
            username=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.getenv("DB_NAME"),
        )
    )


# 4. Helpers
def to_periodo_sql(value):
    text_value = str(value).strip()
    if len(text_value) == 10 and text_value[4] == "-" and text_value[7] == "-":
        return text_value
    return pd.to_datetime(text_value, dayfirst=True).strftime("%Y-%m-%d")


def get_periodos_evolucion(periodos, periodo_fin, cantidad_meses):
    """Devuelve los últimos N períodos disponibles hasta el período seleccionado."""
    if not periodos:
        return []

    periodo_fin_ts = pd.to_datetime(periodo_fin, errors="coerce")
    periodos_dt = (
        pd.Series(periodos)
        .map(lambda p: pd.to_datetime(to_periodo_sql(p), errors="coerce"))
        .dropna()
        .drop_duplicates()
        .sort_values()
    )

    if pd.notna(periodo_fin_ts):
        periodos_dt = periodos_dt[periodos_dt <= periodo_fin_ts]

    return [p.strftime("%Y-%m-%d") for p in periodos_dt.tail(int(cantidad_meses))]


# 5. Funciones de carga (cacheadas)
@st.cache_data
def get_periodos_disponibles():
    try:
        query = text(
            """
            SELECT periodo
            FROM conecciones_energia.facturacion_conceptos
            WHERE periodo IS NOT NULL
              AND servicio = :servicio
            GROUP BY periodo
            ORDER BY periodo DESC
            """
        )
        df_periodos = pd.read_sql(query, engine, params={"servicio": SERVICIO_FC})
        if df_periodos is None or df_periodos.empty:
            return []
        periodos = pd.to_datetime(df_periodos["periodo"], errors="coerce").dropna()
        return periodos.dt.strftime("%Y-%m-%d").tolist()
    except Exception:
        return fetch_periodos_disponibles(engine)


@st.cache_data
def get_total_facturado(periodo):
    """Total facturado del período para Agua Potable."""
    if not periodo:
        return 0.0
    try:
        df = pd.read_sql(
            text(
                """
                SELECT COALESCE(SUM(total), 0) AS total_facturado
                FROM conecciones_energia.facturacion_conceptos
                WHERE periodo = :periodo
                  AND servicio = :servicio
                """
            ),
            engine,
            params={"periodo": periodo, "servicio": SERVICIO_FC},
        )
        val = pd.to_numeric(df.iloc[0]["total_facturado"], errors="coerce")
        return float(val) if pd.notna(val) else 0.0
    except Exception:
        return 0.0


@st.cache_data
def get_cantidad_facturas(periodo):
    """Cantidad de facturas emitidas en el período."""
    if not periodo:
        return 0
    try:
        df = pd.read_sql(
            text(
                """
                SELECT COUNT(DISTINCT nro_factura) AS total_facturas
                FROM conecciones_energia.facturacion_conceptos
                WHERE periodo = :periodo
                  AND servicio = :servicio
                  AND COALESCE(nro_factura, '') <> ''
                """
            ),
            engine,
            params={"periodo": periodo, "servicio": SERVICIO_FC},
        )
        val = pd.to_numeric(df.iloc[0]["total_facturas"], errors="coerce")
        return int(val) if pd.notna(val) else 0
    except Exception:
        return 0


@st.cache_data
def get_consumo_m3_distribuidos(periodo):
    """
    Volumen total de agua facturado en el período (m³).
    Suma cantidad_cons de conceptos escalonados o de consumo total medido.
    """
    if not periodo:
        return 0.0
    try:
        df = pd.read_sql(
            text(
                """
                SELECT COALESCE(SUM(fc.cantidad_cons), 0) AS consumo_m3
                FROM conecciones_energia.facturacion_conceptos fc
                JOIN conecciones_energia.conceptos_maestro cm
                  ON fc.id_concepto = cm.id_concepto
                JOIN conecciones_energia.servicios s
                  ON s.id_servicio = CAST(cm.servicio AS UNSIGNED)
                 AND fc.servicio = s.nombre_servicio
                WHERE fc.periodo = :periodo
                  AND fc.servicio = :servicio
                  AND (cm.es_consumo_escalonado = 1 OR cm.es_consumo_total = 1)
                """
            ),
            engine,
            params={"periodo": periodo, "servicio": SERVICIO_FC},
        )
        val = pd.to_numeric(df.iloc[0]["consumo_m3"], errors="coerce")
        return float(val) if pd.notna(val) else 0.0
    except Exception:
        return 0.0


@st.cache_data
def get_conceptos_servicio_nombres():
    """
    Conceptos de agua de consumo (escalonado o total medido).
    Usado para excluir IVA/percepciones/etc en ranking/barras.
    """
    try:
        df = pd.read_sql(
            text(
                """
                SELECT cm.nombre_concepto
                FROM conecciones_energia.conceptos_maestro cm
                JOIN conecciones_energia.servicios s
                  ON s.id_servicio = CAST(cm.servicio AS UNSIGNED)
                WHERE LOWER(s.nombre_servicio) = :sector
                  AND (cm.es_consumo_escalonado = 1 OR cm.es_consumo_total = 1)
                """
            ),
            engine,
            params={"sector": SERVICIO_CONCEPT_QUERY},
        )
        if df is None or df.empty:
            return frozenset()
        nombres = (
            df["nombre_concepto"]
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .tolist()
        )
        return frozenset(nombres)
    except Exception:
        return frozenset()


def _filtrar_conceptos_servicio(df):
    if df is None or df.empty or "nombre_concepto" not in df.columns:
        return pd.DataFrame(columns=["nombre_concepto", "cantidad", "total"])
    allowed = get_conceptos_servicio_nombres()
    if not allowed:
        return df.iloc[0:0].copy()
    return df[df["nombre_concepto"].isin(allowed)].copy()


@st.cache_data
def get_ranking_servicios_por_periodo(periodo):
    """Ranking de conceptos de agua facturados por período (sin impuestos)."""
    cols = ["nombre_concepto", "cantidad", "total"]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("CALL sp_ranking_servicios_por_periodo(:periodo, :sector)"),
                {"periodo": periodo, "sector": SERVICIO_FC},
            )
            rows = result.fetchall()
            sp_cols = list(result.keys())

        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=sp_cols)
        df["nombre_concepto"] = (
            df["nombre_concepto"].astype(str).str.strip().replace("", "Sin Concepto")
        )
        df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0).astype(int)
        df["total"] = pd.to_numeric(df["total"], errors="coerce").fillna(0.0)
        df = _filtrar_conceptos_servicio(df)
        return df[df["total"] > 0].sort_values("total", ascending=False).reset_index(drop=True)[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


def _tarifa_es_valida(tarifa) -> bool:
    texto = str(tarifa).strip().lower()
    return bool(texto) and not texto.startswith("sin ")


@st.cache_data
def hay_tarifas_para_correlacion(periodo):
    """True si hay al menos una tarifa real (no placeholder) en el período."""
    df = get_facturacion_por_tarifa(periodo)
    if df.empty:
        return False
    return df["tarifa_aplicada"].apply(_tarifa_es_valida).any()


@st.cache_data
def get_detalle_correlacion_por_periodo(periodo):
    """
    Una fila por factura: consumo m³, importe neto de agua y promedio $/m³.
    Equivalente al detalle de correlación del dashboard de Energía.
    """
    cols = [
        "tarifa_aplicada",
        "nro_socio",
        "nombre_socio",
        "nro_factura",
        "consumo_m3",
        "promedio_agua_pura",
        "dinero_agua",
    ]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_sql(
            text(
                """
                SELECT
                    fc.periodo,
                    fc.nro_factura,
                    fc.nro_socio,
                    MAX(
                        COALESCE(NULLIF(TRIM(fc.nombre_socio), ''), 'Sin Nombre')
                    ) AS nombre_socio,
                    COALESCE(t.nombre_tarifa, :sin_tarifa) AS tarifa_aplicada,
                    ROUND(SUM(
                        CASE
                            WHEN cm.es_consumo_escalonado = 1
                              OR cm.es_consumo_total = 1
                            THEN fc.cantidad_cons
                            ELSE 0
                        END
                    ), 2) AS consumo_m3,
                    ROUND(SUM(
                        CASE
                            WHEN cm.es_consumo_escalonado = 1
                              OR cm.es_consumo_total = 1
                            THEN fc.total
                            ELSE 0
                        END
                    ), 2) AS dinero_agua
                FROM conecciones_energia.facturacion_conceptos fc
                JOIN conecciones_energia.servicios s
                  ON fc.servicio = s.nombre_servicio
                JOIN conecciones_energia.conceptos_maestro cm
                  ON fc.id_concepto = cm.id_concepto
                 AND s.id_servicio = CAST(cm.servicio AS UNSIGNED)
                LEFT JOIN conecciones_energia.socio_historial_tarifas sht
                  ON fc.nro_socio = sht.nro_socio
                 AND fc.periodo BETWEEN sht.fecha_desde
                     AND IFNULL(sht.fecha_hasta, '9999-12-31')
                 AND TRIM(sht.servicio_tipo) = 'Agua'
                LEFT JOIN conecciones_energia.tarifas_base t
                  ON sht.id_tarifa_base = t.id_tarifa
                WHERE fc.periodo = :periodo
                  AND fc.servicio = :servicio
                  AND s.id_servicio = 2
                  AND fc.id_concepto <> 9501
                GROUP BY fc.periodo, fc.nro_factura, fc.nro_socio, t.nombre_tarifa
                """
            ),
            engine,
            params={
                "periodo": periodo,
                "servicio": SERVICIO_FC,
                "sin_tarifa": TARIFA_SIN_ASIGNAR,
            },
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)

        df["tarifa_aplicada"] = (
            df["tarifa_aplicada"].astype(str).str.strip().replace("", TARIFA_SIN_ASIGNAR)
        )
        df["nombre_socio"] = (
            df["nombre_socio"].astype(str).str.strip().replace("", "Sin Nombre")
        )
        for col in ["consumo_m3", "dinero_agua"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        mask = df["consumo_m3"] > 0
        df["promedio_agua_pura"] = 0.0
        df.loc[mask, "promedio_agua_pura"] = (
            df.loc[mask, "dinero_agua"] / df.loc[mask, "consumo_m3"]
        )
        df["promedio_agua_pura"] = df["promedio_agua_pura"].round(4)

        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def get_facturacion_por_tarifa(periodo):
    """Distribución por tarifa desde sp_consolidado_agua_por_periodo."""
    cols = ["tarifa_aplicada", "total_facturado", "cantidad_socios"]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("CALL sp_consolidado_agua_por_periodo(:periodo)"),
                {"periodo": periodo},
            )
            rows = result.fetchall()
            sp_cols = list(result.keys())

        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=sp_cols)
        if "tarifa_aplicada" not in df.columns:
            return pd.DataFrame(columns=cols)

        df["tarifa_aplicada"] = (
            df["tarifa_aplicada"].astype(str).str.strip().replace("", TARIFA_SIN_ASIGNAR)
        )
        df["total_factura"] = pd.to_numeric(df["total_factura"], errors="coerce").fillna(0.0)

        agg = (
            df.groupby("tarifa_aplicada", as_index=False)
            .agg(
                total_facturado=("total_factura", "sum"),
                cantidad_socios=("nro_socio", "nunique"),
            )
            .sort_values("total_facturado", ascending=False)
        )
        return agg[agg["total_facturado"] > 0][cols]
    except Exception:
        return pd.DataFrame(columns=cols)


# ─── Utilidad: gráfico de torta/donut interactivo ─────────────────────────────
def _build_interactive_pie_html(df_values, label_col, value_col, palette, chart_id, hole=0.45, height=DIST_CHART_HEIGHT):
    df_plot = df_values.copy()
    df_plot["label"] = df_plot[label_col].astype(str)
    colors = [palette[i % len(palette)] for i in range(len(df_plot))]
    values = pd.to_numeric(df_plot[value_col], errors="coerce").fillna(0.0)
    total = float(values.sum())
    percents = [(float(v) / total * 100) if total else 0.0 for v in values]

    fig = px.pie(
        df_plot,
        values=value_col,
        names="label",
        hole=hole,
        color_discrete_sequence=colors,
    )
    fig.update_traces(
        textposition="inside",
        textinfo="text",
        texttemplate="%{label}<br>%{percent}",
        insidetextorientation="horizontal",
        textfont=dict(size=12),
        hoverinfo="none",
        hovertemplate="<extra></extra>",
        domain=dict(x=list(PIE_DOMAIN_X), y=list(PIE_DOMAIN_Y)),
    )
    fig.update_layout(
        showlegend=False,
        hovermode="closest",
        width=800,
        height=height,
        autosize=False,
        margin=dict(t=0, b=0, l=0, r=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )

    fig_json = pio.to_json(fig, validate=False)
    legend_items = "".join(
        f'<div class="legend-item" data-index="{i}" style="display:flex;align-items:center;gap:5px;padding:1px 0;cursor:default;opacity:1;transition:opacity 120ms ease;">'
        f'<span style="width:8px;height:8px;flex:0 0 8px;border-radius:2px;border:1px solid rgba(0,0,0,0.18);background:{colors[i]};"></span>'
        f'<span class="legend-label" style="font-size:0.72rem;line-height:1.15;">{escape(str(row["label"]))}</span>'
        f"</div>"
        for i, (_, row) in enumerate(df_plot.reset_index(drop=True).iterrows())
    )
    colors_json = json.dumps(colors)
    percents_json = json.dumps([round(p, 1) for p in percents])

    wrap_cls = f"wrap-{chart_id}"
    chart_div = f"chart-{chart_id}"
    leg_div = f"legend-{chart_id}"
    center_div = f"center-{chart_id}"
    chart_area = f"chart-area-{chart_id}"

    return fr"""
    <style>
        html,body{{
            margin:0;
            padding:0;
            background:transparent;
            overflow:hidden;
            box-sizing:border-box;
        }}
        *,*::before,*::after{{box-sizing:border-box;}}
        .{wrap_cls}{{
            position:relative;
            width:100%;
            height:{height}px;
            overflow:hidden;
        }}
        #{chart_div}{{
            width:100%;
            height:{height}px;
            max-width:100%;
            background:transparent;
            overflow:hidden;
        }}
        #{chart_div} .main-svg{{display:block;}}
        #{chart_div} .hoverlayer,#{chart_div} .hovertext{{display:none !important;pointer-events:none !important;}}
        #{leg_div}{{
            position:absolute;top:8px;right:8px;left:auto;z-index:10;display:flex;flex-direction:column;
            gap:3px;max-width:42%;padding:6px 8px;border-radius:6px;background:rgba(255,255,255,0.82);
            box-shadow:0 1px 4px rgba(0,0,0,0.05);
        }}
        .legend-item:hover{{opacity:0.7;}}
        .legend-label{{font-size:0.72rem;line-height:1.15;color:#7d7d7d;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
        .{chart_area}{{position:relative;width:100%;height:{height}px;overflow:hidden;}}
        #{center_div}{{
            position:absolute;top:50%;left:{PIE_CENTER_X * 100:.2f}%;transform:translate(-50%, -50%);
            z-index:20;pointer-events:none;text-align:center;min-width:72px;visibility:hidden;
        }}
        #{center_div}.visible{{visibility:visible;}}
        .center-pct-{chart_id}{{font-size:2.75rem;font-weight:900;line-height:1;letter-spacing:-0.02em;color:#ffffff;text-shadow:-1px -1px 0 rgba(30,30,30,0.85),1px -1px 0 rgba(30,30,30,0.85),-1px  1px 0 rgba(30,30,30,0.85),1px  1px 0 rgba(30,30,30,0.85),0 0 6px rgba(0,0,0,0.35);}}
    </style>
    <div class="{wrap_cls}">
        <div id="{leg_div}">{legend_items}</div>
        <div class="{chart_area}">
            <div id="{center_div}"></div>
            <div id="{chart_div}"></div>
        </div>
    </div>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <script>
        (function(){{
            const fig = {fig_json};
            const baseColors = {colors_json};
            const percents = {percents_json};
            const chart = document.getElementById('{chart_div}');
            const wrap = document.querySelector('.{wrap_cls}');
            const centerEl = document.getElementById('{center_div}');
            const legendItems = Array.from(document.querySelectorAll('#{leg_div} .legend-item'));
            const baseTextSize = (fig.data[0].textfont && fig.data[0].textfont.size) ? fig.data[0].textfont.size : 13;
            const hoverTextSize = baseTextSize + 3;
            const basePull = (fig.data[0].labels || []).map(() => 0);
            let resizeTimer = null;

            function resizeChart() {{
                if (!chart || !wrap || !chart.isConnected) return;
                const width = Math.floor(wrap.clientWidth);
                const height = Math.floor(wrap.clientHeight);
                if (width <= 0 || height <= 0) return;
                Plotly.relayout(chart, {{ width: width, height: height }});
            }}

            function scheduleResize() {{
                if (resizeTimer) clearTimeout(resizeTimer);
                resizeTimer = setTimeout(() => {{
                    resizeChart();
                    requestAnimationFrame(resizeChart);
                }}, 16);
            }}

            function updateCenter(idx) {{
                if (idx === null || idx === undefined) {{
                    centerEl.classList.remove('visible');
                    centerEl.innerHTML = '';
                    return;
                }}
                const pct = percents[idx];
                centerEl.innerHTML = '<div class="center-pct-{chart_id}">' + pct.toFixed(1) + '%</div>';
                centerEl.classList.add('visible');
            }}

            function hexToRgba(color, alpha) {{
                if (!color) return color;
                if (color.startsWith('#')) {{
                    const h = color.slice(1);
                    const f = h.length === 3 ? h.split('').map(c => c+c).join('') : h;
                    const n = parseInt(f, 16);
                    return `rgba(${{(n>>16)&255}},${{(n>>8)&255}},${{n&255}},${{alpha}})`;
                }}
                return color;
            }}

            function clearHighlight() {{
                Plotly.restyle(chart, {{'marker.colors':[baseColors],'pull':[basePull],'textfont.size':baseTextSize}},[0]);
                Plotly.Fx.unhover(chart);
                legendItems.forEach(el => {{ el.style.opacity='1'; el.style.fontWeight='400'; }});
                updateCenter(null);
            }}

            function setHighlight(idx) {{
                const cols = baseColors.map((c,i) => hexToRgba(c, i===idx?1:0.2));
                const pull = basePull.map((_,i) => i===idx?0.08:0);
                Plotly.restyle(chart, {{'marker.colors':[cols],'pull':[pull],'textfont.size':hoverTextSize}},[0]);
                legendItems.forEach(el => {{
                    const a = Number(el.dataset.index)===idx;
                    el.style.opacity = a?'1':'0.35';
                    el.style.fontWeight = a?'600':'400';
                }});
                updateCenter(idx);
            }}

            legendItems.forEach(el => {{
                const i = Number(el.dataset.index);
                el.addEventListener('mouseenter', () => setHighlight(i));
                el.addEventListener('mouseleave', clearHighlight);
            }});

            Plotly.newPlot(chart, fig.data, fig.layout, {{displayModeBar:false}}).then(() => {{
                chart.on('plotly_hover', ev => {{
                    if (ev && ev.points && ev.points.length) {{
                        setHighlight(ev.points[0].pointNumber);
                    }}
                }});
                chart.on('plotly_unhover', clearHighlight);
                clearHighlight();
                resizeChart();
            }});

            if (window.ResizeObserver && wrap) {{
                new ResizeObserver(scheduleResize).observe(wrap);
            }}

            if (window.IntersectionObserver) {{
                new IntersectionObserver(entries => {{
                    entries.forEach(entry => {{
                        if (entry.isIntersecting) scheduleResize();
                    }});
                }}, {{ threshold: 0.01 }}).observe(wrap || chart);
            }}

            window.addEventListener('resize', scheduleResize);
        }})();
    </script>
    """


# ─── 6. Interfaz ──────────────────────────────────────────────────────────────
_COMPACT_LAYOUT_CSS = """
<style>
    html {
        scrollbar-gutter: stable;
    }
    [data-testid="stMainBlockContainer"] {
        padding-top: 0.35rem;
        padding-left: 1.25rem;
        padding-right: 1.25rem;
        max-width: 100%;
    }
    [data-testid="stHtml"] iframe {
        display: block !important;
        width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        overflow: hidden !important;
    }
    [data-testid="stVerticalBlock"] {
        gap: 0.5rem;
    }
    div[data-testid="column"] {
        padding-left: 0.6rem;
        padding-right: 0.6rem;
    }
    [data-testid="stHorizontalBlock"] {
        gap: 0.75rem;
    }
    [data-testid="stMetric"] {
        padding: 0.1rem 0;
    }
    [data-testid="stMetricLabel"] p {
        font-size: 0.8rem;
        margin-bottom: 0.1rem;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.35rem;
    }
    [data-testid="stWidgetLabel"] p {
        font-size: 0.85rem;
        margin-bottom: 0.15rem;
    }
    .inet-filter-label {
        display: block;
        font-size: 0.78rem;
        font-weight: 500;
        line-height: 1.1;
        margin: 0 0 0.12rem 0;
        color: rgba(49, 51, 63, 0.85);
    }
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        min-height: 1.85rem !important;
        height: 1.85rem !important;
    }
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div > div {
        font-size: 0.85rem !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }
    div[data-testid="stNumberInput"] input {
        min-height: 1.85rem !important;
        height: 1.85rem !important;
        padding: 0.15rem 0.45rem !important;
        font-size: 0.85rem !important;
    }
    div[data-testid="stNumberInput"] [data-testid="stNumberInputStepDown"],
    div[data-testid="stNumberInput"] [data-testid="stNumberInputStepUp"] {
        width: 1.45rem !important;
        min-width: 1.45rem !important;
    }
    div[data-testid="stSelectbox"],
    div[data-testid="stNumberInput"] {
        margin-bottom: 0 !important;
    }
    .inet-section-title {
        display: block;
        margin: 0 0 0.55rem 0;
        padding: 0.05rem 0 0.15rem 0;
        font-size: 1rem;
        font-weight: 600;
        line-height: 1.35;
        overflow: visible;
    }
    [data-testid="stHtml"],
    [data-testid="stPlotlyChart"] {
        margin-top: 0.15rem;
    }
    [data-testid="stMarkdown"] h4 {
        margin: 0.2rem 0 0.45rem 0;
        line-height: 1.35;
        overflow: visible;
    }
</style>
"""
st.markdown(_COMPACT_LAYOUT_CSS, unsafe_allow_html=True)

st.markdown(
    "<h3 style='margin:0 0 0.35rem 0;line-height:1.2;'>💧 Dashboard de Facturación - CEEL AGUA</h3>",
    unsafe_allow_html=True,
)

periodos_sorted = get_periodos_disponibles()
if not periodos_sorted:
    periodos_sorted = ["2026-01-01"]

filt_periodo, filt_top_n, kpi_total, kpi_facturas, kpi_m3 = st.columns(
    [1.2, 0.75, 1.05, 1.05, 1.05], gap="small"
)

with filt_periodo:
    st.markdown("<span class='inet-filter-label'>Periodo</span>", unsafe_allow_html=True)
    periodo_display = st.selectbox(
        "Periodo",
        periodos_sorted,
        key="filtro_periodo",
        label_visibility="collapsed",
    )
periodo_sql = to_periodo_sql(periodo_display)

with filt_top_n:
    st.markdown("<span class='inet-filter-label'>Top N categorías</span>", unsafe_allow_html=True)
    top_n_tarifas = st.number_input(
        "Top N categorías",
        min_value=1,
        max_value=50,
        value=TOP_N_TARIFAS_DEFAULT,
        step=1,
        key="filtro_top_n",
        label_visibility="collapsed",
    )

total_facturado = get_total_facturado(periodo_sql)
sin_datos = sin_datos_periodo(total_facturado)
if sin_datos:
    cantidad_facturas = 0
    consumo_m3 = 0.0
else:
    cantidad_facturas = get_cantidad_facturas(periodo_sql)
    consumo_m3 = get_consumo_m3_distribuidos(periodo_sql)

with kpi_total:
    st.metric("Total Facturado", f"${total_facturado:,.0f}")
with kpi_facturas:
    st.metric("Facturas Emitidas", f"{cantidad_facturas:,}")
with kpi_m3:
    st.metric("m³ Total Distribuidos", f"{consumo_m3:,.0f}")

if sin_datos:
    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown("<p class='inet-section-title'>Distribución por Tarifa Usuario</p>", unsafe_allow_html=True)
        st.info(NO_RECORDS_MESSAGE)
    with right_col:
        st.markdown("<p class='inet-section-title'>Total Facturado por Conceptos de Consumo (sin impuestos)</p>", unsafe_allow_html=True)
        st.info(NO_RECORDS_MESSAGE)

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);'/>",
        unsafe_allow_html=True,
    )
    st.markdown("#### Correlación: Consumo m³ vs Costo Unitario Promedio")
    st.info(MENSAJE_CORRELACION_SIN_TARIFAS)

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);'/>",
        unsafe_allow_html=True,
    )
    st.markdown("#### Evolución de Total Facturado")
    st.info(NO_RECORDS_MESSAGE)

if not sin_datos:
    # ── Gráficos de distribución ──────────────────────────────────────────────────
    df_tarifas = get_facturacion_por_tarifa(periodo_sql)
    df_servicios = get_ranking_servicios_por_periodo(periodo_sql)

    left_col, right_col = st.columns(2)

    with left_col:
        st.markdown("<p class='inet-section-title'>Distribución por Tarifa Usuario</p>", unsafe_allow_html=True)
        if not df_tarifas.empty:
            df_chart = df_tarifas.copy()
            if len(df_chart) > top_n_tarifas:
                top_part = df_chart.head(top_n_tarifas)
                otros_val = df_chart.iloc[top_n_tarifas:]["total_facturado"].sum()
                otros_row = pd.DataFrame({"tarifa_aplicada": ["Otros"], "total_facturado": [otros_val], "cantidad_socios": [0]})
                df_chart = pd.concat([top_part, otros_row], ignore_index=True)

            html_pie = _build_interactive_pie_html(
                df_chart, "tarifa_aplicada", "total_facturado",
                px.colors.qualitative.Set3, "pie-agua", hole=0.45,
                height=DIST_CHART_HEIGHT,
            )
            components.html(html_pie, height=DIST_CHART_HEIGHT, scrolling=False)
        else:
            st.info("No hay datos de tarifas para el período seleccionado.")

    with right_col:
        st.markdown("<p class='inet-section-title'>Total Facturado por Conceptos de Consumo (sin impuestos)</p>", unsafe_allow_html=True)
        if not df_servicios.empty:
            df_bars = df_servicios.copy()
            if len(df_bars) > top_n_tarifas:
                top_part = df_bars.head(top_n_tarifas)
                otros_val = df_bars.iloc[top_n_tarifas:]["total"].sum()
                otros_cant = df_bars.iloc[top_n_tarifas:]["cantidad"].sum()
                otros_row = pd.DataFrame(
                    {"nombre_concepto": ["Otros"], "total": [otros_val], "cantidad": [otros_cant]}
                )
                df_bars = pd.concat([top_part, otros_row], ignore_index=True)

            df_bars = df_bars.sort_values("total", ascending=False)
            x_order = df_bars["nombre_concepto"].tolist()
            df_bars["nombre_concepto"] = pd.Categorical(
                df_bars["nombre_concepto"], categories=x_order, ordered=True
            )

            fig_bars = px.bar(
                df_bars,
                x="nombre_concepto",
                y="total",
                text="total",
                color="nombre_concepto",
                color_discrete_sequence=px.colors.qualitative.Set3,
                custom_data=["cantidad"],
            )
            fig_bars.update_traces(
                width=0.85,
                texttemplate="$%{text:,.0f}",
                textposition="outside",
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Total Facturado: $%{y:,.0f}<br>"
                    "Cantidad: %{customdata[0]:,.0f}<extra></extra>"
                ),
                showlegend=False,
            )
            fig_bars.update_layout(
                height=DIST_CHART_HEIGHT,
                margin=dict(t=8, b=72, l=0, r=0, pad=0),
                xaxis_title="Concepto",
                yaxis_title="Total Facturado ($)",
                xaxis={"categoryorder": "array", "categoryarray": x_order, "tickangle": -35},
                yaxis=dict(tickprefix="$", tickformat=",.0f"),
            )
            st.plotly_chart(fig_bars, width="stretch")
        else:
            st.info("No hay datos de conceptos para el período seleccionado.")

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);'/>",
        unsafe_allow_html=True,
    )

    # ── Correlación consumo vs costo unitario ─────────────────────────────────────
    st.markdown("#### Correlación: Consumo m³ vs Costo Unitario Promedio")

    if not hay_tarifas_para_correlacion(periodo_sql):
        st.info(MENSAJE_CORRELACION_SIN_TARIFAS)
    else:
        df_corr_base = get_detalle_correlacion_por_periodo(periodo_sql)
        if df_corr_base.empty:
            st.info("No hay datos de facturación para calcular la correlación en el período.")
        else:
            df_tarifas_corr = get_facturacion_por_tarifa(periodo_sql)
            if not df_tarifas_corr.empty:
                categorias_corr = [
                    t for t in df_tarifas_corr["tarifa_aplicada"].tolist()
                    if _tarifa_es_valida(t)
                ]
            else:
                categorias_corr = sorted(
                    {
                        t
                        for t in df_corr_base["tarifa_aplicada"].tolist()
                        if _tarifa_es_valida(t)
                    }
                )

            if not categorias_corr:
                st.info(MENSAJE_CORRELACION_SIN_TARIFAS)
            else:
                st.markdown(
                    "<span class='inet-filter-label'>Tarifa Usuario</span>",
                    unsafe_allow_html=True,
                )
                categoria_corr_sel = st.selectbox(
                    "Tarifa Usuario",
                    options=categorias_corr,
                    key="corr_tarifa_agua",
                    label_visibility="collapsed",
                )

                df_corr = df_corr_base[
                    df_corr_base["tarifa_aplicada"] == categoria_corr_sel
                ].copy()
                df_corr["consumidor"] = (
                    df_corr["nro_socio"].astype(str)
                    + " - "
                    + df_corr["nombre_socio"].fillna("")
                )
                df_corr = df_corr[
                    (df_corr["consumo_m3"] > 0) & (df_corr["promedio_agua_pura"] > 0)
                ].copy()

                if df_corr.empty:
                    st.info(
                        "No hay datos con consumo/promedio > 0 para la tarifa seleccionada."
                    )
                else:
                    st.caption(f"Registros graficados: {len(df_corr):,}")
                    fig_corr = px.scatter(
                        df_corr,
                        x="consumo_m3",
                        y="promedio_agua_pura",
                        hover_name="consumidor",
                        custom_data=[
                            "dinero_agua",
                            "nro_factura",
                            "tarifa_aplicada",
                            "promedio_agua_pura",
                        ],
                        labels={
                            "consumo_m3": "Consumo m³",
                            "promedio_agua_pura": "Promedio Agua Pura ($/m³)",
                        },
                    )
                    fig_corr.update_traces(
                        marker=dict(
                            size=7,
                            opacity=0.55,
                            line=dict(width=0.5, color="rgba(120,120,120,0.45)"),
                        ),
                        hovertemplate=(
                            "<b>%{hovertext}</b><br>"
                            "Tarifa: %{customdata[2]}<br>"
                            "Nro Factura: %{customdata[1]}<br>"
                            "Consumo: %{x:,.1f} m³<br>"
                            "Promedio Agua Pura: $%{customdata[3]:,.2f}<br>"
                            "Importe Neto Agua: $%{customdata[0]:,.0f}<extra></extra>"
                        ),
                    )
                    fig_corr.update_layout(
                        height=DIST_CHART_HEIGHT,
                        margin=dict(t=8, b=0, l=0, r=0),
                        xaxis_title="Consumo m³",
                        yaxis_title="Promedio Agua Pura ($/m³)",
                        showlegend=False,
                    )
                    st.plotly_chart(fig_corr, width="stretch")


    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);'/>",
        unsafe_allow_html=True,
    )
    st.markdown("#### Evolución de Total Facturado")
    meses_total = st.number_input(
        "Meses a comparar (total):",
        min_value=MESES_EVOLUCION_MIN,
        max_value=MESES_EVOLUCION_MAX,
        value=MESES_EVOLUCION_DEFAULT,
        step=1,
        key="meses_evolucion_total",
    )
    periodos_total = get_periodos_evolucion(periodos_sorted, periodo_sql, meses_total)
    df_total_hist = fetch_totales_por_periodos(engine, periodos_total, SERVICIO_FC)
    if df_total_hist.empty or float(df_total_hist["total_facturado"].sum()) <= 0:
        st.info("No hay datos históricos de total facturado.")
    else:
        fig_total = build_evolucion_total_fig(df_total_hist, height=DIST_CHART_HEIGHT)
        st.plotly_chart(fig_total, width="stretch")

