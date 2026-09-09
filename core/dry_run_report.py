# -*- coding: utf-8 -*-
"""Salida de consola para dry-run de facturación."""

from __future__ import annotations

import pandas as pd


def imprimir_resumen_dry_run(df, anio, mes, tabla_destino):
    """
    Imprime totales generales y desglose por concepto para cruzar con Trylogic.

    Espera un DataFrame ya renombrado con al menos:
    ``nro_socio``, ``id_concepto``, ``importe``, ``total``.
    Si existe ``nombre_concepto`` / ``servicio``, se incluyen en el detalle.
    """
    print("\n--- DRY RUN SUMMARY (nothing was written to the database) ---")
    print(f"  Periodo         : {anio}-{mes}")
    print(f"  Filas totales   : {len(df):,}")
    print(f"  Socios unicos   : {df['nro_socio'].nunique():,}")
    print(f"  Conceptos únicos: {df['id_concepto'].nunique():,}")
    print(f"  Total importe   : {df['importe'].sum():,.2f}")
    print(f"  Total general   : {df['total'].sum():,.2f}")
    print(f"  Tabla destino   : {tabla_destino}")

    group_cols = []
    if "servicio" in df.columns:
        group_cols.append("servicio")
    group_cols.append("id_concepto")
    if "nombre_concepto" in df.columns:
        group_cols.append("nombre_concepto")

    detalle = (
        df.groupby(group_cols, as_index=False, dropna=False)
        .agg(
            filas=("nro_socio", "size"),
            importe=("importe", "sum"),
            total=("total", "sum"),
        )
        .sort_values(group_cols)
        .reset_index(drop=True)
    )

    print("\n--- DETALLE POR CONCEPTO (para cruzar con Trylogic) ---")
    header = (
        f"  {'servicio':<22} {'id':>6}  {'nombre_concepto':<42} "
        f"{'filas':>8}  {'importe':>16}  {'total':>16}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for _, row in detalle.iterrows():
        servicio = str(row["servicio"]) if "servicio" in detalle.columns else "-"
        concepto_id = int(row["id_concepto"]) if pd.notna(row["id_concepto"]) else 0
        if "nombre_concepto" in detalle.columns and pd.notna(row["nombre_concepto"]):
            nombre = str(row["nombre_concepto"])
        else:
            nombre = "(sin nombre)"
        if len(nombre) > 42:
            nombre = nombre[:39] + "..."
        print(
            f"  {servicio:<22} {concepto_id:>6}  {nombre:<42} "
            f"{int(row['filas']):>8,}  {float(row['importe']):>16,.2f}  {float(row['total']):>16,.2f}"
        )

    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':<22} {'':>6}  {'':<42} "
        f"{len(df):>8,}  {df['importe'].sum():>16,.2f}  {df['total'].sum():>16,.2f}"
    )
    print("-------------------------------------------------------------")
    print("--- DRY RUN OK: run without --dry-run to inject into DB ---")
