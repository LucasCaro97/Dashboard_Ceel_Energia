# -*- coding: utf-8 -*-
"""
Normaliza el listado de socios exportado desde TRYLOGYC para el sector Agua.

Uso:
    python sectors/agua/normalizar.py [ruta_csv] [ruta_salida_csv]

El CSV crudo tiene la misma estructura que el sector Energia (columnas 10-18).
La normalizacion es identica; el filtro por servicio se aplica en la sincronizacion.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.normalizar_base import normalizar as _normalizar_base, archivo_mas_reciente
from .config import SERVICIO_TIPO

ROOT = Path(__file__).resolve().parent.parent.parent
CARPETA_SOCIOS = ROOT / "data" / "agua" / "socios"
SALIDA_DEFAULT = CARPETA_SOCIOS / "socios_normalizados.csv"


def normalizar(ruta_csv, ruta_salida=SALIDA_DEFAULT):
    """
    Normaliza un CSV de socios desde TRYLOGYC para el sector Agua.

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
