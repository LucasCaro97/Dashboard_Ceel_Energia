# -*- coding: utf-8 -*-
"""
Processor for Agua Potable billing.
Injects billing concept data from TRYLOGYC TXT into the database.
"""

import glob
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db_manager import (
    build_sqlalchemy_engine,
    get_db_config,
    inyectar_a_mysql,
    obtener_maestro_conceptos,
)
from core.dry_run_report import imprimir_resumen_dry_run
from .config import SERVICIO_TXT_ALIASES, TABLA_FACTURACION


def _parsear_numero_locale(valor) -> float:
    """Convierte importes TRYLOGYC (coma decimal) sin romper floats ya parseados."""
    if pd.isna(valor):
        return 0.0
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return round(float(valor), 2)
    texto = str(valor).strip()
    if not texto or texto.lower() == "nan":
        return 0.0
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return round(float(pd.to_numeric(texto, errors="coerce") or 0), 2)


def _extraer_columnas_facturacion(df: pd.DataFrame) -> pd.DataFrame:
    """
    TRYLOGYC agua puede exportar 10 o 12 columnas.
    En el formato largo hay dos pares Cantidad/Importe antes de TOTAL;
    se usa el par con datos (ej. CF vacío + Cap con importe).
    """
    df = df.drop(df.columns[0], axis=1)
    socio = df.iloc[:, :5].copy()
    socio.columns = [
        "Socio_Con",
        "Nombre",
        "Direccion",
        "Nro_Factura",
        "Socio",
    ]

    rest_cols = [c for c in df.columns[5:] if not str(c).startswith("Unnamed")]
    rest = df[rest_cols]
    total_col = rest_cols[-1]
    pair_cols = rest_cols[:-1]

    pairs = [
        (pair_cols[i], pair_cols[i + 1])
        for i in range(0, len(pair_cols), 2)
        if i + 1 < len(pair_cols)
    ]
    if not pairs:
        raise ValueError(
            f"Formato TXT agua inválido: columnas de facturación insuficientes ({rest_cols})"
        )

    cant_col, imp_col = max(pairs, key=lambda p: rest[p[1]].notna().sum())

    out = socio.copy()
    out["Cantidad"] = rest[cant_col]
    out["Importe"] = rest[imp_col]
    out["Total"] = rest[total_col]
    return out


def _mapear_servicio_real(servicio_norm: str) -> str:
    """TXT agua_* -> nombre exacto en tabla servicios (Agua Potable)."""
    try:
        engine = build_sqlalchemy_engine(get_db_config())
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT nombre_servicio
                    FROM servicios
                    WHERE LOWER(REPLACE(nombre_servicio, ' ', '_')) = :sn
                    LIMIT 1
                    """
                ),
                {"sn": servicio_norm},
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return servicio_norm


def procesar_periodo(anio, mes, sector="agua"):
    """Reads TXT billing files for a specific period (agua_<id>.txt)."""
    ruta_periodo = f"./data/{sector}/inbox/{anio}/{mes}"
    archivos = glob.glob(os.path.join(ruta_periodo, "**", "*.txt"), recursive=True)

    if not archivos:
        print(f"No files found in {ruta_periodo}")
        return None

    dataframes = []
    for archivo in archivos:
        print(f"Processing: {os.path.basename(archivo)}")
        df = pd.read_csv(archivo, sep=";", encoding="latin1")
        df = _extraer_columnas_facturacion(df)
        df = df.dropna(how="all")

        nombre_base = os.path.basename(archivo).replace(".txt", "")
        partes = nombre_base.rsplit("_", 1)
        df["id_concepto"] = int(partes[1])

        servicio_txt = partes[0].lower()
        df["servicio"] = SERVICIO_TXT_ALIASES.get(servicio_txt, servicio_txt)
        df["periodo"] = f"{anio}-{mes}-01"
        dataframes.append(df)

    return pd.concat(dataframes, ignore_index=True)


def procesar_facturacion(anio, mes, sector="agua", dry_run=False):
    """
    Complete billing processing pipeline for Agua:
    1. Reads TXTs from data/agua/inbox/{anio}/{mes}/
    2. Validates against master concepts (servicio=Agua Potable, id_servicio=2)
    3. Injects into facturacion_conceptos (skipped in dry_run)
    4. Generates control Excel in data/agua/processed/{anio}/{mes}/
    """
    modo = "[DRY RUN] " if dry_run else ""
    print(f"--- {modo}Starting Agua billing processing ---")

    df_final = procesar_periodo(anio, mes, sector)
    if df_final is None:
        print("CRITICAL: Could not process text files.")
        return False
    print(f"Files processed. Total rows: {len(df_final)}")

    df_maestro = obtener_maestro_conceptos()
    if df_maestro is None:
        print("CRITICAL: Database connection failed.")
        return False
    print("Database connected and master loaded.")

    for col in ["Importe", "Total", "Cantidad"]:
        df_final[col] = df_final[col].apply(_parsear_numero_locale)

    df_final = pd.merge(df_final, df_maestro, on=["servicio", "id_concepto"], how="left")

    faltantes = (
        df_final[df_final["nombre_concepto"].isna()][["servicio", "id_concepto"]]
        .drop_duplicates()
    )
    if not faltantes.empty:
        print("\n--- ERROR: CONCEPTS NOT FOUND IN MASTER! ---")
        print("The following concepts are in files but NOT in database:")
        print(faltantes)
        print("Run: python scripts/cargar_conceptos_agua.py")
        print("-------------------------------------------------------\n")
        return False

    df_final = df_final.rename(
        columns={
            "Socio_Con": "nro_socio",
            "Nombre": "nombre_socio",
            "Nro_Factura": "nro_factura",
            "Socio": "es_socio",
            "Cantidad": "cantidad_cons",
            "Importe": "importe",
            "Total": "total",
        }
    )

    if dry_run:
        imprimir_resumen_dry_run(df_final, anio, mes, TABLA_FACTURACION)

    df_final = df_final.drop(
        columns=[
            "nombre_concepto",
            "es_consumo_total",
            "grupo_usuario",
            "es_consumo_escalonado",
            "Direccion",
        ],
        errors="ignore",
    )

    if not df_final.empty and "servicio" in df_final.columns:
        servicio_norm = str(df_final["servicio"].iloc[0]).strip()
        df_final["servicio"] = _mapear_servicio_real(servicio_norm)

    ruta_salida = f"./data/{sector}/processed/{anio}/{mes}"
    os.makedirs(ruta_salida, exist_ok=True)
    sufijo = "_dry_run" if dry_run else ""
    nombre_archivo = (
        f"{ruta_salida}/AGUA_conceptos_facturados_{anio}_{mes}{sufijo}.xlsx"
    )
    df_final.to_excel(nombre_archivo, index=False)
    print(f"File generated: {nombre_archivo}")

    if dry_run:
        return True

    if inyectar_a_mysql(df_final, TABLA_FACTURACION):
        print("--- Processing finished successfully ---")
        return True
    print("--- Processing finished with error ---")
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Procesa facturacion Agua Potable.")
    parser.add_argument("--año", required=True)
    parser.add_argument("--mes", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida archivos y conceptos sin inyectar en la BD.",
    )
    args = parser.parse_args()
    procesar_facturacion(args.año, args.mes, dry_run=args.dry_run)
