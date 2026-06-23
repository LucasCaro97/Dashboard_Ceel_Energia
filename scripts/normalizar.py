# -*- coding: utf-8 -*-
"""
Wrapper para normalizar el CSV de socios de cualquier sector.

Uso:
    python scripts/normalizar.py --sector energia
    python scripts/normalizar.py --sector agua --input data/agua/socios/lista_socios_17062026.csv
    python scripts/normalizar.py --sector gas --input <ruta_csv> --output <ruta_salida>

Nota: El CSV crudo exportado desde TRYLOGYC tiene la misma estructura para todos
los sectores. El archivo de salida conserva todos los servicios; el filtro por
sector se aplica en el paso de sincronizacion.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SECTORES_DISPONIBLES = ["energia", "agua", "gas", "internet", "television"]

_SECTOR_MODULOS = {
    "energia": "sectors.energia.normalizar",
    "agua": "sectors.agua.normalizar",
    "gas": "sectors.gas.normalizar",
    "internet": "sectors.internet.normalizar",
    "television": "sectors.television.normalizar",
}


def main():
    parser = argparse.ArgumentParser(
        description="Normaliza el CSV de socios exportado desde TRYLOGYC"
    )
    parser.add_argument(
        "--sector",
        required=True,
        choices=SECTORES_DISPONIBLES,
        help=f"Sector a normalizar: {', '.join(SECTORES_DISPONIBLES)}",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Ruta del CSV crudo (por defecto toma el lista_socios_*.csv mas reciente del sector)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta del CSV normalizado de salida (por defecto data/<sector>/socios/socios_normalizados.csv)",
    )

    args = parser.parse_args()

    import importlib
    modulo = importlib.import_module(_SECTOR_MODULOS[args.sector])

    ruta_entrada = Path(args.input) if args.input else modulo._archivo_mas_reciente()
    ruta_salida = Path(args.output) if args.output else modulo.SALIDA_DEFAULT

    modulo.normalizar(ruta_entrada, ruta_salida)
    sys.exit(0)


if __name__ == "__main__":
    main()
