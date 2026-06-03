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
import numpy as np
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
@st.cache_data
def get_datos_base():
    query = """
    SELECT
        periodo,
        tarifa_base,
        categoria_calculada,
        consumo_kwh_real,
        dinero_energia_subdiario,
        dinero_otros_conceptos,
        total_factura
    FROM v_reporte_facturacion_energia
    """
    return pd.read_sql(query, engine)

@st.cache_data
def get_datos_facturacion_procesados(top_n):
    # Consulta a tu vista ya normalizada
    query = "SELECT tarifa_base, SUM(total_factura) as total_facturado FROM v_reporte_facturacion_energia GROUP BY tarifa_base"
    df_raw = pd.read_sql(query, engine)
    
    # Ordenar por valor descendente
    df_raw = df_raw.sort_values(by='total_facturado', ascending=False)
    
    # Lógica Top N + Otros
    if len(df_raw) > top_n:
        top = df_raw.head(top_n)
        otros = pd.DataFrame({
            'tarifa_base': ['Otros'],
            'total_facturado': [df_raw.iloc[top_n:]['total_facturado'].sum()]
        })
        df_final = pd.concat([top, otros], ignore_index=True)
    else:
        df_final = df_raw
        
    return df_final

@st.cache_data
def get_desglose_tarifa_por_periodo(periodo):
    query = text("""
    SELECT
        tarifa_base,
        COUNT(*) AS cantidad_facturas,
        SUM(consumo_kwh_real) AS consumo_kwh_real,
        SUM(dinero_energia_subdiario) AS dinero_energia_subdiario
    FROM conecciones_energia.v_reporte_facturacion_energia
    WHERE periodo = :periodo
    GROUP BY tarifa_base
    """)
    return pd.read_sql(query, engine, params={"periodo": periodo})

@st.cache_data
def get_consumidores_por_periodo(periodo):
    query = text("""
    SELECT
        tarifa_base,
        categoria_calculada,
        nro_socio,
        nombre_socio,
        COUNT(*) AS cantidad_facturas,
        SUM(consumo_kwh_real) AS consumo_kwh_real,
        SUM(dinero_energia_subdiario) AS importe_neto_energia
    FROM conecciones_energia.v_reporte_facturacion_energia
    WHERE periodo = :periodo
    GROUP BY tarifa_base, categoria_calculada, nro_socio, nombre_socio
    """)
    return pd.read_sql(query, engine, params={"periodo": periodo})

@st.cache_data
def get_detalle_correlacion_por_periodo(periodo):
    query = text("""
    SELECT
        tarifa_base,
        categoria_calculada,
        nro_socio,
        nombre_socio,
        nro_factura,
        consumo_kwh_real,
        dinero_energia_subdiario AS importe_neto_energia,
        costo_unitario_energia_pura
    FROM conecciones_energia.v_reporte_facturacion_energia
    WHERE periodo = :periodo
    """)
    return pd.read_sql(query, engine, params={"periodo": periodo})

# Carga de datos
df = get_datos_base()

# 5. Interfaz
st.markdown(
    "<h3 style='margin-bottom: 0;'>📊 Dashboard de Facturación - CEEL ENERGIA ( ABR - 2026)</h3>",
    unsafe_allow_html=True,
)

# Filtros laterales
st.sidebar.header("Filtros")
columna_servicio = 'servicio' if 'servicio' in df.columns else ('tarifa_base' if 'tarifa_base' in df.columns else None)
opciones_servicio = df[columna_servicio].dropna().unique() if columna_servicio else []
servicio = st.sidebar.multiselect("Servicio:", opciones_servicio, default=opciones_servicio)
periodo = st.sidebar.selectbox("Periodo:", df['periodo'].dropna().unique())
top_n_tarifas = st.sidebar.number_input(
    "Cantidad de categorías (Top N):",
    min_value=1,
    max_value=50,
    value=TOP_N_TARIFAS_DEFAULT,
    step=1,
)

df_tarifa_procesado = get_datos_facturacion_procesados(top_n_tarifas)

# Filtrar df
df_filtrado = df[df['periodo'] == periodo]
if columna_servicio:
    df_filtrado = df_filtrado[df_filtrado[columna_servicio].isin(servicio)]

