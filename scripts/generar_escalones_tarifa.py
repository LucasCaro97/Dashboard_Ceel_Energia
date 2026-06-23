# -*- coding: utf-8 -*-
"""
Genera escalones_tarifa a partir del mapeo crudo -> base.

Reglas:
  - Tarifas SIN variantes de consumo (< 500, < 700, < 4000, > 2000, etc.)
    -> un solo escalon: limite_inferior=1, limite_superior=999999
  - Patron < 4000 + > 4000 (Comercial, Gobierno, Hoteles, Industrial, etc.)
    -> tres escalones: 1-2000 (nombre base), 2001-4000 (< 4000), 4001-999999 (> 4000)
  - Patron Social (> 500 + < 700 + < 1400 + > 1400)
    -> cuatro escalones: 1-500 (> 500), 501-700, 701-1400, 1401-999999
  - Otras tarifas CON variantes de consumo
    -> rangos contiguos derivados de los limites (< acumulativos y > superior)

Excluye escalones de potencia (> 300 kW en GU-BT/GU-MT): esas tarifas quedan
con un unico nivel 1-999999 hasta contar con columna de potencia.

Uso:
    python scripts/generar_escalones_tarifa.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MAPEO = ROOT / "data" / "energia" / "tarifas" / "tarifas_mapeo_crudo_a_base.csv"
DEFAULT_BASES = ROOT / "data" / "energia" / "tarifas" / "tarifas_base_limpias.csv"
OUTPUT_DIR = ROOT / "data" / "energia" / "tarifas"

LIMITE_MIN = 1
LIMITE_MAX = 999999
# Corte intermedio para tarifas con variantes < 4000 y > 4000
LIMITE_BASE_PATRON_4000 = 2000
LIMITE_TOPE_PATRON_4000 = 4000

PAT_ESCALON = re.compile(
    r"^\s*(<|>)\s*(\d+(?:\.\d+)?)\s*(kw|kW|KW)?\s*$",
    re.IGNORECASE,
)


def parse_escalon_consumo(texto: str) -> tuple[str, int] | None:
    """
    Parsea texto de escalon de consumo.
    Retorna ('<', N) o ('>', N), o None si no aplica (potencia, vacio, etc.).
    """
    texto = texto.strip()
    if not texto:
        return None

    match = PAT_ESCALON.match(texto)
    if not match:
        return None

    operador = match.group(1)
    valor = int(float(match.group(2)))
    unidad = (match.group(3) or "").lower()

    # > 300 kW = potencia (GU-BT / GU-MT), no escalon de consumo kWh
    if operador == ">" and unidad == "kw" and valor == 300:
        return None

    return operador, valor


def _parsear_limites(escalones_texto: list[str]) -> tuple[set[int], set[int]]:
    limites_menor: set[int] = set()
    limites_mayor: set[int] = set()

    for texto in escalones_texto:
        parsed = parse_escalon_consumo(texto)
        if not parsed:
            continue
        operador, valor = parsed
        if operador == "<":
            limites_menor.add(valor)
        else:
            limites_mayor.add(valor)

    return limites_menor, limites_mayor


def tiene_patron_4000(escalones_texto: list[str]) -> bool:
    """True si la tarifa tiene variantes < 4000 y > 4000 (Comercial, Gobierno, etc.)."""
    limites_menor, limites_mayor = _parsear_limites(escalones_texto)
    return (
        LIMITE_TOPE_PATRON_4000 in limites_menor
        and LIMITE_TOPE_PATRON_4000 in limites_mayor
    )


def construir_rangos(escalones_texto: list[str]) -> list[tuple[int, int]]:
    if tiene_patron_4000(escalones_texto):
        return [
            (LIMITE_MIN, LIMITE_BASE_PATRON_4000),
            (LIMITE_BASE_PATRON_4000 + 1, LIMITE_TOPE_PATRON_4000),
            (LIMITE_TOPE_PATRON_4000 + 1, LIMITE_MAX),
        ]

    limites_menor, limites_mayor = _parsear_limites(escalones_texto)

    if not limites_menor and not limites_mayor:
        return [(LIMITE_MIN, LIMITE_MAX)]

    if not limites_menor and limites_mayor:
        tope = min(limites_mayor)
        return [(LIMITE_MIN, tope), (tope + 1, LIMITE_MAX)]

    rangos: list[tuple[int, int]] = []
    prev = LIMITE_MIN

    # Tier inicial: variantes "> N" donde N es menor que el primer limite "<"
    # Ej: Social con Ahorro > 500 + < 700 -> escalon 1-500
    if limites_menor and limites_mayor:
        primer_menor = min(limites_menor)
        umbrales_iniciales = [v for v in limites_mayor if v < primer_menor]
        if umbrales_iniciales:
            umbral = max(umbrales_iniciales)
            rangos.append((prev, umbral))
            prev = umbral + 1

    for limite_superior in sorted(limites_menor):
        rangos.append((prev, limite_superior))
        prev = limite_superior + 1

    if limites_mayor:
        tope = max(limites_mayor)
        if prev <= tope:
            rangos.append((prev, tope))
            prev = tope + 1
        if prev <= LIMITE_MAX:
            rangos.append((prev, LIMITE_MAX))
    elif prev <= LIMITE_MAX:
        rangos.append((prev, LIMITE_MAX))

    return rangos


def _variantes_consumo(subset: pd.DataFrame) -> tuple[str | None, list[tuple[str, int, str]]]:
    """Retorna (nombre_plano_crudo, [(op, valor, nombre_crudo), ...])."""
    nombre_plano: str | None = None
    variantes: list[tuple[str, int, str]] = []

    for _, row in subset.iterrows():
        crudo = row["nombre_tarifa_crudo"].strip()
        escalon = row["escalon_texto"].strip()
        if not escalon:
            nombre_plano = crudo
            continue
        parsed = parse_escalon_consumo(escalon)
        if parsed:
            variantes.append((parsed[0], parsed[1], crudo))

    return nombre_plano, variantes


def asignar_nombre_escalon(
    nombre_tarifa: str,
    limite_inferior: int,
    limite_superior: int,
    subset: pd.DataFrame,
) -> str:
    """
    Asigna el nombre TRYLOGYC original al escalon segun sus limites.
    Ej: rango 1-500 de Residencial s/Subs. -> 'Residencial s/Subs. < 500'
    """
    nombre_plano, variantes = _variantes_consumo(subset)

    if not variantes:
        return nombre_plano or nombre_tarifa

    if tiene_patron_4000(subset["escalon_texto"].tolist()):
        if (
            limite_inferior == LIMITE_MIN
            and limite_superior == LIMITE_BASE_PATRON_4000
        ):
            return nombre_plano or nombre_tarifa
        for operador, valor, crudo in variantes:
            if (
                operador == "<"
                and valor == LIMITE_TOPE_PATRON_4000
                and limite_inferior == LIMITE_BASE_PATRON_4000 + 1
                and limite_superior == LIMITE_TOPE_PATRON_4000
            ):
                return crudo
            if (
                operador == ">"
                and valor == LIMITE_TOPE_PATRON_4000
                and limite_inferior == LIMITE_TOPE_PATRON_4000 + 1
            ):
                return crudo
        return nombre_plano or nombre_tarifa

    for operador, valor, crudo in variantes:
        if (
            operador == ">"
            and limite_inferior == LIMITE_MIN
            and limite_superior == valor
        ):
            return crudo

    for operador, valor, crudo in variantes:
        if operador == "<" and limite_superior == valor:
            return crudo

    for operador, valor, crudo in variantes:
        if operador == ">" and limite_inferior == valor + 1:
            return crudo

    limites_mayor = [valor for operador, valor, _ in variantes if operador == ">"]
    if limites_mayor and limite_superior == min(limites_mayor) and nombre_plano:
        return nombre_plano

    return nombre_plano or nombre_tarifa


def generar(
    mapeo_path: Path,
    bases_path: Path,
    output_dir: Path,
) -> pd.DataFrame:
    mapeo = pd.read_csv(mapeo_path, dtype=str, keep_default_na=False, encoding="utf-8")
    bases = pd.read_csv(bases_path, dtype=str, keep_default_na=False, encoding="utf-8")

    filas: list[dict] = []
    id_escalon = 1

    for _, base_row in bases.iterrows():
        id_tarifa = int(base_row["id_tarifa"])
        nombre = base_row["nombre_tarifa"]
        subset = mapeo[mapeo["nombre_tarifa"] == nombre]
        escalones_texto = subset["escalon_texto"].tolist()
        rangos = construir_rangos(escalones_texto)
        multi = len(rangos) > 1 or (
            len(rangos) == 1 and rangos[0] != (LIMITE_MIN, LIMITE_MAX)
        )

        for orden, (inf, sup) in enumerate(rangos, start=1):
            nombre_escalon = asignar_nombre_escalon(nombre, inf, sup, subset)
            filas.append(
                {
                    "id_escalon": id_escalon,
                    "id_tarifa": id_tarifa,
                    "nombre_escalon": nombre_escalon,
                    "nombre_tarifa": nombre,
                    "orden_escalon": orden,
                    "limite_inferior": inf,
                    "limite_superior": sup,
                    "escalones_texto_origen": " | ".join(
                        sorted({t for t in escalones_texto if t.strip()})
                    ),
                    "multi_nivel": multi,
                    "precio": "0.0000",
                }
            )
            id_escalon += 1

    df = pd.DataFrame(filas)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "escalones_tarifa_limpios.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")

    sql_path = output_dir / "escalones_tarifa_insert.sql"
    alter_path = output_dir / "escalones_tarifa_alter.sql"
    with alter_path.open("w", encoding="utf-8") as f:
        f.write("-- Agregar columna nombre_escalon (si la tabla ya existe sin ella)\n")
        f.write("ALTER TABLE escalones_tarifa\n")
        f.write("  ADD COLUMN nombre_escalon VARCHAR(150) NOT NULL DEFAULT '' AFTER id_tarifa;\n")

    with sql_path.open("w", encoding="utf-8") as f:
        f.write("-- INSERT para escalones_tarifa (precio=0 pendiente de cargar)\n")
        f.write("-- Generado por scripts/generar_escalones_tarifa.py\n")
        f.write("-- Ejecutar escalones_tarifa_alter.sql antes si la tabla ya existe\n\n")
        f.write(
            "INSERT INTO escalones_tarifa "
            "(id_escalon, id_tarifa, nombre_escalon, limite_inferior, limite_superior, precio) VALUES\n"
        )
        rows = []
        for _, row in df.iterrows():
            nombre_esc = row["nombre_escalon"].replace("'", "''")
            rows.append(
                f"({row['id_escalon']}, {row['id_tarifa']}, '{nombre_esc}', "
                f"{row['limite_inferior']}, {row['limite_superior']}, {row['precio']})"
            )
        f.write(",\n".join(rows))
        f.write(";\n")

    multi_df = df[df["multi_nivel"]].groupby("nombre_tarifa").first().reset_index()
    single_count = df[~df["multi_nivel"]]["id_tarifa"].nunique()

    print(f"Tarifas con un solo nivel (1-999999): {single_count}")
    print(f"Tarifas con multiples niveles:        {multi_df['id_tarifa'].nunique()}")
    print(f"Total filas escalones_tarifa:         {len(df)}")
    print(f"CSV:  {csv_path}")
    print(f"SQL:  {sql_path}")
    print(f"ALTER: {alter_path}")
    print("\n--- Tarifas multi-nivel ---")
    for _, row in multi_df.sort_values("id_tarifa").iterrows():
        sub = df[df["id_tarifa"] == row["id_tarifa"]]
        rangos_txt = ", ".join(
            f"{r.limite_inferior}-{r.limite_superior}" for _, r in sub.iterrows()
        )
        print(f"  {row['id_tarifa']:>2}  {row['nombre_tarifa']:<28}  [{rangos_txt}]")

    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera escalones_tarifa desde mapeo crudo.")
    parser.add_argument("--mapeo", default=str(DEFAULT_MAPEO))
    parser.add_argument("--bases", default=str(DEFAULT_BASES))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generar(Path(args.mapeo), Path(args.bases), Path(args.output_dir))


if __name__ == "__main__":
    main()
