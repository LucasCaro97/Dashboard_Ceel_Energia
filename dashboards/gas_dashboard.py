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
    from streamlit_echarts import st_echarts
except ImportError:
    st_echarts = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# 1. Configuración de página
st.set_page_config(page_title="Dashboard Gas - CEEL", layout="wide")

# 2. Constantes
TOP_N_TARIFAS_DEFAULT = 6

# `facturacion_conceptos.servicio` debe coincidir con `servicios.nombre_servicio`
SERVICIO_FC = "GAS ENVASADO"

# Para consultar conceptos por servicio usamos LOWER(s.nombre_servicio)
SERVICIO_CONCEPT_QUERY = "gas envasado"


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
    """Total facturado del período para Gas (solo servicio GAS ENVASADO)."""
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
def get_conceptos_servicio_nombres():
    """
    Conceptos de gas marcados como servicio (es_consumo_total=1).
    Usado para excluir IVA/percepciones/etc en ranking/barras y evolución.
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
                  AND cm.es_consumo_total = 1
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
    """Ranking de conceptos de gas facturados por período (sin impuestos)."""
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


@st.cache_data
def get_ranking_servicios_historico(periodos):
    """Cantidad de usuarios por concepto para cada período."""
    cols = ["periodo", "nombre_concepto", "cantidad"]
    if not periodos:
        return pd.DataFrame(columns=cols)

    frames = []
    for periodo in periodos:
        df = get_ranking_servicios_por_periodo(periodo)
        if df.empty:
            continue
        part = df[["nombre_concepto", "cantidad"]].copy()
        part["periodo"] = periodo
        frames.append(part)

    if not frames:
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)[cols]


@st.cache_data
def get_facturacion_por_tarifa(periodo):
    """Distribución por tarifa desde sp_consolidado_gas_por_periodo."""
    cols = ["tarifa_aplicada", "total_facturado", "cantidad_socios"]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("CALL sp_consolidado_gas_por_periodo(:periodo)"),
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
            df["tarifa_aplicada"].astype(str).str.strip().replace("", "Sin Tarifa")
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
def _build_interactive_pie_html(df_values, label_col, value_col, palette, chart_id, hole=0.45):
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
    )
    fig.update_layout(
        showlegend=False,
        hovermode="closest",
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
        html,body{{margin:0;padding:0;background:transparent;overflow:hidden;}}
        .{wrap_cls}{{position:relative;width:100%;}}
        #{chart_div}{{width:100%;background:transparent;}}
        #{chart_div} .hoverlayer,#{chart_div} .hovertext{{display:none !important;pointer-events:none !important;}}
        #{leg_div}{{
            position:absolute;top:8px;right:8px;left:auto;z-index:10;display:flex;flex-direction:column;
            gap:3px;max-width:42%;padding:6px 8px;border-radius:6px;background:rgba(255,255,255,0.82);
            box-shadow:0 1px 4px rgba(0,0,0,0.05);
        }}
        .legend-item:hover{{opacity:0.7;}}
        .legend-label{{font-size:0.72rem;line-height:1.15;color:#7d7d7d;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
        .{chart_area}{{position:relative;width:100%;}}
        #{center_div}{{position:absolute;top:50%;left:50%;transform:translate(-50%, -50%);z-index:20;pointer-events:none;text-align:center;min-width:72px;visibility:hidden;}}
        #{center_div}.visible{{visibility:visible;}}
        .center-pct-{chart_id}{{font-size:2.75rem;font-weight:900;line-height:1;letter-spacing:-0.02em;color:#ffffff;text-shadow:-1px -1px 0 rgba(30,30,30,0.85),1px -1px 0 rgba(30,30,30,0.85),-1px  1px 0 rgba(30,30,30,0.85),1px  1px 0 rgba(30,30,30,0.85),0 0 6px rgba(0,0,0,0.35);}}
    </style>
    <div class="{wrap_cls}">
        <div id="{leg_div}">{legend_items}</div>
        <div class="{chart_area}">
            <div id="{center_div}"></div>
            <div id="{chart_div}" style="min-height:420px;"></div>
        </div>
    </div>
    <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
    <script>
        (function(){{
            const fig = {fig_json};
            const baseColors = {colors_json};
            const percents = {percents_json};
            const chart = document.getElementById('{chart_div}');
            const centerEl = document.getElementById('{center_div}');
            const legendItems = Array.from(document.querySelectorAll('#{leg_div} .legend-item'));
            const baseTextSize = (fig.data[0].textfont && fig.data[0].textfont.size) ? fig.data[0].textfont.size : 13;
            const hoverTextSize = baseTextSize + 3;
            const basePull = (fig.data[0].labels || []).map(() => 0);

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

            Plotly.newPlot(chart, fig.data, fig.layout, {{responsive:true, displayModeBar:false}}).then(() => {{
                chart.on('plotly_hover', ev => {{
                    if (ev && ev.points && ev.points.length) {{
                        setHighlight(ev.points[0].pointNumber);
                    }}
                }});
                chart.on('plotly_unhover', clearHighlight);
                clearHighlight();
            }});
        }})();
    </script>
    """


def _chunk_legend_rows(legend_data, max_chars_per_row=42):
    """Agrupa ítems de leyenda en filas según ancho estimado del texto."""
    rows, current, current_len = [], [], 0
    for name in legend_data:
        item_len = len(str(name)) + 6
        if current and current_len + item_len > max_chars_per_row:
            rows.append(current)
            current, current_len = [name], item_len
        else:
            current.append(name)
            current_len += item_len
    if current:
        rows.append(current)
    return rows