# KPIs
def sum_if_exists(dataframe, column_names):
    for col in column_names:
        if col in dataframe.columns:
            return dataframe[col].sum()
    return 0

total_facturado = sum_if_exists(df_filtrado, ['total_factura', 'total'])
importe_neto_energia = sum_if_exists(df_filtrado, ['dinero_energia_subdiario'])
importe_otros_conceptos = sum_if_exists(df_filtrado, ['dinero_otros_conceptos'])
consumo_kwh_real = sum_if_exists(df_filtrado, ['consumo_kwh_real'])

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Facturado", f"${total_facturado:,.0f}")
col2.metric("Importe Neto Energía", f"${importe_neto_energia:,.0f}")
col3.metric("Importe Otros Conceptos", f"${importe_otros_conceptos:,.0f}")
col4.metric("Consumo kWh Real", f"{consumo_kwh_real:,.0f}")

st.markdown(
    "<hr style='margin:0.3rem 0;border:0;border-top:1px solid rgba(127,127,127,0.35);' />",
    unsafe_allow_html=True,
)

# 6. Gráfico de Anillo
st.subheader("Distribución de Facturación por Tarifa Base")

if not df_tarifa_procesado.empty:
    palette = px.colors.qualitative.Pastel
    slice_colors = [palette[index % len(palette)] for index in range(len(df_tarifa_procesado))]

    fig_donut = px.pie(
        df_tarifa_procesado, 
        values='total_facturado', 
        names='tarifa_base', 
        hole=0.4,
        color_discrete_sequence=slice_colors
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
            f'<span style="font-size:0.92rem;line-height:1.2;">{escape(str(row["tarifa_base"]))}</span>'
            f'</div>'
        )

    donut_html = fr"""
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
        #donut-chart {{
            width: 100%;
            height: 100%;
            background: transparent;
        }}
        .donut-wrap {{
            display: flex;
            align-items: flex-start;
            gap: 18px;
            width: 100%;
        }}
        .donut-main {{
            flex: 1 1 520px;
            min-width: 320px;
        }}
        .donut-legend {{
            flex: 0 0 220px;
            display: grid;
            align-content: flex-start;
            gap: 4px;
        }}
        .legend-item:hover {{
            opacity: 0.7;
        }}
    </style>
    <div class="donut-wrap">
        <div class="donut-main">
            <div id="donut-chart" style="width:100%;min-height:420px;"></div>
        </div>
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

        function themeTextColor() {{
            return window.matchMedia('(prefers-color-scheme: dark)').matches ? '#ffffff' : '#111827';
        }}

        function applyLegendTheme() {{
            const textColor = themeTextColor();
            legendItems.forEach((item) => {{
                item.style.color = textColor;
            }});
        }}

        function normalizeColor(color, alpha) {{
            if (!color) return color;
            if (color.startsWith('rgba')) {{
                return color.replace(/rgba\(([^)]+),\s*[^)]+\)/, `rgba($1, ${{alpha}})`);
            }}
            if (color.startsWith('rgb')) {{
                return color.replace(/rgb\(([^)]+)\)/, `rgba($1, ${{alpha}})`);
            }}
            if (color.startsWith('#')) {{
                const hex = color.slice(1);
                const fullHex = hex.length === 3 ? hex.split('').map((char) => char + char).join('') : hex;
                const number = parseInt(fullHex, 16);
                const red = (number >> 16) & 255;
                const green = (number >> 8) & 255;
                const blue = number & 255;
                return `rgba(${{red}}, ${{green}}, ${{blue}}, ${{alpha}})`;
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

        const schemeListener = window.matchMedia('(prefers-color-scheme: dark)');
        if (schemeListener.addEventListener) {{
            schemeListener.addEventListener('change', applyLegendTheme);
        }} else if (schemeListener.addListener) {{
            schemeListener.addListener(applyLegendTheme);
        }}

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
            applyLegendTheme();
            clearHighlight();
        }});
    </script>
    """

    components.html(donut_html, height=660, scrolling=False)
else:
    st.warning("No hay datos disponibles para mostrar el gráfico.")

st.markdown(
    "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
    unsafe_allow_html=True,
)

