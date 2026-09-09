"""App única con pestañas para los dashboards sectoriales implementados."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


IMPLEMENTED_DASHBOARDS = [
    {
        "slug": "energia",
        "label": "Energía",
        "icon": "⚡",
        "path": ROOT / "energia_dashboard.py",
    },
    {
        "slug": "internet",
        "label": "Internet",
        "icon": "🌐",
        "path": ROOT / "internet_dashboard.py",
    },
    {
        "slug": "television",
        "label": "Televisión",
        "icon": "📺",
        "path": ROOT / "television_dashboard.py",
    },
    {
        "slug": "gas",
        "label": "Gas",
        "icon": "🔥",
        "path": ROOT / "gas_dashboard.py",
    },
    {
        "slug": "agua",
        "label": "Agua",
        "icon": "💧",
        "path": ROOT / "agua_dashboard.py",
    },
]


SESSION_STATE_KEYS = [
    "usuarios_area_chart",
    "usuarios_kpi_servicios",
    "_usuarios_legend_key",
    "_usuarios_legend_selected",
    "corr_segmentar_por_tarifa_base",
]


def _prefix_explicit_widget_keys(source: str, slug: str) -> str:
    """Prefija keys explícitos para evitar colisiones entre dashboards."""
    source = re.sub(
        r"key=(['\"])([^'\"]+)\1",
        lambda m: f"key={m.group(1)}{slug}_{m.group(2)}{m.group(1)}",
        source,
    )
    source = re.sub(
        r"key=f(['\"])",
        lambda m: f"key=f{m.group(1)}{slug}_",
        source,
    )
    return source


def _prefix_session_state_keys(source: str, slug: str) -> str:
    """Aísla claves conocidas de session_state que varios dashboards comparten."""
    for key in SESSION_STATE_KEYS:
        source = source.replace(f"st.session_state.{key}", f"st.session_state.{slug}_{key}")
        source = source.replace(f"st.session_state.get(\"{key}\"", f"st.session_state.get(\"{slug}_{key}\"")
        source = source.replace(f"st.session_state.get('{key}'", f"st.session_state.get('{slug}_{key}'")
        source = source.replace(f"st.session_state[\"{key}\"]", f"st.session_state[\"{slug}_{key}\"]")
        source = source.replace(f"st.session_state['{key}']", f"st.session_state['{slug}_{key}']")
    return source


def _prepare_dashboard_source(source: str, slug: str) -> str:
    """
    Adapta scripts standalone para renderizarlos dentro de una tab:
    - mueve filtros de sidebar al contenido de la pestaña;
    - prefija keys/widgets/session_state para evitar colisiones.
    """
    source = source.replace("st.sidebar.", "st.")
    source = _prefix_explicit_widget_keys(source, slug)
    source = _prefix_session_state_keys(source, slug)
    return source


def _render_dashboard_script(path: Path, slug: str) -> None:
    if not path.exists():
        st.info(f"Dashboard no encontrado: `{path.name}`")
        return

    source = _prepare_dashboard_source(path.read_text(encoding="utf-8"), slug)
    namespace = {
        "__file__": str(path),
        "__name__": f"__dashboard_{slug}__",
        "__package__": None,
    }

    def _wrap_widget(fn, fn_name):
        counter = {"value": 0}

        def _wrapped(*args, **kwargs):
            if "key" not in kwargs or kwargs["key"] is None:
                counter["value"] += 1
                kwargs["key"] = f"{slug}_{fn_name}_{counter['value']}"
            return fn(*args, **kwargs)

        return _wrapped

    # Cada dashboard standalone llama st.set_page_config; en la app contenedora
    # esa llamada debe ser única, así que la ignoramos solo durante el exec.
    # También prefijamos widgets sin key para evitar colisiones entre tabs.
    original_set_page_config = st.set_page_config
    original_selectbox = st.selectbox
    original_number_input = st.number_input
    st.set_page_config = lambda *args, **kwargs: None
    st.selectbox = _wrap_widget(original_selectbox, "selectbox")
    st.number_input = _wrap_widget(original_number_input, "number_input")
    try:
        exec(compile(source, str(path), "exec"), namespace)
    finally:
        st.set_page_config = original_set_page_config
        st.selectbox = original_selectbox
        st.number_input = original_number_input


def main() -> None:
    st.set_page_config(page_title="Dashboards CEEL", layout="wide")
    st.markdown(
        """
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
            [data-testid="stTabs"] {
                margin-bottom: 0.15rem;
            }
            [data-testid="stTabs"] [data-baseweb="tab-list"] {
                gap: 0.35rem;
            }
            iframe[title="streamlit_echarts.st_echarts"] {
                width: 100% !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<h2 style='margin:0 0 0.35rem 0;font-size:1.25rem;line-height:1.2;'>Dashboards de Facturación CEEL</h2>",
        unsafe_allow_html=True,
    )

    dashboards = [item for item in IMPLEMENTED_DASHBOARDS if item["path"].exists()]
    tabs = st.tabs([f"{item['icon']} {item['label']}" for item in dashboards])

    for tab, item in zip(tabs, dashboards):
        with tab:
            _render_dashboard_script(item["path"], item["slug"])


if __name__ == "__main__":
    main()
