# -*- coding: utf-8 -*-
"""
Limpia nombres de tarifa TRYLOGYC para la tabla tarifas_base.

Quita escalones del nombre (< 500, > 4000, > 300 kW, etc.), aplica equivalencias
conocidas y deduplica por (id_servicio, nombre_tarifa).

Uso:
    python scripts/limpiar_tarifas_base.py
    python scripts/limpiar_tarifas_base.py --input data/energia/tarifas/tarifas_crudas.csv
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_INPUT = ROOT / "data" / "energia" / "tarifas" / "tarifas_crudas.csv"
OUTPUT_DIR = ROOT / "data" / "energia" / "tarifas"

# Nomenclatura TRYLOGYC -> nomenclatura BD (misma lógica que sincronizador)
EQUIVALENCIAS_EXACTAS = {
    "Entes de Radiodif.y Telev": "Entes Radio TV",
    "GU-ME >300 KW PEAJE": "GU-ME Peaje",
}

# Sufijos de escalón al final del nombre (consumo kWh o potencia kW)
PAT_ESCALON = re.compile(
    r"\s*(<|>|>=|<=)\s*(\d+(?:\.\d+)?)\s*(kw|kW|KW)?(?:\s+PEAJE)?\s*$",
    re.IGNORECASE,
)


def colapsar_espacios(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.strip())


def quitar_escalon(nombre: str) -> tuple[str, str | None]:
    """Devuelve (nombre_base, texto_escalon) o (nombre, None) si no hay escalón."""
    nombre = colapsar_espacios(nombre)
    if nombre in EQUIVALENCIAS_EXACTAS:
        return EQUIVALENCIAS_EXACTAS[nombre], None

    match = PAT_ESCALON.search(nombre)
    if not match:
        base = EQUIVALENCIAS_EXACTAS.get(nombre, nombre)
        return base, None

    base = colapsar_espacios(nombre[: match.start()])
    escalon = colapsar_espacios(match.group(0))
    base = EQUIVALENCIAS_EXACTAS.get(base, base)
    return base, escalon


def limpiar(input_path: Path, output_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8")
    required = {"id_servicio", "nombre_tarifa_crudo"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en CSV: {sorted(missing)}")

    parsed = df["nombre_tarifa_crudo"].apply(quitar_escalon)
    df["nombre_tarifa"] = parsed.apply(lambda x: x[0])
    df["escalon_texto"] = parsed.apply(lambda x: x[1] if x[1] else "")

    output_dir.mkdir(parents=True, exist_ok=True)

    mapping_path = output_dir / "tarifas_mapeo_crudo_a_base.csv"
    df.to_csv(mapping_path, index=False, encoding="utf-8")

    dedup = (
        df.groupby(["id_servicio", "nombre_tarifa"], as_index=False)
        .agg(
            variantes_crudas=("nombre_tarifa_crudo", lambda s: " | ".join(sorted(set(s)))),
            cantidad_variantes=("nombre_tarifa_crudo", "nunique"),
        )
        .sort_values(["id_servicio", "nombre_tarifa"])
        .reset_index(drop=True)
    )
    dedup.insert(0, "id_tarifa", range(1, len(dedup) + 1))

    limpias_path = output_dir / "tarifas_base_limpias.csv"
    dedup.to_csv(limpias_path, index=False, encoding="utf-8")

    sql_path = output_dir / "tarifas_base_insert.sql"
    with sql_path.open("w", encoding="utf-8") as f:
        f.write("-- INSERT para tarifas_base (Energia, id_servicio=1)\n")
        f.write("-- Generado por scripts/limpiar_tarifas_base.py\n\n")
        f.write("INSERT INTO tarifas_base (id_tarifa, id_servicio, nombre_tarifa) VALUES\n")
        rows = []
        for _, row in dedup.iterrows():
            nombre = row["nombre_tarifa"].replace("'", "''")
            rows.append(f"({row['id_tarifa']}, {row['id_servicio']}, '{nombre}')")
        f.write(",\n".join(rows))
        f.write(";\n")

    print(f"Filas crudas:           {len(df):,}")
    print(f"Tarifas base unicas:    {len(dedup):,}")
    print(f"Mapeo detallado:        {mapping_path}")
    print(f"CSV limpio:             {limpias_path}")
    print(f"SQL INSERT:             {sql_path}")
    print("\n--- Tarifas base (sin escalones) ---")
    for _, row in dedup.iterrows():
        print(f"  {row['id_tarifa']:>3}  {row['nombre_tarifa']}")

    return dedup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Limpia nombres de tarifa para tarifas_base.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="CSV con tarifas crudas")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Carpeta de salida")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limpiar(Path(args.input), Path(args.output_dir))


if __name__ == "__main__":
    main()
