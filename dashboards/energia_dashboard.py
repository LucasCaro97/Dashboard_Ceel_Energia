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

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# 1. Configuración de página
st.set_page_config(page_title="Dashboard CEEL", layout="wide")

# 2. Configuración de variables
TOP_N_TARIFAS_DEFAULT = 6
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

# Preparar datos para el gráfico de anillo (segmentación por grupos de servicio).
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
            st.info("No hay datos de tarifa base para el período seleccionado.")
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