st.subheader("Desglose por Tarifa Base")

df_desglose_tarifa = get_desglose_tarifa_por_periodo(periodo)

if not df_desglose_tarifa.empty:
    df_desglose_tarifa = df_desglose_tarifa.copy()
    df_desglose_tarifa["precio_medio_por_kwh"] = (
        df_desglose_tarifa["dinero_energia_subdiario"]
        / df_desglose_tarifa["consumo_kwh_real"].replace(0, pd.NA)
    )

    df_grid = df_desglose_tarifa.rename(
        columns={
            "tarifa_base": "Tarifa_Base",
            "cantidad_facturas": "Cantidad_Facturas",
            "consumo_kwh_real": "Consumo_kWh",
            "dinero_energia_subdiario": "Importe_Neto_Energia",
            "precio_medio_por_kwh": "Precio_Medio_Por_kWh",
        }
    )[
        [
            "Tarifa_Base",
            "Cantidad_Facturas",
            "Consumo_kWh",
            "Importe_Neto_Energia",
            "Precio_Medio_Por_kWh",
        ]
    ]

    df_grid = df_grid.sort_values(by="Importe_Neto_Energia", ascending=False)

    st.dataframe(
        df_grid,
        width='stretch',
        hide_index=True,
        column_config={
            "Cantidad_Facturas": st.column_config.NumberColumn(format="%d"),
            "Consumo_kWh": st.column_config.NumberColumn(format="%,d"),
            "Importe_Neto_Energia": st.column_config.NumberColumn(format="$%,.0f"),
            "Precio_Medio_Por_kWh": st.column_config.NumberColumn(format="$%.4f"),
        },
    )
else:
    st.info("No hay datos para el período seleccionado en el desglose por tarifa.")

st.markdown(
    "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);' />",
    unsafe_allow_html=True,
)

st.subheader("Top 10 Consumidores por Categoría")

df_consumidores = get_consumidores_por_periodo(periodo)