def _build_wrapped_legends(legend_data, legend_selected=None):
    """Varias leyendas apiladas para simular salto de línea sin scroll."""
    if legend_selected is None:
        legend_selected = {name: True for name in legend_data}

    rows = _chunk_legend_rows(legend_data)
    row_height = 24
    legends = []
    for row_idx, chunk in enumerate(rows):
        legends.append(
            {
                "data": chunk,
                "type": "plain",
                "orient": "horizontal",
                "left": "center",
                "bottom": row_idx * row_height,
                "itemGap": 12,
                "textStyle": {"fontSize": 11},
                "selected": {name: legend_selected.get(name, True) for name in chunk},
            }
        )
    legend_height = len(rows) * row_height + 10
    return legends, legend_height


def _build_usuarios_area_options(pivot, periodos, stack_order, legend_data, legend_selected=None):
    """Opciones ECharts para área apilada de cantidad por servicio."""
    periodos = list(periodos)
    periodo_labels = [pd.Timestamp(p).strftime("%m-%Y") for p in periodos]

    series = [
        {
            "name": name,
            "type": "line",
            "stack": "Total",
            "areaStyle": {},
            "emphasis": {"focus": "series"},
            "data": [int(pivot.loc[p, name]) for p in periodos],
        }
        for name in stack_order
    ]

    legends, legend_height = _build_wrapped_legends(legend_data, legend_selected)

    return {
        "title": {"text": "Usuarios por Servicio"},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross", "label": {"backgroundColor": "#6a7985"}},
        },
        "legend": legends,
        "toolbox": {"feature": {"saveAsImage": {}}},
        "grid": {"left": "3%", "right": "4%", "bottom": legend_height, "containLabel": True},
        "xAxis": [
            {
                "type": "category",
                "boundaryGap": False,
                "data": periodo_labels,
            }
        ],
        "yAxis": [{"type": "value", "name": "Cantidad de usuarios"}],
        "series": series,
    }


# ─── 6. Interfaz ──────────────────────────────────────────────────────────────
st.markdown(
    "<h3 style='margin-bottom:0;'>📊 Dashboard de Facturación - CEEL GAS</h3>",
    unsafe_allow_html=True,
)

st.sidebar.header("Filtros")
periodos_sorted = get_periodos_disponibles()
if not periodos_sorted:
    periodos_sorted = ["2026-01-01"]

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
total_facturado = get_total_facturado(periodo_sql)
cantidad_facturas = get_cantidad_facturas(periodo_sql)

col1, col2 = st.columns(2)
col1.metric("Total Facturado", f"${total_facturado:,.0f}")
col2.metric("Facturas Emitidas", f"{cantidad_facturas:,}")

st.markdown(
    "<hr style='margin:0.3rem 0;border:0;border-top:1px solid rgba(127,127,127,0.35);'/>",
    unsafe_allow_html=True,
)

# Barras: Total Facturado por Servicios (sin impuestos)
st.markdown("#### Total Facturado por Servicios (sin impuestos)")
df_servicios = get_ranking_servicios_por_periodo(periodo_sql)
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
        margin=dict(t=10, b=80, l=0, r=0),
        xaxis_title="Servicio",
        yaxis_title="Total Facturado ($)",
        xaxis={"categoryorder": "array", "categoryarray": x_order, "tickangle": -35},
        yaxis=dict(tickprefix="$", tickformat=",.0f"),
    )
    st.plotly_chart(fig_bars, width="stretch")
else:
    st.info("No hay datos de servicios para el período seleccionado.")

st.markdown(
    "<hr style='margin:0.35rem 0;border:0;border-top:1px solid rgba(127,127,127,0.25);'/>",
    unsafe_allow_html=True,
)

# Área histórica (stacks por servicio)
st.markdown("#### Evolución histórica (cantidad de usuarios por servicio)")

periodos_chart = sorted(periodos_sorted)
df_hist = get_ranking_servicios_historico(periodos_chart)

if df_hist.empty:
    st.info("No hay datos históricos para mostrar.")
elif st_echarts is None:
    st.warning(
        "Instalá `streamlit-echarts` para ver el gráfico histórico estilo Internet "
        "(pip install streamlit-echarts)."
    )
else:
    df_hist["periodo_dt"] = pd.to_datetime(df_hist["periodo"], errors="coerce")
    df_hist = df_hist.dropna(subset=["periodo_dt"])
    if df_hist.empty:
        st.info("No hay datos históricos para mostrar.")
    else:
        top_n = int(top_n_tarifas)
        top_services = (
            df_hist.groupby("nombre_concepto")["cantidad"]
            .sum()
            .sort_values(ascending=False)
            .head(top_n)
            .index
        )
        df_hist["nombre_concepto"] = df_hist["nombre_concepto"].where(
            df_hist["nombre_concepto"].isin(top_services), "Otros"
        )

        df_hist = df_hist.groupby(["periodo_dt", "nombre_concepto"], as_index=False)["cantidad"].sum()
        df_hist = df_hist.sort_values("periodo_dt")

        periodos = sorted(df_hist["periodo_dt"].unique())
        pivot = (
            df_hist.pivot(index="periodo_dt", columns="nombre_concepto", values="cantidad")
            .fillna(0)
            .reindex(periodos, fill_value=0)
        )

        totals = pivot.sum().sort_values()
        stack_order = totals.index.tolist()  # apila de menor a mayor
        legend_data = totals.sort_values(ascending=False).index.tolist()

        legend_selected = {name: True for name in legend_data}
        area_options = _build_usuarios_area_options(
            pivot=pivot,
            periodos=periodos,
            stack_order=stack_order,
            legend_data=legend_data,
            legend_selected=legend_selected,
        )

        st_echarts(
            options=area_options,
            key="gas_area_chart",
            height="500px",
        )

