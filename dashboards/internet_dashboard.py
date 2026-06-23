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

try:
    from streamlit_echarts import st_echarts
except ImportError:
    st_echarts = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# 1. Configuración de página
st.set_page_config(page_title="Dashboard Internet - CEEL", layout="wide")

# 2. Constantes
TOP_N_TARIFAS_DEFAULT = 6
VIEW_INTERNET = "conecciones_energia.v_consolidado_internet"
SERVICIO_TIPO  = "internet"

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


# 5. Funciones de carga (todas cacheadas)

@st.cache_data
def get_periodos_disponibles():
    try:
        df = pd.read_sql(
            text(
                f"""
                SELECT DISTINCT periodo
                FROM {VIEW_INTERNET}
                WHERE periodo IS NOT NULL
                ORDER BY periodo DESC
                """
            ),
            engine,
        )
        if df is None or df.empty:
            return []
        return pd.to_datetime(df["periodo"], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist()
    except Exception:
        return []


@st.cache_data
def get_total_facturado_sp(periodo):
    """Total facturado para el período, obtenido desde sp_kpi_por_sector."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("CALL sp_kpi_por_sector('internet')"))
            rows = result.fetchall()
            cols = list(result.keys())

        if not rows:
            return 0.0

        df = pd.DataFrame(rows, columns=cols)
        df["periodo"] = pd.to_datetime(df["periodo"], errors="coerce")

        if periodo:
            periodo_ts = pd.to_datetime(periodo, errors="coerce")
            match = df[df["periodo"] == periodo_ts]
            if match.empty:
                return 0.0
            val = match.iloc[0]["total_facturado"]
        else:
            val = df["total_facturado"].sum()

        return float(val) if val is not None else 0.0
    except Exception:
        return 0.0


@st.cache_data
def get_cantidad_facturas(periodo):
    """Cantidad de facturas desde la vista (la procedure no lo provee)."""
    try:
        params = {"periodo": periodo} if periodo else {}
        where = "WHERE periodo = :periodo" if periodo else ""
        df = pd.read_sql(
            text(
                f"""
                SELECT COUNT(DISTINCT nro_factura) AS cantidad_facturas
                FROM {VIEW_INTERNET}
                {where}
                """
            ),
            engine,
            params=params,
        )
        if df is None or df.empty:
            return 0
        return int(df.iloc[0]["cantidad_facturas"] or 0)
    except Exception:
        return 0


@st.cache_data
def get_ranking_servicios_por_periodo(periodo):
    """Ranking de servicios facturados por período desde sp_ranking_servicios_por_periodo."""
    cols = ["nombre_concepto", "cantidad", "total"]
    if not periodo:
        return pd.DataFrame(columns=cols)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("CALL sp_ranking_servicios_por_periodo(:periodo, :sector)"),
                {"periodo": periodo, "sector": SERVICIO_TIPO},
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
        return df[df["total"] > 0].sort_values("total", ascending=False).reset_index(drop=True)[cols]
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data
def get_ranking_servicios_historico(periodos):
    """Cantidad de usuarios por servicio para cada período (vía SP)."""
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


def _prepare_usuarios_pivot(df_hist, top_n):
    """Prepara pivot de cantidad por período y servicio."""
    if df_hist is None or df_hist.empty:
        return None

    df = df_hist.copy()
    df["periodo"] = pd.to_datetime(df["periodo"], errors="coerce")
    df = df.dropna(subset=["periodo"])
    if df.empty:
        return None

    periodos = sorted(df["periodo"].unique())
    periodo_labels = [pd.Timestamp(p).strftime("%m-%Y") for p in periodos]

    top_servicios = (
        df.groupby("nombre_concepto")["cantidad"]
        .sum()
        .sort_values(ascending=False)
        .head(int(top_n))
        .index.tolist()
    )

    df["nombre_concepto"] = df["nombre_concepto"].where(
        df["nombre_concepto"].isin(top_servicios), "Otros"
    )
    df = df.groupby(["periodo", "nombre_concepto"], as_index=False)["cantidad"].sum()

    pivot = (
        df.pivot(index="periodo", columns="nombre_concepto", values="cantidad")
        .fillna(0)
        .reindex(periodos, fill_value=0)
    )

    totals = pivot.sum().sort_values()
    stack_order = totals.index.tolist()
    legend_data = totals.sort_values(ascending=False).index.tolist()

    return {
        "pivot": pivot,
        "periodos": periodos,
        "periodo_labels": periodo_labels,
        "stack_order": stack_order,
        "legend_data": legend_data,
    }


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
        legends.append({
            "data": chunk,
            "type": "plain",
            "orient": "horizontal",
            "left": "center",
            "bottom": row_idx * row_height,
            "itemGap": 12,
            "textStyle": {"fontSize": 11},
            "selected": {name: legend_selected.get(name, True) for name in chunk},
        })
    legend_height = len(rows) * row_height + 10
    return legends, legend_height


def _build_usuarios_area_options(pivot, periodos, stack_order, legend_data, legend_selected=None):
    """Opciones ECharts para área apilada de cantidad de usuarios por servicio."""
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


def _calc_usuarios_kpis(pivot, selected_map):
    """KPIs de socios según servicios visibles en la leyenda."""
    if pivot is None or pivot.empty:
        return None

    if selected_map:
        cols = [c for c in pivot.columns if selected_map.get(c, True)]
    else:
        cols = pivot.columns.tolist()

    if not cols:
        return {
            "inicio": 0,
            "fin": 0,
            "variacion": "estable",
            "tasa_anual": 0.0,
            "tasa_mensual": 0.0,
            "periodo_inicio": "",
            "periodo_fin": "",
            "servicios_activos": 0,
        }

    sub = pivot[cols]
    inicio = int(sub.iloc[0].sum())
    fin = int(sub.iloc[-1].sum())
    n_intervalos = max(1, len(sub) - 1)

    if inicio <= 0:
        variacion, tasa_anual, tasa_mensual = "estable", 0.0, 0.0
    elif fin < inicio:
        tasa_anual = (inicio - fin) / inicio * 100
        tasa_mensual = tasa_anual / n_intervalos
        variacion = "baja"
    elif fin > inicio:
        tasa_anual = (fin - inicio) / inicio * 100
        tasa_mensual = tasa_anual / n_intervalos
        variacion = "alta"
    else:
        variacion, tasa_anual, tasa_mensual = "estable", 0.0, 0.0

    delta_usuarios = fin - inicio
    delta_usuarios_mensual = delta_usuarios / n_intervalos

    return {
        "inicio": inicio,
        "fin": fin,
        "variacion": variacion,
        "tasa_anual": tasa_anual,
        "tasa_mensual": tasa_mensual,
        "delta_usuarios": delta_usuarios,
        "delta_usuarios_mensual": delta_usuarios_mensual,
        "periodo_inicio": pd.Timestamp(sub.index[0]).strftime("%m-%Y"),
        "periodo_fin": pd.Timestamp(sub.index[-1]).strftime("%m-%Y"),
        "servicios_activos": len(cols),
    }


def _extract_legend_selected(raw, legend_names):
    """Extrae el mapa {servicio: bool} devuelto por ECharts al togglear la leyenda."""
    if not raw or not isinstance(raw, dict):
        return None

    payload = raw.get("chart_event", raw)
    if not isinstance(payload, dict):
        return None

    legend_set = set(legend_names)
    if not legend_set & set(payload.keys()):
        return None

    return {name: bool(payload.get(name, True)) for name in legend_names}


def _sync_usuarios_kpi_servicios(legend_names):
    """Sincroniza el filtro de KPIs desde el estado del gráfico (leyenda)."""
    selected = _extract_legend_selected(
        st.session_state.get("usuarios_area_chart"),
        legend_names,
    )
    if selected:
        st.session_state.usuarios_kpi_servicios = [
            name for name, active in selected.items() if active
        ]


@st.cache_data
def get_facturacion_por_tarifa(periodo):
    """Total facturado agrupado por tarifa_aplicada para el período."""
    cols = ["tarifa_aplicada", "total_facturado", "cantidad_socios"]
    try:
        params = {"periodo": periodo} if periodo else {}
        where = "WHERE periodo = :periodo" if periodo else ""
        df = pd.read_sql(
            text(
                f"""
                SELECT
                    COALESCE(NULLIF(TRIM(tarifa_aplicada), ''), 'Sin Tarifa') AS tarifa_aplicada,
                    SUM(COALESCE(total_factura, 0)) AS total_facturado,
                    COUNT(DISTINCT nro_socio)        AS cantidad_socios
                FROM {VIEW_INTERNET}
                {where}
                GROUP BY COALESCE(NULLIF(TRIM(tarifa_aplicada), ''), 'Sin Tarifa')
                ORDER BY total_facturado DESC
                """
            ),
            engine,
            params=params,
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=cols)
        df["total_facturado"] = pd.to_numeric(df["total_facturado"], errors="coerce").fillna(0.0)
        return df[df["total_facturado"] > 0][cols]
    except Exception:
        return pd.DataFrame(columns=cols)


# ─── Utilidad: gráfico de torta/donut interactivo ─────────────────────────────

def _build_interactive_pie_html(df_values, label_col, value_col, palette, chart_id, hole=0.45):
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

    wrap_cls     = f"wrap-{chart_id}"
    chart_div    = f"chart-{chart_id}"
    leg_div      = f"legend-{chart_id}"
    center_div   = f"center-{chart_id}"
    chart_area   = f"chart-area-{chart_id}"

    return fr"""
    <style>
        html,body{{margin:0;padding:0;background:transparent;overflow:hidden;}}
        .{wrap_cls}{{position:relative;width:100%;}}
        #{chart_div}{{width:100%;background:transparent;}}
        #{chart_div} .hoverlayer,
        #{chart_div} .hovertext{{
            display:none !important;
            pointer-events:none !important;
        }}
        #{leg_div}{{
            position:absolute;
            top:8px;
            right:8px;
            left:auto;
            z-index:10;
            display:flex;
            flex-direction:column;
            gap:3px;
            max-width:42%;
            padding:6px 8px;
            border-radius:6px;
            background:rgba(255,255,255,0.82);
            box-shadow:0 1px 4px rgba(0,0,0,0.05);
        }}
        .legend-item:hover{{opacity:0.7;}}
        .legend-label{{
            font-size:0.72rem;
            line-height:1.15;
            color:#7d7d7d;
            font-weight:500;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }}
        .{chart_area}{{
            position:relative;
            width:100%;
        }}
        #{center_div}{{
            position:absolute;
            top:50%;
            left:50%;
            transform:translate(-50%, -50%);
            z-index:20;
            pointer-events:none;
            text-align:center;
            min-width:72px;
            visibility:hidden;
        }}
        #{center_div}.visible{{
            visibility:visible;
        }}
        .center-pct-{chart_id}{{
            font-size:2.75rem;
            font-weight:900;
            line-height:1;
            letter-spacing:-0.02em;
            font-stretch:expanded;
            color:#ffffff;
            text-shadow:
                -1px -1px 0 rgba(30,30,30,0.85),
                 1px -1px 0 rgba(30,30,30,0.85),
                -1px  1px 0 rgba(30,30,30,0.85),
                 1px  1px 0 rgba(30,30,30,0.85),
                 0    0   6px rgba(0,0,0,0.35);
        }}
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
                centerEl.innerHTML =
                    '<div class="center-pct-{chart_id}">' + pct.toFixed(1) + '%</div>';
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


