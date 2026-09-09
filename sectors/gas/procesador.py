# -*- coding: utf-8 -*-
"""
Processor for Gas (GAS ENVASADO) billing.
Injects billing concept data from TXT into the database.
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


def _mapear_servicio_real(servicio_norm: str) -> str:
    """
    Mapea el servicio normalizado (ej: gas_envasado) al nombre exacto
    en la tabla `servicios` (ej: 'GAS ENVASADO'), para que los JOINs por
    nombre funcionen en MySQL.
    """
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


def procesar_periodo(anio, mes, sector="gas"):
    """
    Reads TXT billing files for a specific period and normalizes them.
    TXT naming: <servicio>_<id_concepto>.txt (e.g. gas_1.txt)
    """
    ruta_periodo = f"./data/{sector}/inbox/{anio}/{mes}"
    archivos = glob.glob(os.path.join(ruta_periodo, "**", "*.txt"), recursive=True)

    if not archivos:
        print(f"No files found in {ruta_periodo}")
        return None

    dataframes = []
    for archivo in archivos:
        print(f"Processing: {os.path.basename(archivo)}")
        df = pd.read_csv(archivo, sep=";", encoding="latin1")
        df = df.drop(df.columns[0], axis=1).iloc[:, 0:8]
        df.columns = [
            "Socio_Con",
            "Nombre",
            "Direccion",
            "Nro_Factura",
            "Socio",
            "Cantidad",
            "Importe",
            "Total",
        ]
        df = df.dropna(how="all")

        nombre_base = os.path.basename(archivo).replace(".txt", "")
        partes = nombre_base.rsplit("_", 1)
        df["id_concepto"] = int(partes[1])

        servicio_txt = partes[0].lower()
        df["servicio"] = SERVICIO_TXT_ALIASES.get(servicio_txt, servicio_txt)
        df["periodo"] = f"{anio}-{mes}-01"
        dataframes.append(df)

    return pd.concat(dataframes, ignore_index=True)


def procesar_facturacion(anio, mes, sector="gas", dry_run=False):
    """
    Complete billing processing pipeline for Gas:
    1. Reads TXTs from data/gas/inbox/{anio}/{mes}/
    2. Validates against master concepts
    3. Injects into facturacion_conceptos (skipped in dry_run)
    4. Generates control Excel in data/gas/processed/{anio}/{mes}/
    """
    modo = "[DRY RUN] " if dry_run else ""
    print(f"--- {modo}Starting Gas billing processing ---")

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
        df_final[col] = (
            df_final[col]
            .astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        df_final[col] = pd.to_numeric(df_final[col], errors="coerce").fillna(0).round(2)

    df_final = pd.merge(df_final, df_maestro, on=["servicio", "id_concepto"], how="left")

    faltantes = (
        df_final[df_final["nombre_concepto"].isna()][["servicio", "id_concepto"]]
        .drop_duplicates()
    )
    if not faltantes.empty:
        print("\n--- ERROR: CONCEPTS NOT FOUND IN MASTER! ---")
        print("The following concepts are in files but NOT in database:")
        print(faltantes)
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

    # En la tabla facturacion_conceptos, `servicio` debe coincidir con `servicios.nombre_servicio`
    # para que los JOINs en SQL/SP funcionen.
    if not df_final.empty and "servicio" in df_final.columns:
        servicio_norm = str(df_final["servicio"].iloc[0]).strip()
        df_final["servicio"] = _mapear_servicio_real(servicio_norm)

    ruta_salida = f"./data/{sector}/processed/{anio}/{mes}"
    os.makedirs(ruta_salida, exist_ok=True)
    sufijo = "_dry_run" if dry_run else ""
    nombre_archivo = f"{ruta_salida}/{sector.upper()}_conceptos_facturados_{anio}_{mes}{sufijo}.xlsx"
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

    parser = argparse.ArgumentParser(description="Procesa facturacion Gas (GAS ENVASADO).")
    parser.add_argument("--año", required=True)
    parser.add_argument("--mes", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida archivos y conceptos sin inyectar en la BD.",
    )
    args = parser.parse_args()
    procesar_facturacion(args.año, args.mes, dry_run=args.dry_run)

