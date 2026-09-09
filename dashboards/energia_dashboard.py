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
from common.evolucion_facturado import build_evolucion_total_fig, historico_desde_getter

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# 1. Configuración de página
st.set_page_config(page_title="Dashboard CEEL", layout="wide")

# 2. Configuración de variables
TOP_N_TARIFAS_DEFAULT = 6
MESES_EVOLUCION_DEFAULT = 6
MESES_EVOLUCION_MIN = 2
MESES_EVOLUCION_MAX = 12
DIST_CHART_HEIGHT = 360
PIE_DOMAIN_X = (0.0, 0.58)
PIE_DOMAIN_Y = (0.02, 0.98)
PIE_CENTER_X = (PIE_DOMAIN_X[0] + PIE_DOMAIN_X[1]) / 2
SERVICIO_TIPO = 'energia'

# 3. Motor de conexión
if load_dotenv is not None:
    load_dotenv()

db_url = os.getenv("DB_URL")

if db_url:
    engine = create_engine(db_url)
else:
    required_env_vars = ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"]
    missing_env_vars = [var_name for var_name in required_env_vars if not os.getenv(var_name)]

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

# 4. Funciones de carga y procesamiento
def normalize_label(value):
    if pd.isna(value):
        return value
    value = str(value).strip()
    mapping = {
        'energia': 'Energía',
        'alumbrado_publico': 'Alumbrado Público',
        'consorcios_barriales': 'Consorcios Barriales',
        'facturas_adicionales': 'Facturas Adicionales',
        'adicionales': 'Adicionales',
        'otros': 'Otros',
        'sin tarifa base': 'Sin Tarifa Base',
    }
    key = value.lower()
    if key in mapping:
        return mapping[key]
    return value.replace('_', ' ').replace('-', ' ').title()


def to_periodo_sql(value):
    """Convierte la selección del filtro a 'YYYY-MM-DD' para consultas SQL."""
    text_value = str(value).strip()
    # Valores ISO devueltos por la BD: usar tal cual (dayfirst=True los corrompe).
    if len(text_value) == 10 and text_value[4] == '-' and text_value[7] == '-':
        return text_value
    return pd.to_datetime(text_value, dayfirst=True).strftime('%Y-%m-%d')


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


@st.cache_data
def get_periodos_disponibles():
    return fetch_periodos_disponibles(engine)

@st.cache_data
def get_facturacion_por_tarifa_base(periodo):
    """Facturación por tarifa base desde sp_kpi_facturacion_por_tarifa."""
    cols = ['tarifa_base', 'total_facturado']
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text('CALL sp_kpi_facturacion_por_tarifa(:periodo)'),
                {'periodo': periodo},
            )
            rows = result.fetchall()
            sp_cols = list(result.keys())
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=sp_cols)
        if 'tarifa_base' not in df.columns:
            return pd.DataFrame(columns=cols)
        df['tarifa_base'] = (
            df['tarifa_base'].astype(str).str.strip().replace('', 'Sin Definir')
        )
        df['total_facturado'] = pd.to_numeric(df['total_facturado'], errors='coerce').fillna(0.0)
        df = df[df['total_facturado'] > 0].sort_values('total_facturado', ascending=False)
        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def _get_nombres_socios_energia():
    """Mapa nro_socio → nombre_socio desde socios_energia (el SP no incluye este dato)."""
    try:
        query = text(
            """
            SELECT
                nro_socio,
                COALESCE(NULLIF(TRIM(MAX(nombre_socio)), ''), 'Sin Nombre') AS nombre_socio
            FROM conecciones_energia.socios_energia
            WHERE servicio_tipo = 'Energia'
            GROUP BY nro_socio
            """
        )
        df = pd.read_sql(query, engine)
        if df is None or df.empty:
            return pd.DataFrame(columns=['nro_socio', 'nombre_socio'])
        return df
    except Exception:
        return pd.DataFrame(columns=['nro_socio', 'nombre_socio'])