# ─── 6. Interfaz ──────────────────────────────────────────────────────────────

st.markdown(
    "<h3 style='margin-bottom:0;'>🌐 Dashboard de Facturación - CEEL INTERNET</h3>",
    unsafe_allow_html=True,
)

# Filtros laterales
st.sidebar.header("Filtros")
periodos_sorted = get_periodos_disponibles()
if not periodos_sorted:
    periodos_sorted = ["2026-05-01"]

periodo_display = st.sidebar.selectbox("Periodo:", periodos_sorted)
periodo_sql = to_periodo_sql(periodo_display)

top_n_tarifas = st.sidebar.number_input(
    "Cantidad de categorías (Top N):",
    min_value=1,
    max_value=50,
    value=TOP_N_TARIFAS_DEFAULT,
    step=1,
)

# ── KPIs ──────────────────────────────────────────────────────────────────────
total_facturado = get_total_facturado_sp(periodo_sql)
cantidad_facturas = get_cantidad_facturas(periodo_sql)

col1, col2 = st.columns(2)
col1.metric("Total Facturado",   f"${total_facturado:,.0f}")
col2.metric("Facturas Emitidas", f"{cantidad_facturas:,}")

st.markdown(
    "<hr style='margin:0.3rem 0;border:0;border-top:1px solid rgba(127,127,127,0.35);'/>",
    unsafe_allow_html=True,
)

