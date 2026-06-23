"""Pipeline mensual de sincronizacion de socios de Gas desde TRYLOGYC."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.sector_sync import (
    SectorSyncConfig,
    SyncSummary,
    ejecutar_sync_sector,
    print_summary_sector,
)
from .config import (
    DB_SCHEMA,
    SERVICIO_TIPO,
    TABLA_SOCIOS,
    TABLA_MEDIDORES,
    TABLA_TARIFAS,
    TABLA_TARIFA_BASE,
    TARIFA_EQUIVALENCIAS,
)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "gas" / "socios" / "socios_normalizados.csv"

GAS_CONFIG = SectorSyncConfig(
    servicio_objetivo=SERVICIO_TIPO,
    db_schema=DB_SCHEMA,
    tabla_socios=TABLA_SOCIOS,
    tabla_medidores=TABLA_MEDIDORES,
    tabla_tarifas=TABLA_TARIFAS,
    tabla_tarifa_base=TABLA_TARIFA_BASE,
    tarifa_equivalencias=TARIFA_EQUIVALENCIAS,
    reportes_dir=ROOT / "data" / "gas" / "reportes_sincro",
)


def ejecutar_sync(
    input_path: Path,
    dry_run: bool,
    export_reportes_csv_flag: bool,
) -> SyncSummary:
    """Sincroniza socios/medidores/tarifas del sector Gas."""
    return ejecutar_sync_sector(
        input_path=input_path,
        dry_run=dry_run,
        export_reportes_csv_flag=export_reportes_csv_flag,
        config=GAS_CONFIG,
    )


def print_summary(summary: SyncSummary) -> None:
    print_summary_sector(summary, GAS_CONFIG)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sincroniza socios/medidores/tarifas de Gas."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Ruta del CSV normalizado (default: data/gas/socios/socios_normalizados.csv).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta todo dentro de transaccion y hace rollback al final.",
    )
    parser.add_argument(
        "--export-reportes-csv",
        action="store_true",
        help="Genera CSVs de control en data/gas/reportes_sincro/.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")

    summary = ejecutar_sync(
        input_path=input_path,
        dry_run=args.dry_run,
        export_reportes_csv_flag=args.export_reportes_csv,
    )
    print_summary(summary)


if __name__ == "__main__":
    main()