@st.cache_data
def get_consolidado_facturas_por_periodo(periodo):
    """
    Una fila por factura vía sp_consolidado_facturas_por_periodo.
    Fuente única cacheada para Top 10 y Correlación (una sola llamada al SP por período).
    """
    cols = [
        'nro_factura', 'nro_socio', 'nombre_socio',
        'tarifa_base', 'escalon_asignado',
        'consumo_kwh_real', 'promedio_energia_pura',
        'dinero_energia', 'total_factura',
    ]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text('CALL sp_consolidado_facturas_por_periodo(:periodo)'),
                {'periodo': periodo},
            )
            rows = result.fetchall()
            sp_cols = list(result.keys())
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=sp_cols)
        if 'dinero_energia' not in df.columns and 'importe_neto_energia' in df.columns:
            df['dinero_energia'] = df['importe_neto_energia']

        if 'nro_socio' in df.columns:
            nombres = _get_nombres_socios_energia()
            if not nombres.empty:
                df = df.merge(nombres, on='nro_socio', how='left')
            if 'nombre_socio' not in df.columns:
                df['nombre_socio'] = 'Sin Nombre'
            else:
                df['nombre_socio'] = (
                    df['nombre_socio'].astype(str).str.strip().replace('', 'Sin Nombre')
                )
                df['nombre_socio'] = df['nombre_socio'].fillna('Sin Nombre')

        for field, default in [
            ('tarifa_base', 'Sin Definir'),
            ('escalon_asignado', 'Sin Escalón'),
        ]:
            if field in df.columns:
                df[field] = df[field].astype(str).str.strip().replace('', default)

        for col in ['consumo_kwh_real', 'promedio_energia_pura', 'dinero_energia', 'total_factura']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

        for col in cols:
            if col not in df.columns:
                df[col] = 0.0 if col in (
                    'consumo_kwh_real', 'promedio_energia_pura', 'dinero_energia', 'total_factura',
                ) else 'Sin Nombre' if col == 'nombre_socio' else ''

        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


def get_consumidores_por_periodo(periodo):
    """Top 10: agrega por (tarifa_base, nro_socio) reutilizando el cache del consolidado."""
    cols = ['tarifa_base', 'nro_socio', 'nombre_socio', 'cantidad_facturas',
            'consumo_kwh_real', 'importe_neto_energia']
    df = get_consolidado_facturas_por_periodo(periodo)
    if df.empty:
        return pd.DataFrame(columns=cols)
    agg = (
        df.groupby(['tarifa_base', 'nro_socio'], as_index=False)
        .agg(
            nombre_socio=('nombre_socio', 'first'),
            cantidad_facturas=('nro_factura', 'nunique'),
            consumo_kwh_real=('consumo_kwh_real', 'sum'),
            importe_neto_energia=('dinero_energia', 'sum'),
        )
    )
    return agg[cols]


def get_detalle_correlacion_por_periodo(periodo):
    """Correlación: una fila por factura con promedio_energia_pura directo de la vista."""
    cols = [
        'tarifa_base', 'escalon_asignado',
        'nro_socio', 'nombre_socio', 'nro_factura',
        'consumo_kwh_real', 'promedio_energia_pura', 'importe_neto_energia',
    ]
    df = get_consolidado_facturas_por_periodo(periodo)
    if df.empty:
        return pd.DataFrame(columns=cols)
    result = df.rename(columns={'dinero_energia': 'importe_neto_energia'})
    return result[cols]


