# -*- coding: utf-8 -*-
"""
Wrapper para sincronizar socios/medidores/tarifas de cualquier sector contra la BD.

Uso:
    # Simular sin persistir (recomendado primero)
    python scripts/sincronizar.py --sector energia --dry-run --export-reportes-csv

    # Ejecutar real
    python scripts/sincronizar.py --sector energia --export-reportes-csv

    # Con CSV especifico
    python scripts/sincronizar.py --sector energia --input data/energia/socios/socios_normalizados.csv
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SECTORES_DISPONIBLES = ["energia", "agua", "internet", "television", "gas"]


def main():
    parser = argparse.ArgumentParser(
        description="Sincroniza socios/medidores/tarifas de un sector contra la base de datos"
    )
    parser.add_argument(
        "--sector",
        required=True,
        choices=SECTORES_DISPONIBLES,
        help=f"Sector a sincronizar: {', '.join(SECTORES_DISPONIBLES)}"
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Ruta del CSV normalizado (por defecto data/<sector>/socios/socios_normalizados.csv)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta todo dentro de transaccion y hace rollback al final (sin persistir cambios)"
    )
    parser.add_argument(
        "--export-reportes-csv",
        action="store_true",
        help="Genera CSVs de control en data/<sector>/reportes_sincro/<timestamp>/"
    )

    args = parser.parse_args()

    if args.sector == "energia":
        from sectors.energia.sincronizador import ejecutar_sync, print_summary, DEFAULT_INPUT

        input_path = Path(args.input) if args.input else DEFAULT_INPUT

        if not input_path.exists():
            print(f"Error: No existe el archivo de entrada: {input_path}")
            sys.exit(1)

        summary = ejecutar_sync(
            input_path=input_path,
            dry_run=args.dry_run,
            export_reportes_csv_flag=args.export_reportes_csv,
        )
        print_summary(summary)
        sys.exit(0)
    else:
        print(f"Sector '{args.sector}' aun no implementado")
        sys.exit(1)


if __name__ == "__main__":
    main()