if not df_consumidores.empty:
    df_consumidores = df_consumidores.copy()
    df_consumidores["cantidad_facturas"] = pd.to_numeric(
        df_consumidores["cantidad_facturas"], errors="coerce"
    ).fillna(0)
    df_consumidores["consumo_kwh_real"] = pd.to_numeric(
        df_consumidores["consumo_kwh_real"], errors="coerce"
    ).fillna(0)
    df_consumidores["importe_neto_energia"] = pd.to_numeric(
        df_consumidores["importe_neto_energia"], errors="coerce"
    ).fillna(0)

    tarifas_orden_df = get_desglose_tarifa_por_periodo(periodo)
    categorias_top10 = []
    if not tarifas_orden_df.empty:
        categorias_top10 = (
            tarifas_orden_df.sort_values(by="cantidad_facturas", ascending=False)["tarifa_base"]
            .dropna()
            .tolist()
        )
    if not categorias_top10:
        categorias_top10 = sorted(df_consumidores["tarifa_base"].dropna().unique().tolist())
    categoria_sel = st.selectbox(
        "Tarifa Base:",
        options=categorias_top10,
        key="top10_categoria",
    )

    if categoria_sel:
        df_plot = df_consumidores[df_consumidores["tarifa_base"] == categoria_sel].copy()
        df_plot["consumidor"] = (
            df_plot["nro_socio"].astype(str) + " - " + df_plot["nombre_socio"].fillna("")
        )

        df_top10_barras = df_plot.sort_values(by="consumo_kwh_real", ascending=False).head(10)
        df_top10_barras = df_top10_barras.sort_values(by="consumo_kwh_real", ascending=True)

        fig_top10_bar = px.bar(
            df_top10_barras,
            x="consumo_kwh_real",
            y="consumidor",
            color="tarifa_base",
            orientation="h",
            text="consumo_kwh_real",
            custom_data=["importe_neto_energia", "tarifa_base"],
        )
        fig_top10_bar.update_traces(
            texttemplate="%{text:,.0f}",
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Categoría: %{customdata[1]}<br>"
                "Consumo: %{x:,.0f} kWh<br>"
                "Importe Neto Energía: $%{customdata[0]:,.0f}<extra></extra>"
            ),
        )
        fig_top10_bar.update_layout(
            margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Consumo kWh Real",
            yaxis_title="Consumidor",
            legend_title_text="Tarifa Base",
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

st.subheader("Correlación: Consumo kWh vs Costo Promedio")

df_consumidores_corr = get_detalle_correlacion_por_periodo(periodo)

if not df_consumidores_corr.empty:
    df_consumidores_corr = df_consumidores_corr.copy()
    df_consumidores_corr["consumo_kwh_real"] = pd.to_numeric(
        df_consumidores_corr["consumo_kwh_real"], errors="coerce"
    ).fillna(0)
    df_consumidores_corr["importe_neto_energia"] = pd.to_numeric(
        df_consumidores_corr["importe_neto_energia"], errors="coerce"
    ).fillna(0)
    df_consumidores_corr["costo_unitario_energia_pura"] = pd.to_numeric(
        df_consumidores_corr["costo_unitario_energia_pura"], errors="coerce"
    ).fillna(0)

    categorias_corr = (
        df_consumidores_corr.groupby("categoria_calculada", dropna=True)
        .size()
        .sort_values(ascending=False)
        .index.tolist()
    )
    categoria_corr_sel = st.selectbox(
        "Tarifa Calculada:",
        options=categorias_corr,
        key="corr_categoria_calculada",
    )

    if categoria_corr_sel:
        df_corr = df_consumidores_corr[
            df_consumidores_corr["categoria_calculada"] == categoria_corr_sel
        ].copy()
        df_corr["consumidor"] = (
            df_corr["nro_socio"].astype(str) + " - " + df_corr["nombre_socio"].fillna("")
        )
        df_corr = df_corr[
            (df_corr["consumo_kwh_real"] > 0)
            & (df_corr["costo_unitario_energia_pura"] > 0)
        ].copy()

        if not df_corr.empty:
            st.caption(f"Registros graficados: {len(df_corr):,}")
            fig_corr = px.scatter(
                df_corr,
                x="consumo_kwh_real",
                y="costo_unitario_energia_pura",
                hover_name="consumidor",
                custom_data=[
                    "importe_neto_energia",
                    "nro_factura",
                    "categoria_calculada",
                    "costo_unitario_energia_pura",
                ],
                labels={
                    "consumo_kwh_real": "Consumo kWh Real",
                    "costo_unitario_energia_pura": "Costo Unitario Energía Pura",
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
                    "Tarifa Calculada: %{customdata[2]}<br>"
                    "Nro Factura: %{customdata[1]}<br>"
                    "Consumo: %{x:,.0f} kWh<br>"
                    "Costo Unitario Energía Pura: $%{customdata[3]:,.2f}<br>"
                    "Importe Neto Energía: $%{customdata[0]:,.0f}<extra></extra>"
                ),
            )

            if len(df_corr) >= 2 and df_corr["consumo_kwh_real"].nunique() > 1:
                x_vals = df_corr["consumo_kwh_real"].to_numpy(dtype=float)
                y_vals = df_corr["costo_unitario_energia_pura"].to_numpy(dtype=float)
                slope, intercept = np.polyfit(x_vals, y_vals, 1)
                x_line = np.array([x_vals.min(), x_vals.max()])
                y_line = slope * x_line + intercept
                fig_corr.add_trace(
                    go.Scatter(
                        x=x_line,
                        y=y_line,
                        mode="lines",
                        name="Tendencia",
                        line=dict(width=2.5, color="#1f77b4", dash="dash"),
                        hovertemplate=(
                            "Línea de tendencia<br>"
                            "y = {:.6f}x + {:.6f}<extra></extra>"
                        ).format(slope, intercept),
                    )
                )

            fig_corr.update_layout(
                margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Consumo kWh Real",
                yaxis_title="Costo Unitario Energía Pura",
                showlegend=True,
            )

            st.plotly_chart(fig_corr, width='stretch')
        else:
            st.info("No hay datos con consumo/costo unitario > 0 para calcular la correlación en la tarifa calculada seleccionada.")
    else:
        st.info("Seleccione una tarifa calculada para ver la correlación.")
else:
    st.info("No hay datos disponibles para la sección de correlación en el período seleccionado.")