@st.cache_data
def get_kpi_por_sector_sp(periodo):
    """Servicios del período desde sp_kpi_por_sector(sector, periodo)."""
    cols = ['periodo', 'nombre_servicio', 'total_facturado', 'consumo_kwh_real']
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text('CALL sp_kpi_por_sector(:sector, :periodo)'),
                {'sector': SERVICIO_TIPO, 'periodo': periodo},
            )
            rows = result.fetchall()
            sp_cols = list(result.keys())
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows, columns=sp_cols)
        df['nombre_servicio'] = df['nombre_servicio'].astype(str).str.strip()
        df['total_facturado'] = pd.to_numeric(df['total_facturado'], errors='coerce').fillna(0.0)
        df['consumo_kwh_real'] = pd.to_numeric(df['consumo_kwh_real'], errors='coerce').fillna(0.0)
        return df[[c for c in cols if c in df.columns]]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def get_kpis_por_periodo(periodo):
    """KPIs calculados desde sp_kpi_por_sector (una fila por nombre_servicio)."""
    _df_empty = pd.DataFrame(columns=['nombre_servicio', 'total_facturado', 'consumo_kwh_real'])
    _zero = {
        'total_facturado': 0.0,
        'importe_neto_energia': 0.0,
        'importe_otros_conceptos': 0.0,
        'consumo_kwh_real': 0.0,
        'detalle_servicios': _df_empty,
    }
    try:
        df = get_kpi_por_sector_sp(periodo)
        if df.empty:
            return _zero

        total_facturado = float(df['total_facturado'].sum())
        mask_energia = df['nombre_servicio'].str.lower() == 'energia'
        fila_energia = df.loc[mask_energia]
        importe_neto_energia = float(fila_energia['total_facturado'].iloc[0]) if mask_energia.any() else 0.0
        importe_otros_conceptos = total_facturado - importe_neto_energia
        consumo_kwh_real = float(fila_energia['consumo_kwh_real'].iloc[0]) if mask_energia.any() else 0.0

        detalle = df[['nombre_servicio', 'total_facturado', 'consumo_kwh_real']].copy()

        return {
            'total_facturado': total_facturado,
            'importe_neto_energia': importe_neto_energia,
            'importe_otros_conceptos': importe_otros_conceptos,
            'consumo_kwh_real': consumo_kwh_real,
            'detalle_servicios': detalle,
        }
    except Exception:
        return _zero


def _build_interactive_pie_html(df_values, label_col, value_col, palette, chart_id, hole=0.45, height=DIST_CHART_HEIGHT):
    """Genera el HTML de un pie/donut Plotly interactivo con leyenda sincronizada."""
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


# ─── 5. Interfaz ──────────────────────────────────────────────────────────────
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
    iframe[title="streamlit_echarts.st_echarts"] {
        width: 100% !important;
    }
