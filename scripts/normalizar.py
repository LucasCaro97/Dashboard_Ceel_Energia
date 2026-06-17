# -*- coding: utf-8 -*-
"""
Wrapper para normalizar el CSV de socios de cualquier sector.

Uso:
    python scripts/normalizar.py --sector energia
    python scripts/normalizar.py --sector energia --input data/energia/socios/lista_socios_17062026.csv
    python scripts/normalizar.py --sector energia --input <ruta_csv> --output <ruta_salida>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SECTORES_DISPONIBLES = ["energia", "agua", "internet", "television", "gas"]


def main():
    parser = argparse.ArgumentParser(
        description="Normaliza el CSV de socios exportado desde TRYLOGYC"
    )
    parser.add_argument(
        "--sector",
        required=True,
        choices=SECTORES_DISPONIBLES,
        help=f"Sector a normalizar: {', '.join(SECTORES_DISPONIBLES)}"
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Ruta del CSV crudo (por defecto toma el lista_socios_*.csv mas reciente del sector)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ruta del CSV normalizado de salida (por defecto data/<sector>/socios/socios_normalizados.csv)"
    )

    args = parser.parse_args()

    if args.sector == "energia":
        from sectors.energia.normalizar import normalizar, _archivo_mas_reciente, SALIDA_DEFAULT

        ruta_entrada = Path(args.input) if args.input else _archivo_mas_reciente()
        ruta_salida = Path(args.output) if args.output else SALIDA_DEFAULT

        normalizar(ruta_entrada, ruta_salida)
        sys.exit(0)
    else:
        print(f"Sector '{args.sector}' aun no implementado")
        sys.exit(1)


if __name__ == "__main__":
    main()
