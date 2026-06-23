import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from html import escape
import json
import math
import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# 1. Configuración de página
st.set_page_config(page_title="Dashboard CEEL", layout="wide")

# 2. Configuración de variables
TOP_N_TARIFAS_DEFAULT = 6

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


@st.cache_data
def get_periodos_disponibles():
    try:
        query = text(
            """
            SELECT DISTINCT periodo
            FROM conecciones_energia.v_consolidado_facturas_final
            WHERE periodo IS NOT NULL
            ORDER BY periodo DESC
            """
        )
        df_periodos = pd.read_sql(query, engine)
        if df_periodos is None or df_periodos.empty:
            return []
        periodos = pd.to_datetime(df_periodos['periodo'], errors='coerce').dropna()
        return periodos.dt.strftime('%Y-%m-%d').tolist()
    except Exception:
        return []

@st.cache_data
def get_totalizado_por_tarifa_base(periodo):
    """Totales por tarifa_base desde v_totalizado_por_tarifa_base (pre-agregado en MySQL)."""
    cols = [
        'tarifa_base',
        'cantidad_facturas',
        'total_consumo_kwh',
        'total_dinero_energia',
        'total_dinero_otros',
        'total_recaudacion',
        'total_facturado',
    ]
    try:
        if periodo:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                    COALESCE(cantidad_facturas, 0) AS cantidad_facturas,
                    COALESCE(total_consumo_kwh, 0) AS total_consumo_kwh,
                    COALESCE(total_dinero_energia, 0) AS total_dinero_energia,
                    COALESCE(total_dinero_otros, 0) AS total_dinero_otros,
                    COALESCE(total_recaudacion, 0) AS total_recaudacion
                FROM conecciones_energia.v_totalizado_por_tarifa_base
                WHERE periodo = :periodo
                """
            )
            df_tarifa = pd.read_sql(query, engine, params={'periodo': periodo})
        else:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                    SUM(COALESCE(cantidad_facturas, 0)) AS cantidad_facturas,
                    SUM(COALESCE(total_consumo_kwh, 0)) AS total_consumo_kwh,
                    SUM(COALESCE(total_dinero_energia, 0)) AS total_dinero_energia,
                    SUM(COALESCE(total_dinero_otros, 0)) AS total_dinero_otros,
                    SUM(COALESCE(total_recaudacion, 0)) AS total_recaudacion
                FROM conecciones_energia.v_totalizado_por_tarifa_base
                GROUP BY COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir')
                """
            )
            df_tarifa = pd.read_sql(query, engine)

        if df_tarifa is None or df_tarifa.empty:
            return pd.DataFrame(columns=cols)

        df_tarifa = df_tarifa.copy()
        for col in [
            'cantidad_facturas',
            'total_consumo_kwh',
            'total_dinero_energia',
            'total_dinero_otros',
            'total_recaudacion',
        ]:
            df_tarifa[col] = pd.to_numeric(df_tarifa[col], errors='coerce').fillna(0.0)
        df_tarifa['total_facturado'] = df_tarifa['total_recaudacion']
        df_tarifa = df_tarifa[df_tarifa['total_recaudacion'] > 0].sort_values(
            by='total_recaudacion', ascending=False
        )
        return df_tarifa[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def get_facturacion_por_tarifa_base(periodo):
    cols = ['tarifa_base', 'total_facturado']
    try:
        if periodo:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                    SUM(COALESCE(total_factura, 0)) AS total_facturado
                FROM conecciones_energia.v_consolidado_facturas_final
                WHERE periodo = :periodo
                GROUP BY COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir')
                ORDER BY total_facturado DESC
                """
            )
            df = pd.read_sql(query, engine, params={'periodo': periodo})
        else:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                    SUM(COALESCE(total_factura, 0)) AS total_facturado
                FROM conecciones_energia.v_consolidado_facturas_final
                GROUP BY COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir')
                ORDER BY total_facturado DESC
                """
            )
            df = pd.read_sql(query, engine)
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        df['total_facturado'] = pd.to_numeric(df['total_facturado'], errors='coerce').fillna(0.0)
        df = df[df['total_facturado'] > 0]
        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def get_facturacion_por_escalon(periodo):
    cols = ['escalon_asignado', 'total_facturado']
    try:
        if periodo:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(escalon_asignado), ''), 'Sin Escalón') AS escalon_asignado,
                    SUM(COALESCE(total_factura, 0)) AS total_facturado
                FROM conecciones_energia.v_consolidado_facturas_final
                WHERE periodo = :periodo
                GROUP BY COALESCE(NULLIF(TRIM(escalon_asignado), ''), 'Sin Escalón')
                ORDER BY total_facturado DESC
                """
            )
            df = pd.read_sql(query, engine, params={'periodo': periodo})
        else:
            query = text(
                """
                SELECT
                    COALESCE(NULLIF(TRIM(escalon_asignado), ''), 'Sin Escalón') AS escalon_asignado,
                    SUM(COALESCE(total_factura, 0)) AS total_facturado
                FROM conecciones_energia.v_consolidado_facturas_final
                GROUP BY COALESCE(NULLIF(TRIM(escalon_asignado), ''), 'Sin Escalón')
                ORDER BY total_facturado DESC
                """
            )
            df = pd.read_sql(query, engine)
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        df['total_facturado'] = pd.to_numeric(df['total_facturado'], errors='coerce').fillna(0.0)
        df = df[df['total_facturado'] > 0]
        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)

@st.cache_data
def _get_facturas_raw_por_periodo(periodo):
    """Una fila por factura desde v_consolidado_facturas_final + nombre_socio.
    Fuente única para Top 10 y Correlación; se cachea una sola vez por período."""
    cols = [
        'nro_factura', 'nro_socio', 'nombre_socio',
        'tarifa_base', 'escalon_asignado',
        'consumo_kwh_real', 'promedio_energia_pura',
        'dinero_energia', 'total_factura',
    ]
    try:
        base_select = """
            SELECT
                f.nro_factura,
                f.nro_socio,
                COALESCE(NULLIF(TRIM(s.nombre_socio), ''), 'Sin Nombre') AS nombre_socio,
                COALESCE(NULLIF(TRIM(f.tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                COALESCE(NULLIF(TRIM(f.escalon_asignado), ''), 'Sin Escalón') AS escalon_asignado,
                COALESCE(f.consumo_kwh_real, 0) AS consumo_kwh_real,
                COALESCE(f.promedio_energia_pura, 0) AS promedio_energia_pura,
                COALESCE(f.dinero_energia, 0) AS dinero_energia,
                COALESCE(f.total_factura, 0) AS total_factura
            FROM conecciones_energia.v_consolidado_facturas_final f
            LEFT JOIN conecciones_energia.socios_energia s
                ON s.nro_socio = f.nro_socio AND s.servicio_tipo = 'Energia'
        """
        if periodo:
            df = pd.read_sql(
                text(base_select + " WHERE f.periodo = :periodo"),
                engine,
                params={'periodo': periodo},
            )
        else:
            df = pd.read_sql(text(base_select), engine)

        if df is None or df.empty:
            return pd.DataFrame(columns=cols)

        for col in ['consumo_kwh_real', 'promedio_energia_pura', 'dinero_energia', 'total_factura']:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
        return df[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


def get_consumidores_por_periodo(periodo):
    """Top 10: agrega por (tarifa_base, nro_socio) reutilizando el cache raw."""
    cols = ['tarifa_base', 'nro_socio', 'nombre_socio', 'cantidad_facturas',
            'consumo_kwh_real', 'importe_neto_energia']
    df = _get_facturas_raw_por_periodo(periodo)
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
    df = _get_facturas_raw_por_periodo(periodo)
    if df.empty:
        return pd.DataFrame(columns=cols)
    result = df.rename(columns={'dinero_energia': 'importe_neto_energia'})
    return result[cols]

@st.cache_data
def get_facturacion_por_tarifa_base_historico():
    """Totales reales por (periodo, tarifa_base) desde v_consolidado_facturas_final."""
    cols = ['periodo', 'tarifa_base', 'total_facturado']
    try:
        query = text(
            """
            SELECT
                periodo,
                COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir') AS tarifa_base,
                SUM(COALESCE(total_factura, 0)) AS total_facturado
            FROM conecciones_energia.v_consolidado_facturas_final
            WHERE NOT LOWER(TRIM(tarifa_base)) LIKE 'sin %'
            GROUP BY periodo, COALESCE(NULLIF(TRIM(tarifa_base), ''), 'Sin Definir')
            ORDER BY periodo ASC, total_facturado DESC
            """
        )
        df = pd.read_sql(query, engine)
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        df['periodo'] = pd.to_datetime(df['periodo'], errors='coerce')
        df = df.dropna(subset=['periodo'])
        df['total_facturado'] = pd.to_numeric(df['total_facturado'], errors='coerce').fillna(0)
        return df[df['total_facturado'] > 0][cols]
    except Exception:
        return pd.DataFrame(columns=cols)


def simular_facturacion_anual_por_tarifa_base(df_base):
    """Proyecta 12 meses ficticios usando los totales reales del período más reciente como base."""
    cols = ['periodo', 'tarifa_base', 'total_facturado']
    if df_base is None or df_base.empty:
        return pd.DataFrame(columns=cols)

    df_work = df_base.copy()
    df_work['periodo'] = pd.to_datetime(df_work['periodo'], errors='coerce')
    df_work = df_work.dropna(subset=['periodo'])
    if df_work.empty:
        return pd.DataFrame(columns=cols)

    end_period = df_work['periodo'].max().to_period('M').to_timestamp()
    periodos = pd.date_range(end=end_period, periods=12, freq='MS')

    latest_rows = df_work[df_work['periodo'].dt.to_period('M') == end_period.to_period('M')]
    base_por_tarifa = latest_rows.groupby('tarifa_base', as_index=False)['total_facturado'].sum()

    if base_por_tarifa.empty:
        base_por_tarifa = df_work.groupby('tarifa_base', as_index=False)['total_facturado'].mean()

    base_por_tarifa = (
        base_por_tarifa[base_por_tarifa['total_facturado'] > 0]
        .sort_values('total_facturado', ascending=False)
        .reset_index(drop=True)
    )

    rows = []
    for idx, row in base_por_tarifa.iterrows():
        tarifa = row['tarifa_base']
        base = float(row['total_facturado'])
        for month_idx, periodo in enumerate(periodos):
            seasonal = 1 + 0.14 * math.sin((2 * math.pi * (month_idx + idx)) / 12)
            trend = 0.78 + (0.045 * month_idx) + (idx % 3) * 0.02
            rows.append({
                'periodo': periodo,
                'tarifa_base': tarifa,
                'total_facturado': max(base * seasonal * trend, 0),
            })

    return pd.DataFrame(rows, columns=cols)

# 5. Interfaz
st.markdown(
    "<h3 style='margin-bottom: 0;'>📊 Dashboard de Facturación - CEEL ENERGIA ( ABR - 2026)</h3>",
    unsafe_allow_html=True,
)

# Filtros laterales
st.sidebar.header("Filtros")
periodos_sorted = get_periodos_disponibles()
if not periodos_sorted:
    periodos_sorted = ['01-05-2026', '01-04-2026']

# Mostrar el periodo más reciente por defecto (primer elemento de la lista ordenada)
periodo_display = st.sidebar.selectbox("Periodo:", periodos_sorted)

periodo_sql = to_periodo_sql(periodo_display)
top_n_tarifas = st.sidebar.number_input(
    "Cantidad de categorías (Top N):",
    min_value=1,
    max_value=50,
    value=TOP_N_TARIFAS_DEFAULT,
    step=1,
)

# KPIs


@st.cache_data
def get_kpis_por_periodo(periodo):
    """
    Devuelve totales de facturación por servicio desde la vista v_kpi_facturacion.
    Los campos resultantes serán:
        - total_facturado: suma total (todos los servicios)
        - importe_neto_energia: suma solo donde nombre_servicio = 'energia'
        - importe_otros_conceptos: suma donde nombre_servicio <> 'energia'
        - consumo_kwh_real: suma total de consumo_kwh_real
    """
    _df_empty = pd.DataFrame(columns=['nombre_servicio', 'total_facturado', 'consumo_kwh_real'])
    _zero = {
        'total_facturado': 0.0,
        'importe_neto_energia': 0.0,
        'importe_otros_conceptos': 0.0,
        'consumo_kwh_real': 0.0,
        'detalle_servicios': _df_empty,
    }
    try:
        if periodo:
            query = text(
                """
                SELECT
                    nombre_servicio,
                    COALESCE(total_facturado, 0) AS total_facturado,
                    COALESCE(consumo_kwh_real, 0) AS consumo_kwh_real
                FROM conecciones_energia.v_kpi_facturacion
                WHERE periodo = :periodo
                """
            )
            df = pd.read_sql(query, engine, params={"periodo": periodo})
        else:
            query = text(
                """
                SELECT
                    nombre_servicio,
                    SUM(COALESCE(total_facturado, 0)) AS total_facturado,
                    SUM(COALESCE(consumo_kwh_real, 0)) AS consumo_kwh_real
                FROM conecciones_energia.v_kpi_facturacion
                GROUP BY nombre_servicio
                """
            )
            df = pd.read_sql(query, engine)

        if df is None or df.empty:
            return _zero

        df['total_facturado']  = pd.to_numeric(df['total_facturado'],  errors='coerce').fillna(0.0)
        df['consumo_kwh_real'] = pd.to_numeric(df['consumo_kwh_real'], errors='coerce').fillna(0.0)

        total_facturado = float(df['total_facturado'].sum())
        try:
            importe_neto_energia = float(df.loc[df['nombre_servicio'] == 'energia', 'total_facturado'].sum())
        except Exception:
            importe_neto_energia = 0.0
        importe_otros_conceptos = float(df.loc[df['nombre_servicio'] != 'energia', 'total_facturado'].sum())
        consumo_kwh_real = float(df['consumo_kwh_real'].sum())

        return {
            'total_facturado': total_facturado,
            'importe_neto_energia': importe_neto_energia,
            'importe_otros_conceptos': importe_otros_conceptos,
            'consumo_kwh_real': consumo_kwh_real,
            'detalle_servicios': df[['nombre_servicio', 'total_facturado', 'consumo_kwh_real']].copy(),
        }
    except Exception:
        return _zero

# Obtener KPIs desde la base de datos para el periodo seleccionado
kpis = get_kpis_por_periodo(periodo_sql)
total_facturado = kpis['total_facturado']
importe_neto_energia = kpis['importe_neto_energia']
importe_otros_conceptos = kpis['importe_otros_conceptos']
consumo_kwh_real = kpis['consumo_kwh_real']

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Facturado", f"${total_facturado:,.0f}")
col2.metric("Importe Neto Energía", f"${importe_neto_energia:,.0f}")
col3.metric("Importe Otros Conceptos", f"${importe_otros_conceptos:,.0f}")
col4.metric("kW Total Distribuidos", f"{consumo_kwh_real:,.0f}")

st.markdown(
    "<hr style='margin:0.3rem 0;border:0;border-top:1px solid rgba(127,127,127,0.35);' />",
    unsafe_allow_html=True,
)

# 6. Gráfico de Anillo

df_totalizado_tarifa = get_facturacion_por_tarifa_base(periodo_sql)
df_escalon = get_facturacion_por_escalon(periodo_sql)

# Preparar datos para el gráfico de anillo (Gráfico 2: segmentación por grupos de servicio).
df_kpi_serv = kpis.get('detalle_servicios', pd.DataFrame())
if not df_kpi_serv.empty:
    df_kpi_serv = df_kpi_serv[df_kpi_serv['total_facturado'] > 0].sort_values(
        by='total_facturado', ascending=False
    )
    if len(df_kpi_serv) > top_n_tarifas:
        top_serv = df_kpi_serv.head(top_n_tarifas)
        otros_serv = pd.DataFrame({
            'nombre_servicio': ['Otros'],
            'total_facturado': [df_kpi_serv.iloc[top_n_tarifas:]['total_facturado'].sum()],
        })
        df_serv_donut = pd.concat([top_serv, otros_serv], ignore_index=True)
    else:
        df_serv_donut = df_kpi_serv.copy()
    df_tarifa_procesado = df_serv_donut[['nombre_servicio', 'total_facturado']].rename(
        columns={'nombre_servicio': 'tarifa_base'}
    )
else:
    df_tarifa_procesado = pd.DataFrame(columns=['tarifa_base', 'total_facturado'])

if not df_tarifa_procesado.empty:
    df_tarifa_procesado = df_tarifa_procesado.copy()
    df_tarifa_procesado['label'] = df_tarifa_procesado['tarifa_base'].apply(normalize_label)
    palette = px.colors.qualitative.Pastel
    slice_colors = [palette[index % len(palette)] for index in range(len(df_tarifa_procesado))]

    fig_donut = px.pie(
        df_tarifa_procesado,
        values='total_facturado',
        names='label',
        hole=0.4,
        color_discrete_sequence=slice_colors,
    )
    
    # Personalización: Mostrar nombre de categoría + porcentaje dentro de cada porción
    fig_donut.update_traces(
        textposition='inside',
        textinfo='label+percent',
        insidetextorientation='auto',
        textfont=dict(size=16),
        hovertemplate='%{label}<br>Total facturado: $%{value:,.0f}<extra></extra>'
    )
    fig_donut.update_layout(
        showlegend=False,
        margin=dict(t=0, b=0, l=0, r=0),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)'
    )
    
    donut_json = pio.to_json(fig_donut, validate=False)
    legend_items = []
    for index, row in df_tarifa_procesado.reset_index(drop=True).iterrows():
        color = slice_colors[index % len(slice_colors)]
        legend_items.append(
            f'<div class="legend-item" data-index="{index}" style="display:flex;align-items:center;gap:8px;padding:3px 0;cursor:default;opacity:1;transition:opacity 120ms ease;">'
            f'<span style="width:12px;height:12px;flex:0 0 12px;border-radius:2px;border:1px solid rgba(0,0,0,0.18);background:{color};"></span>'
            f'<span class="legend-label" style="font-size:0.92rem;line-height:1.2;">{escape(str(row["label"]))}</span>'
            f'</div>'
        )

    # Render en columnas: ocupar 50% del ancho para este gráfico (columna izquierda)
    left_col, right_col = st.columns(2)

    donut_html = fr"""
    <style>
        html, body {{
            margin: 0;
            padding: 0;
            background: transparent;
            overflow: hidden;
        }}
        * {{
        }}
        .donut-wrap {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            width: 100%;
        }}
        #donut-chart {{
            flex: 1 1 0;
            min-width: 0;
            background: transparent;
        }}
        .donut-legend {{
            flex: 0 0 auto;
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 6px;
            padding-top: 140px;
        }}
        .legend-item:hover {{
            opacity: 0.7;
        }}
        .legend-label {{
            font-size: 0.92rem;
            line-height: 1.2;
            /* Color gris estático que funciona bien en ambos temas */
            color: #7d7d7d;
            font-weight: 500;
        }}
    </style>
    <div class="donut-wrap">
        <div id="donut-chart" style="min-height:420px;"></div>
        <div class="donut-legend">
            {''.join(legend_items)}
        </div>
    </div>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <script>
        const fig = {donut_json};
        const baseColors = {json.dumps(slice_colors)};
        const chart = document.getElementById('donut-chart');
        const legendItems = Array.from(document.querySelectorAll('.legend-item'));
        const baseTextSize = (fig.data[0].textfont && fig.data[0].textfont.size) ? fig.data[0].textfont.size : 16;
        const hoverTextSize = baseTextSize + 3;
        const basePull = (fig.data[0].labels || []).map(() => 0);
        let skipNextPlotlyHover = false;

        function normalizeColor(color, alpha) {{
            if (!color) return color;
            if (color.startsWith('#')) {{
                const hex = color.slice(1);
                const fullHex = hex.length === 3 ? hex.split('').map((char) => char + char).join('') : hex;
                const number = parseInt(fullHex, 16);
                return `rgba(${{(number >> 16) & 255}}, ${{(number >> 8) & 255}}, ${{number & 255}}, ${{alpha}})`;
            }}
            return color;
        }}

        function colorsForIndex(activeIndex) {{
            return baseColors.map((color, index) => normalizeColor(color, index === activeIndex ? 1 : 0.2));
        }}

        function pullForIndex(activeIndex) {{
            return basePull.map((value, index) => index === activeIndex ? 0.08 : value);
        }}

        function clearHighlight() {{
            Plotly.restyle(chart, {{
                'marker.colors': [baseColors],
                'pull': [basePull],
                'textfont.size': baseTextSize
            }}, [0]);
            Plotly.Fx.unhover(chart);
            legendItems.forEach((item) => {{
                item.style.opacity = '1';
                item.style.fontWeight = '400';
            }});
        }}

        function setHighlight(index, showNativeHover) {{
            Plotly.restyle(chart, {{
                'marker.colors': [colorsForIndex(index)],
                'pull': [pullForIndex(index)],
                'textfont.size': hoverTextSize
            }}, [0]);
            if (showNativeHover) {{
                skipNextPlotlyHover = true;
                Plotly.Fx.hover(chart, [{{curveNumber: 0, pointNumber: index}}]);
            }}
            legendItems.forEach((item) => {{
                const isActive = Number(item.dataset.index) === index;
                item.style.opacity = isActive ? '1' : '0.35';
                item.style.fontWeight = isActive ? '600' : '400';
            }});
        }}

        legendItems.forEach((item) => {{
            const index = Number(item.dataset.index);
            item.addEventListener('mouseenter', () => setHighlight(index, true));
            item.addEventListener('mouseleave', clearHighlight);
        }});

        Plotly.newPlot(chart, fig.data, fig.layout, {{responsive: true, displayModeBar: false}}).then(() => {{
            chart.on('plotly_hover', (eventData) => {{
                if (eventData && eventData.points && eventData.points.length > 0) {{
                    if (skipNextPlotlyHover) {{
                        skipNextPlotlyHover = false;
                        return;
                    }}
                    setHighlight(eventData.points[0].pointNumber, false);
                }}
            }});
            chart.on('plotly_unhover', clearHighlight);
            clearHighlight();
        }});
    </script>
    """
    
    with left_col:
        st.markdown("#### Distr. de Fact. por Servicios")
        components.html(donut_html, height=500, scrolling=False)
    with right_col:
        st.markdown("#### Distr. de Fact. por Tarifa Base")
        df_tarifa_torta = df_totalizado_tarifa.copy() if not df_totalizado_tarifa.empty else pd.DataFrame(columns=['tarifa_base', 'total_facturado'])

        if not df_tarifa_torta.empty:
            df_tarifa_torta = df_tarifa_torta[df_tarifa_torta['total_facturado'] > 0].sort_values(
                by='total_facturado', ascending=False
            )
            df_tarifa_torta['label'] = df_tarifa_torta['tarifa_base'].apply(normalize_label)

            if len(df_tarifa_torta) > top_n_tarifas:
                top_tb = df_tarifa_torta.head(top_n_tarifas)
                otros_tb = pd.DataFrame({
                    'tarifa_base': ['Otros'],
                    'total_facturado': [df_tarifa_torta.iloc[top_n_tarifas:]['total_facturado'].sum()],
                    'label': ['Otros'],
                })
                df_torta_tarifa = pd.concat([top_tb, otros_tb], ignore_index=True)
            else:
                df_torta_tarifa = df_tarifa_torta

            pie_palette = px.colors.qualitative.Set3
            pie_slice_colors = [pie_palette[index % len(pie_palette)] for index in range(len(df_torta_tarifa))]

            fig_torta_tarifa = px.pie(
                df_torta_tarifa,
                values='total_facturado',
                names='label',
                hole=0,
                color_discrete_sequence=pie_slice_colors,
            )
            fig_torta_tarifa.update_traces(
                textposition='inside',
                textinfo='label+percent',
                insidetextorientation='auto',
                textfont=dict(size=16),
                hovertemplate='%{label}<br>Total facturado: $%{value:,.0f}<extra></extra>',
            )
            fig_torta_tarifa.update_layout(
                showlegend=False,
                margin=dict(t=0, b=0, l=0, r=0),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
            )

            pie_json = pio.to_json(fig_torta_tarifa, validate=False)
            pie_legend_items = []
            for index, row in df_torta_tarifa.reset_index(drop=True).iterrows():
                color = pie_slice_colors[index % len(pie_slice_colors)]
                pie_legend_items.append(
                    f'<div class="legend-item" data-index="{index}" style="display:flex;align-items:center;gap:8px;padding:3px 0;cursor:default;opacity:1;transition:opacity 120ms ease;">'
                    f'<span style="width:12px;height:12px;flex:0 0 12px;border-radius:2px;border:1px solid rgba(0,0,0,0.18);background:{color};"></span>'
                    f'<span class="legend-label" style="font-size:0.92rem;line-height:1.2;">{escape(str(row["label"]))}</span>'
                    f'</div>'
                )

            pie_html = fr"""
            <style>
                html, body {{
                    margin: 0;
                    padding: 0;
                    background: transparent;
                    overflow: hidden;
                }}
                * {{
                    box-sizing: border-box;
                }}
                .pie-wrap {{
                    display: flex;
                    align-items: flex-start;
                    gap: 12px;
                    width: 100%;
                }}
                #pie-chart {{
                    flex: 1 1 0;
                    min-width: 0;
                    background: transparent;
                }}
                .pie-legend {{
                    flex: 0 0 auto;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    gap: 6px;
                    padding-top: 140px;
                }}
                .legend-item:hover {{
                    opacity: 0.7;
                }}
                .legend-label {{
                    font-size: 0.92rem;
                    line-height: 1.2;
                    color: #7d7d7d;
                    font-weight: 500;
                }}
            </style>
            <div class="pie-wrap">
                <div id="pie-chart" style="min-height:420px;"></div>
                <div class="pie-legend">
                    {''.join(pie_legend_items)}
                </div>
            </div>
            <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
            <script>
                const fig = {pie_json};
                const baseColors = {json.dumps(pie_slice_colors)};
                const chart = document.getElementById('pie-chart');
                const legendItems = Array.from(document.querySelectorAll('.legend-item'));
                const baseTextSize = (fig.data[0].textfont && fig.data[0].textfont.size) ? fig.data[0].textfont.size : 16;
                const hoverTextSize = baseTextSize + 3;
                const basePull = (fig.data[0].labels || []).map(() => 0);
                let skipNextPlotlyHover = false;

                function normalizeColor(color, alpha) {{
                    if (!color) return color;
                    if (color.startsWith('#')) {{
                        const hex = color.slice(1);
                        const fullHex = hex.length === 3 ? hex.split('').map((char) => char + char).join('') : hex;
                        const number = parseInt(fullHex, 16);
                        return `rgba(${{(number >> 16) & 255}}, ${{(number >> 8) & 255}}, ${{number & 255}}, ${{alpha}})`;
                    }}
                    return color;
                }}

                function colorsForIndex(activeIndex) {{
                    return baseColors.map((color, index) => normalizeColor(color, index === activeIndex ? 1 : 0.2));
                }}

                function pullForIndex(activeIndex) {{
                    return basePull.map((value, index) => index === activeIndex ? 0.08 : value);
                }}

                function clearHighlight() {{
                    Plotly.restyle(chart, {{
                        'marker.colors': [baseColors],
                        'pull': [basePull],
                        'textfont.size': baseTextSize
                    }}, [0]);
                    Plotly.Fx.unhover(chart);
                    legendItems.forEach((item) => {{
                        item.style.opacity = '1';
                        item.style.fontWeight = '400';
                    }});
                }}

                function setHighlight(index, showNativeHover) {{
                    Plotly.restyle(chart, {{
                        'marker.colors': [colorsForIndex(index)],
                        'pull': [pullForIndex(index)],
                        'textfont.size': hoverTextSize
                    }}, [0]);
                    if (showNativeHover) {{
                        skipNextPlotlyHover = true;
                        Plotly.Fx.hover(chart, [{{curveNumber: 0, pointNumber: index}}]);
                    }}
                    legendItems.forEach((item) => {{
                        const isActive = Number(item.dataset.index) === index;
                        item.style.opacity = isActive ? '1' : '0.35';
                        item.style.fontWeight = isActive ? '600' : '400';
                    }});
                }}

                legendItems.forEach((item) => {{
                    const index = Number(item.dataset.index);
                    item.addEventListener('mouseenter', () => setHighlight(index, true));
                    item.addEventListener('mouseleave', clearHighlight);
                }});

                Plotly.newPlot(chart, fig.data, fig.layout, {{responsive: true, displayModeBar: false}}).then(() => {{
                    chart.on('plotly_hover', (eventData) => {{
                        if (eventData && eventData.points && eventData.points.length > 0) {{
                            if (skipNextPlotlyHover) {{
                                skipNextPlotlyHover = false;
                                return;
                            }}
                            setHighlight(eventData.points[0].pointNumber, false);
                        }}
                    }});
                    chart.on('plotly_unhover', clearHighlight);
                    clearHighlight();
                }});
            </script>
            """

            components.html(pie_html, height=500, scrolling=False)
        else:
            st.info("No hay datos de escalón asignado para el período seleccionado.")
else:
    st.warning("No hay datos disponibles para mostrar el gráfico.")

st.markdown(
    "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
    unsafe_allow_html=True,
)

st.subheader("Top 10 Consumidores por Tarifa Base")

df_consumidores = get_consumidores_por_periodo(periodo_sql)

if not df_totalizado_tarifa.empty:
    categorias_top10 = df_totalizado_tarifa['tarifa_base'].tolist()
    categoria_sel = st.selectbox(
        "Tarifa Base:",
        options=categorias_top10,
        key="top10_categoria",
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
                    margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title='Consumo kWh Real',
                    yaxis_title='Consumidor',
                    yaxis={'categoryorder': 'array', 'categoryarray': y_order},
                    legend_title_text='Tarifa Base',
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

st.subheader("Correlación: Consumo kWh vs Costo Unitario Promedio")

df_consumidores_corr = get_detalle_correlacion_por_periodo(periodo_sql)

if not df_consumidores_corr.empty:
    if 'corr_segmentar_por_tarifa_base' not in st.session_state:
        st.session_state['corr_segmentar_por_tarifa_base'] = False

    usar_tarifa_base = st.session_state['corr_segmentar_por_tarifa_base']
    etiqueta_segmentacion = 'Tarifa Base' if usar_tarifa_base else 'Escalón Asignado'
    campo_segmentacion = 'tarifa_base' if usar_tarifa_base else 'escalon_asignado'
    valor_sin_asignar = 'Sin Definir' if usar_tarifa_base else 'Sin Escalón'

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
    categoria_corr_sel = st.selectbox(
        f"{etiqueta_segmentacion}:",
        options=categorias_corr,
        index=_default_corr_idx,
        key=f"corr_categoria_{campo_segmentacion}",
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
                margin=dict(t=10, b=0, l=0, r=0),
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

st.subheader("Line Race: Evolución de Facturación por Tarifa Base")

df_line_race = simular_facturacion_anual_por_tarifa_base(
    get_facturacion_por_tarifa_base_historico()
)

if not df_line_race.empty:
    df_line_race = df_line_race.copy()
    df_line_race['periodo_display'] = df_line_race['periodo'].dt.strftime('%m-%Y')

    periodos_line = (
        df_line_race[['periodo', 'periodo_display']]
        .drop_duplicates()
        .sort_values('periodo')
    )
    period_options = periodos_line['periodo_display'].tolist()

    periodos_sel = st.multiselect(
        "Períodos para line race:",
        options=period_options,
        default=period_options,
        key='line_race_periodos',
    )

    if periodos_sel:
        df_line_plot = df_line_race[df_line_race['periodo_display'].isin(periodos_sel)].copy()
        period_order = [p for p in period_options if p in periodos_sel]
        df_line_plot['periodo_display'] = pd.Categorical(df_line_plot['periodo_display'], categories=period_order, ordered=True)

        top_categorias_line = (
            df_line_plot.groupby('tarifa_base', as_index=False)['total_facturado']
            .sum()
            .sort_values('total_facturado', ascending=False)
            .head(int(top_n_tarifas))['tarifa_base']
            .tolist()
        )
        df_line_plot = df_line_plot[df_line_plot['tarifa_base'].isin(top_categorias_line)].copy()
        df_line_plot = df_line_plot.sort_values(['periodo', 'tarifa_base'])

        period_dates = (
            df_line_plot[['periodo', 'periodo_display']]
            .drop_duplicates()
            .sort_values('periodo')
        )
        period_values = period_dates['periodo'].tolist()
        period_labels = period_dates['periodo_display'].tolist()

        if len(period_values) >= 2:
            x_range_min = period_values[0]
            x_range_max = period_values[-1]
        elif len(period_values) == 1:
            x_range_min = period_values[0] - pd.Timedelta(days=15)
            x_range_max = period_values[0] + pd.Timedelta(days=15)
        else:
            x_range_min = None
            x_range_max = None

        y_max = float(df_line_plot['total_facturado'].max()) if not df_line_plot.empty else 0.0
        y_range_max = y_max * 1.15 if y_max > 0 else 1

        palette_line = px.colors.qualitative.Set2
        color_map = {
            categoria: palette_line[idx % len(palette_line)]
            for idx, categoria in enumerate(top_categorias_line)
        }

        series_by_categoria = {}
        for categoria in top_categorias_line:
            serie = (
                df_line_plot[df_line_plot['tarifa_base'] == categoria][['periodo', 'total_facturado']]
                .set_index('periodo')
                .reindex(period_values)
                .fillna(0)
            )
            series_by_categoria[categoria] = serie['total_facturado'].tolist()

        def build_line_traces(segment_idx, t_value):
            traces = []
            for categoria in top_categorias_line:
                y_full = series_by_categoria[categoria]
                x_vals = list(period_values[:segment_idx + 1])
                y_vals = list(y_full[:segment_idx + 1])

                if len(period_values) > 1 and segment_idx < len(period_values) - 1 and t_value > 0:
                    x0 = period_values[segment_idx]
                    x1 = period_values[segment_idx + 1]
                    x_interp = x0 + (x1 - x0) * float(t_value)
                    y0 = float(y_full[segment_idx])
                    y1 = float(y_full[segment_idx + 1])
                    y_interp = y0 + (y1 - y0) * float(t_value)
                    x_vals.append(x_interp)
                    y_vals.append(y_interp)

                traces.append(
                    go.Scatter(
                        x=x_vals,
                        y=y_vals,
                        mode='lines+markers',
                        name=categoria,
                        line=dict(width=3, color=color_map[categoria]),
                        marker=dict(size=7, color=color_map[categoria]),
                        hovertemplate=(
                            '<b>%{fullData.name}</b><br>'
                            'Período: %{x|%m-%Y}<br>'
                            'Total Facturado: $%{y:,.0f}<extra></extra>'
                        ),
                    )
                )
            return traces

        substeps = 24
        frame_defs = []
        period_to_frame = {}

        frame_idx = 0
        first_frame = f'f{frame_idx:04d}'
        frame_defs.append((first_frame, period_labels[0], 0, 0.0))
        period_to_frame[period_labels[0]] = first_frame

        if len(period_values) > 1:
            for seg_idx in range(len(period_values) - 1):
                for step in range(1, substeps + 1):
                    frame_idx += 1
                    t_value = step / substeps
                    frame_name = f'f{frame_idx:04d}'
                    if step == substeps:
                        frame_label = period_labels[seg_idx + 1]
                        period_to_frame[frame_label] = frame_name
                    else:
                        frame_label = f'{period_labels[seg_idx]} → {period_labels[seg_idx + 1]}'
                    frame_defs.append((frame_name, frame_label, seg_idx, t_value))

        frames = [
            go.Frame(
                name=name,
                data=build_line_traces(seg_idx, t_value),
                layout=go.Layout(title_text=f'Período: {label}')
            )
            for name, label, seg_idx, t_value in frame_defs
        ]

        fig_line_race = go.Figure(
            data=build_line_traces(frame_defs[0][2], frame_defs[0][3]),
            frames=frames,
        )
        fig_line_race.update_layout(
            title=f'Período: {frame_defs[0][1]}',
            margin=dict(t=40, b=0, l=0, r=0),
            xaxis_title='Período',
            yaxis_title='Total Facturado',
            yaxis=dict(range=[0, y_range_max], tickprefix='$', tickformat=',.0f'),
            xaxis=dict(
                range=[x_range_min, x_range_max],
                tickformat='%m-%Y',
                dtick='M1',
                fixedrange=True,
            ),
            legend_title_text='Tarifa Base',
            updatemenus=[
                {
                    'type': 'buttons',
                    'showactive': False,
                    'x': 0.0,
                    'y': 1.15,
                    'xanchor': 'left',
                    'yanchor': 'top',
                    'direction': 'left',
                    'buttons': [
                        {
                            'label': '▶ Play',
                            'method': 'animate',
                            'args': [
                                [item[0] for item in frame_defs],
                                {
                                    'frame': {'duration': 42, 'redraw': True},
                                    'transition': {'duration': 0},
                                    'mode': 'immediate',
                                    'fromcurrent': True,
                                },
                            ],
                        },
                        {
                            'label': '⏸ Pause',
                            'method': 'animate',
                            'args': [[None], {'frame': {'duration': 0, 'redraw': False}, 'mode': 'immediate'}],
                        },
                    ],
                }
            ],
            sliders=[
                {
                    'active': 0,
                    'x': 0.0,
                    'y': -0.08,
                    'len': 1.0,
                    'pad': {'b': 0, 't': 30},
                    'currentvalue': {'prefix': 'Período: '},
                    'steps': [
                        {
                            'label': period_labels[idx],
                            'method': 'animate',
                            'args': [
                                [period_to_frame[period_labels[idx]]],
                                {
                                    'frame': {'duration': 0, 'redraw': True},
                                    'mode': 'immediate',
                                    'transition': {'duration': 250},
                                },
                            ],
                        }
                        for idx in range(len(period_labels))
                    ],
                }
            ],
        )

        st.caption("Datos simulados para 12 períodos (demo anual). Use ▶ Play para ver la evolución mensual por tarifa base.")
        st.plotly_chart(fig_line_race, width='stretch')
    else:
        st.info("Seleccione al menos un período para visualizar el line race.")
else:
    st.info("No hay datos disponibles para el line race por categoría.")