# ── Gráficos de distribución ──────────────────────────────────────────────────
df_tarifas = get_facturacion_por_tarifa(periodo_sql)
df_servicios = get_ranking_servicios_por_periodo(periodo_sql)

left_col, right_col = st.columns(2)

with left_col:
    st.markdown("#### Distribución por Tarifa Usuario")
    if not df_tarifas.empty:
        df_chart = df_tarifas.copy()
        if len(df_chart) > top_n_tarifas:
            top_part  = df_chart.head(top_n_tarifas)
            otros_val = df_chart.iloc[top_n_tarifas:]["total_facturado"].sum()
            otros_row = pd.DataFrame({"tarifa_aplicada": ["Otros"], "total_facturado": [otros_val], "cantidad_socios": [0]})
            df_chart  = pd.concat([top_part, otros_row], ignore_index=True)

        html_pie = _build_interactive_pie_html(
            df_chart, "tarifa_aplicada", "total_facturado",
            px.colors.qualitative.Set3, "pie-inet", hole=0.45,
        )
        components.html(html_pie, height=500, scrolling=False)
    else:
        st.info("No hay datos de tarifas para el período seleccionado.")

with right_col:
    st.markdown("#### Total Facturado por Servicios")
    if not df_servicios.empty:
        df_bars = df_servicios.copy()
        if len(df_bars) > top_n_tarifas:
            top_part  = df_bars.head(top_n_tarifas)
            otros_val = df_bars.iloc[top_n_tarifas:]["total"].sum()
            otros_cant = df_bars.iloc[top_n_tarifas:]["cantidad"].sum()
            otros_row = pd.DataFrame({"nombre_concepto": ["Otros"], "total": [otros_val], "cantidad": [otros_cant]})
            df_bars = pd.concat([top_part, otros_row], ignore_index=True)

        df_bars = df_bars.sort_values("total", ascending=False).copy()
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