</style>
"""
st.markdown(_COMPACT_LAYOUT_CSS, unsafe_allow_html=True)

st.markdown(
    "<h3 style='margin:0 0 0.35rem 0;line-height:1.2;'>⚡ Dashboard de Facturación - CEEL ENERGÍA</h3>",
    unsafe_allow_html=True,
)

periodos_sorted = get_periodos_disponibles()
if not periodos_sorted:
    periodos_sorted = ['01-05-2026', '01-04-2026']

filt_periodo, filt_top_n, kpi_total, kpi_neto, kpi_otros, kpi_kwh = st.columns(
    [1.2, 0.75, 1.05, 1.05, 1.05, 1.05], gap="small"
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

kpis = get_kpis_por_periodo(periodo_sql)
total_facturado = kpis['total_facturado']
sin_datos = sin_datos_periodo(total_facturado)
importe_neto_energia = kpis['importe_neto_energia']
importe_otros_conceptos = kpis['importe_otros_conceptos']
consumo_kwh_real = kpis['consumo_kwh_real']

with kpi_total:
    st.metric("Total Facturado", f"${total_facturado:,.0f}")
with kpi_neto:
    st.metric("Importe Neto Energía", f"${importe_neto_energia:,.0f}")
with kpi_otros:
    st.metric("Importe Otros Conceptos", f"${importe_otros_conceptos:,.0f}")
with kpi_kwh:
    st.metric("kW Total Distribuidos", f"{consumo_kwh_real:,.0f}")

if sin_datos:
    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown(
            "<p class='inet-section-title'>Distribución de Facturación por Servicios</p>",
            unsafe_allow_html=True,
        )
        st.info(NO_RECORDS_MESSAGE)
    with right_col:
        st.markdown(
            "<p class='inet-section-title'>Distribución de Facturación por Tarifa Base</p>",
            unsafe_allow_html=True,
        )
        st.info(NO_RECORDS_MESSAGE)

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
        unsafe_allow_html=True,
    )
    st.markdown("#### Top 10 Consumidores por Tarifa Base")
    st.info(NO_RECORDS_MESSAGE)

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
        unsafe_allow_html=True,
    )
    st.markdown("#### Correlación: Consumo kWh vs Costo Unitario Promedio")
    st.info(NO_RECORDS_MESSAGE)

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
        unsafe_allow_html=True,
    )
    st.markdown("#### Evolución de Total Facturado")
    st.info(NO_RECORDS_MESSAGE)

if not sin_datos:
    # ── Gráficos de distribución ──────────────────────────────────────────────────
    df_totalizado_tarifa = get_facturacion_por_tarifa_base(periodo_sql)

    left_col, right_col = st.columns(2)

    with left_col:
        st.markdown(
            "<p class='inet-section-title'>Distribución de Facturación por Servicios</p>",
            unsafe_allow_html=True,
        )
        df_kpi_serv = kpis.get('detalle_servicios', pd.DataFrame())
        if not df_kpi_serv.empty:
            df_serv = df_kpi_serv[df_kpi_serv['total_facturado'] > 0].sort_values(
                by='total_facturado', ascending=False
            )
            if len(df_serv) > top_n_tarifas:
                top_serv = df_serv.head(top_n_tarifas)
                otros_serv = pd.DataFrame({
                    'nombre_servicio': ['Otros'],
                    'total_facturado': [df_serv.iloc[top_n_tarifas:]['total_facturado'].sum()],
                })
                df_serv = pd.concat([top_serv, otros_serv], ignore_index=True)
            df_serv['label'] = df_serv['nombre_servicio'].apply(normalize_label)
            html_donut = _build_interactive_pie_html(
                df_serv, 'label', 'total_facturado',
                px.colors.qualitative.Pastel, 'pie-energia-serv', hole=0.45,
                height=DIST_CHART_HEIGHT,
            )
            components.html(html_donut, height=DIST_CHART_HEIGHT, scrolling=False)
        else:
            st.info("No hay datos de servicios para el período seleccionado.")

    with right_col:
        st.markdown(
            "<p class='inet-section-title'>Distribución de Facturación por Tarifa Base</p>",
            unsafe_allow_html=True,
        )
        if not df_totalizado_tarifa.empty:
            df_tarifa = df_totalizado_tarifa[
                df_totalizado_tarifa['total_facturado'] > 0
            ].sort_values(by='total_facturado', ascending=False).copy()
            if len(df_tarifa) > top_n_tarifas:
                top_tb = df_tarifa.head(top_n_tarifas)
                otros_tb = pd.DataFrame({
                    'tarifa_base': ['Otros'],
                    'total_facturado': [df_tarifa.iloc[top_n_tarifas:]['total_facturado'].sum()],
                })
                df_tarifa = pd.concat([top_tb, otros_tb], ignore_index=True)
            df_tarifa['label'] = df_tarifa['tarifa_base'].apply(normalize_label)
            html_pie = _build_interactive_pie_html(
                df_tarifa, 'label', 'total_facturado',
                px.colors.qualitative.Set3, 'pie-energia-tarifa', hole=0,
                height=DIST_CHART_HEIGHT,
            )
            components.html(html_pie, height=DIST_CHART_HEIGHT, scrolling=False)
        else:
            st.info("No hay datos de tarifa base para el período seleccionado.")

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
        unsafe_allow_html=True,
    )

    st.markdown("#### Top 10 Consumidores por Tarifa Base")

    df_consumidores = get_consumidores_por_periodo(periodo_sql)

    if not df_totalizado_tarifa.empty:
        categorias_top10 = df_totalizado_tarifa['tarifa_base'].tolist()
        st.markdown("<span class='inet-filter-label'>Tarifa Base</span>", unsafe_allow_html=True)
        categoria_sel = st.selectbox(
            "Tarifa Base",
            options=categorias_top10,
            key="top10_categoria",
            label_visibility="collapsed",
        )

        if categoria_sel:
            if df_consumidores.empty:
                st.info("No hay consumidores disponibles para el período seleccionado.")
            else:
                df_plot = df_consumidores[df_consumidores['tarifa_base'] == categoria_sel].copy()
                if df_plot.empty:
                    st.info(f"No hay consumidores para la tarifa base '{categoria_sel}' en el período seleccionado.")
                else:
                    df_plot['consumidor'] = (
                        df_plot['nro_socio'].astype(str) + ' - ' + df_plot['nombre_socio'].fillna('')
                    )

                    df_top10_barras = df_plot.nlargest(10, 'consumo_kwh_real').copy()
                    y_order = df_top10_barras.sort_values(by='consumo_kwh_real', ascending=True)['consumidor'].tolist()
                    df_top10_barras['consumidor'] = pd.Categorical(
                        df_top10_barras['consumidor'],
                        categories=y_order,
                        ordered=True,
                    )

                    fig_top10_bar = px.bar(
                        df_top10_barras,
                        x='consumo_kwh_real',
                        y='consumidor',
                        color='tarifa_base',
                        orientation='h',
                        text='consumo_kwh_real',
                        custom_data=['importe_neto_energia', 'tarifa_base', 'cantidad_facturas'],
                    )
                    fig_top10_bar.update_traces(
                        texttemplate='%{text:,.0f}',
                        textposition='outside',
                        hovertemplate=(
                            '<b>%{y}</b><br>'
                            'Categoría: %{customdata[1]}<br>'
                            'Facturas: %{customdata[2]:,.0f}<br>'
                            'Consumo: %{x:,.0f} kWh<br>'
                            'Importe Neto Energía: $%{customdata[0]:,.0f}<extra></extra>'
                        ),
                    )
                    fig_top10_bar.update_layout(
                        height=DIST_CHART_HEIGHT,
                        margin=dict(t=8, b=0, l=0, r=0),
                        xaxis_title='Consumo kWh Real',
                        yaxis_title='Consumidor',
                        yaxis={'categoryorder': 'array', 'categoryarray': y_order},
                        legend_title_text='Tarifa Base',
                        showlegend=False,
                    )
                    st.plotly_chart(fig_top10_bar, width='stretch')
        else:
            st.info("Seleccione una categoría para ver el Top 10.")
    else:
        st.info("No hay datos disponibles para el Top 10 del período seleccionado.")

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
        unsafe_allow_html=True,
    )

    st.markdown("#### Correlación: Consumo kWh vs Costo Unitario Promedio")

    df_consumidores_corr = get_detalle_correlacion_por_periodo(periodo_sql)

    if not df_consumidores_corr.empty:
        if 'corr_segmentar_por_tarifa_base' not in st.session_state:
            st.session_state['corr_segmentar_por_tarifa_base'] = False

        usar_tarifa_base = st.session_state['corr_segmentar_por_tarifa_base']
        etiqueta_segmentacion = 'Tarifa Base' if usar_tarifa_base else 'Escalón Asignado'
        campo_segmentacion = 'tarifa_base' if usar_tarifa_base else 'escalon_asignado'

        boton_segmentacion = (
            "Cambiar a Escalón Asignado" if usar_tarifa_base else "Cambiar a Tarifa Base"
        )
        if st.button(boton_segmentacion, key='corr_toggle_segmentacion'):
            st.session_state['corr_segmentar_por_tarifa_base'] = not usar_tarifa_base
            st.rerun()

        if usar_tarifa_base:
            df_tarifas_corr = get_facturacion_por_tarifa_base(periodo_sql)
            if not df_tarifas_corr.empty:
                categorias_corr = df_tarifas_corr['tarifa_base'].tolist()
            else:
                categorias_corr = sorted(df_consumidores_corr['tarifa_base'].dropna().unique().tolist())
        else:
            categorias_corr = (
                df_consumidores_corr.groupby(campo_segmentacion, dropna=True)
                .size()
                .sort_values(ascending=False)
                .index.tolist()
            )
        categorias_corr = [c for c in categorias_corr if not str(c).lower().startswith('sin ')]

        _defaults_corr = {
            'tarifa_base':      'Residencial c/Subs < 500',
            'escalon_asignado': 'Residencial c/Subs < 500',
        }
        _default_corr = _defaults_corr.get(campo_segmentacion, '')
        _default_corr_idx = (
            categorias_corr.index(_default_corr)
            if _default_corr in categorias_corr
            else 0
        )
        st.markdown(f"<span class='inet-filter-label'>{etiqueta_segmentacion}</span>", unsafe_allow_html=True)
        categoria_corr_sel = st.selectbox(
            etiqueta_segmentacion,
            options=categorias_corr,
            index=_default_corr_idx,
            key=f"corr_categoria_{campo_segmentacion}",
            label_visibility="collapsed",
        )

        if categoria_corr_sel:
            df_corr = df_consumidores_corr[
                df_consumidores_corr[campo_segmentacion] == categoria_corr_sel
            ].copy()
            df_corr['consumidor'] = (
                df_corr['nro_socio'].astype(str) + ' - ' + df_corr['nombre_socio'].fillna('')
            )
            df_corr = df_corr[
                (df_corr['consumo_kwh_real'] > 0)
                & (df_corr['promedio_energia_pura'] > 0)
            ].copy()

            if not df_corr.empty:
                st.caption(f"Registros graficados: {len(df_corr):,}")
                fig_corr = px.scatter(
                    df_corr,
                    x='consumo_kwh_real',
                    y='promedio_energia_pura',
                    hover_name='consumidor',
                    custom_data=[
                        'importe_neto_energia',
                        'nro_factura',
                        campo_segmentacion,
                        'promedio_energia_pura',
                    ],
                    labels={
                        'consumo_kwh_real': 'Consumo kWh Real',
                        'promedio_energia_pura': 'Promedio Energía Pura ($/kWh)',
                    },
                )
                fig_corr.update_traces(
                    marker=dict(size=7, opacity=0.55, line=dict(width=0.5, color='rgba(120,120,120,0.45)')),
                    hovertemplate=(
                        '<b>%{hovertext}</b><br>'
                        f'{etiqueta_segmentacion}: %{{customdata[2]}}<br>'
                        'Nro Factura: %{customdata[1]}<br>'
                        'Consumo: %{x:,.0f} kWh<br>'
                        'Promedio Energía Pura: $%{customdata[3]:,.2f}<br>'
                        'Importe Neto Energía: $%{customdata[0]:,.0f}<extra></extra>'
                    ),
                )
                fig_corr.update_layout(
                    height=DIST_CHART_HEIGHT,
                    margin=dict(t=8, b=0, l=0, r=0),
                    xaxis_title='Consumo kWh Real',
                    yaxis_title='Promedio Energía Pura ($/kWh)',
                    showlegend=False,
                )
                st.plotly_chart(fig_corr, width='stretch')
            else:
                st.info(
                    f"No hay datos con consumo/promedio > 0 para calcular la correlación en la {etiqueta_segmentacion.lower()} seleccionada."
                )
        else:
            st.info(f"Seleccione una {etiqueta_segmentacion.lower()} para ver la correlación.")
    else:
        st.info("No hay datos disponibles para la sección de correlación en el período seleccionado.")

    st.markdown(
        "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
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
    df_total_hist = historico_desde_getter(
        periodos_total,
        lambda p: get_kpis_por_periodo(p)["total_facturado"],
    )
    if df_total_hist.empty or float(df_total_hist["total_facturado"].sum()) <= 0:
        st.info("No hay datos históricos de total facturado.")
    else:
        fig_total = build_evolucion_total_fig(df_total_hist, height=DIST_CHART_HEIGHT)
        st.plotly_chart(fig_total, width="stretch")

