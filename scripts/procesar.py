"""
Script wrapper para procesar facturación de cualquier sector.

Uso:
    python scripts/procesar.py --sector energia --año 2026 --mes 05
    python scripts/procesar.py --sector internet --año 2026 --mes 05 --dry-run
    python scripts/procesar.py --sector agua --año 2026 --mes 06
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sectors.energia.procesador import procesar_facturacion
from sectors.internet.procesador import procesar_facturacion as procesar_facturacion_internet


SECTORES_DISPONIBLES = ["energia", "agua", "internet", "television", "gas"]


def main():
    parser = argparse.ArgumentParser(
        description="Procesa facturación de un sector específico"
    )
    parser.add_argument(
        "--sector",
        required=True,
        choices=SECTORES_DISPONIBLES,
        help=f"Sector a procesar: {', '.join(SECTORES_DISPONIBLES)}"
    )
    parser.add_argument(
        "--año",
        required=True,
        help="Año a procesar (ej: 2026)"
    )
    parser.add_argument(
        "--mes",
        required=True,
        help="Mes a procesar (ej: 05)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida archivos y conceptos sin inyectar datos en la BD.",
    )

    args = parser.parse_args()

    if args.sector == "energia":
        success = procesar_facturacion(args.año, args.mes, sector="energia", dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    elif args.sector == "internet":
        success = procesar_facturacion_internet(args.año, args.mes, sector="internet", dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    else:
        print(f"Sector '{args.sector}' aún no implementado")
        sys.exit(1)


if __name__ == "__main__":
    main()