# ── Área apilada: usuarios por servicio ───────────────────────────────────────
st.markdown("#### Evolución de Usuarios por Servicio")

periodos_chart = sorted(periodos_sorted)
df_usuarios_hist = get_ranking_servicios_historico(periodos_chart)
usuarios_ctx = _prepare_usuarios_pivot(df_usuarios_hist, top_n_tarifas)

if st_echarts is None:
    st.warning("Instale streamlit-echarts para ver este gráfico: pip install streamlit-echarts")
elif usuarios_ctx is None:
    st.info("No hay datos históricos de usuarios por servicio.")
else:
    pivot = usuarios_ctx["pivot"]
    legend_data = usuarios_ctx["legend_data"]
    legend_key = tuple(legend_data)
    if st.session_state.get("_usuarios_legend_key") != legend_key:
        st.session_state.usuarios_kpi_servicios = list(legend_data)
        st.session_state._usuarios_legend_key = legend_key

    servicios_activos = st.session_state.get("usuarios_kpi_servicios", list(legend_data))
    legend_selected = {name: (name in servicios_activos) for name in legend_data}
    area_options = _build_usuarios_area_options(
        pivot,
        usuarios_ctx["periodos"],
        usuarios_ctx["stack_order"],
        legend_data,
        legend_selected=legend_selected,
    )

    chart_col, kpi_col = st.columns([3, 1])

    with chart_col:
        st_echarts(
            options=area_options,
            events={
                "legendselectchanged": "function(params){ return params.selected; }",
            },
            on_change=lambda: _sync_usuarios_kpi_servicios(legend_data),
            key="usuarios_area_chart",
            height="500px",
        )

    with kpi_col:
        st.markdown("##### Indicadores")
        selected_map = {name: (name in servicios_activos) for name in legend_data}
        kpis = _calc_usuarios_kpis(pivot, selected_map)

        if kpis:
            st.metric(
                "Socios (inicio)",
                f"{kpis['inicio']:,}",
                help=f"Período {kpis['periodo_inicio']}",
            )
            st.metric(
                "Socios (fin)",
                f"{kpis['fin']:,}",
                delta=f"{kpis['fin'] - kpis['inicio']:,}",
                help=f"Período {kpis['periodo_fin']}",
            )
            if kpis["variacion"] == "baja":
                label_anual = "Tasa desconexión (período)"
                label_mensual = "Tasa desconexión mensual"
                help_anual = "Porcentaje de usuarios perdidos entre el inicio y el fin del rango"
                help_mensual = "Promedio mensual de la tasa de desconexión en el rango"
                delta_anual = f"{kpis['delta_usuarios']:,}"
                delta_mensual = f"{kpis['delta_usuarios_mensual']:,.0f}"
            elif kpis["variacion"] == "alta":
                label_anual = "Tasa crecimiento (período)"
                label_mensual = "Tasa crecimiento mensual"
                help_anual = "Porcentaje de usuarios ganados entre el inicio y el fin del rango"
                help_mensual = "Promedio mensual de la tasa de crecimiento en el rango"
                delta_anual = f"{kpis['delta_usuarios']:,}"
                delta_mensual = f"{kpis['delta_usuarios_mensual']:,.0f}"
            else:
                label_anual = "Variación neta (período)"
                label_mensual = "Variación mensual promedio"
                help_anual = "Sin cambio neto de usuarios entre inicio y fin"
                help_mensual = "Sin cambio neto promedio por mes"
                delta_anual = None
                delta_mensual = None

            st.metric(
                label_anual,
                f"{kpis['tasa_anual']:.1f}%",
                delta=delta_anual,
                help=help_anual,
            )
            st.metric(
                label_mensual,
                f"{kpis['tasa_mensual']:.1f}%",
                delta=delta_mensual,
                help=help_mensual,
            )
            st.caption(
                f"Servicios activos: {kpis['servicios_activos']}. "
                "Filtrá haciendo clic en la leyenda del gráfico."
            )
