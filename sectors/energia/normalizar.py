# -*- coding: utf-8 -*-
"""
Normaliza el listado de socios exportado desde TRYLOGYC para el sector Energia.

Uso:
    python sectors/energia/normalizar.py [ruta_csv] [ruta_salida_csv]

Transformaciones aplicadas (via core.normalizar_base):
  - Extrae columnas 11 a 19 (1-indexadas).
  - Encoding latin-1 (formato TRYLOGYC).
  - nro_socio: "00000002/000001" -> "000002/0001" (6 digitos / 4 digitos).
  - documento: split "DNI-9048914" -> tipo_doc=DNI, nro_doc=9048914.
  - Todos los campos de texto: strip de espacios.
  - dom_consumo / dom_postal: saltos de linea TRYLOGYC -> espacio.
  - fecha_fuente: extraida del nombre del archivo (lista_socios_DDMMAAAA.csv).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.normalizar_base import normalizar as _normalizar_base, archivo_mas_reciente
from .config import SERVICIO_TIPO

ROOT = Path(__file__).resolve().parent.parent.parent
CARPETA_SOCIOS = ROOT / "data" / "energia" / "socios"
SALIDA_DEFAULT = CARPETA_SOCIOS / "socios_normalizados.csv"


def normalizar(ruta_csv, ruta_salida=SALIDA_DEFAULT):
    """
    Normaliza un CSV de socios desde TRYLOGYC para el sector Energia.

    Delega toda la logica a ``core.normalizar_base.normalizar``.
    El CSV de salida conserva todos los servicios; el filtro por
    ``servicio == "Energia"`` se aplica en el paso de sincronizacion.

    Args:
        ruta_csv: Ruta del CSV crudo de TRYLOGYC.
        ruta_salida: Ruta de destino del CSV normalizado.

    Returns:
        pd.DataFrame con todos los registros normalizados.
    """
    return _normalizar_base(
        ruta_csv=ruta_csv,
        ruta_salida=ruta_salida,
        servicio_tipo=SERVICIO_TIPO,
    )


def _archivo_mas_reciente():
    return archivo_mas_reciente(CARPETA_SOCIOS)


if __name__ == "__main__":
    ruta_entrada = Path(sys.argv[1]) if len(sys.argv) > 1 else _archivo_mas_reciente()
    ruta_salida = Path(sys.argv[2]) if len(sys.argv) > 2 else SALIDA_DEFAULT
    normalizar(ruta_entrada, ruta_salida)
