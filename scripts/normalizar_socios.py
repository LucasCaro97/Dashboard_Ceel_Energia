# -*- coding: utf-8 -*-
"""
Normaliza el listado de socios exportado desde TRYLOGYC.

Uso:
    python scripts/normalizar_socios.py [ruta_csv] [ruta_salida_csv]

Transformaciones aplicadas:
  - Extrae columnas 11 a 19 (1-indexadas).
  - Encoding latin-1 (formato TRYLOGYC).
  - nro_socio: "00000002/000001" -> "000002/0001" (6 digitos / 4 digitos).
  - documento: split "DNI-9048914" -> tipo_doc=DNI, nro_doc=9048914.
  - Todos los campos de texto: strip de espacios.
  - fecha_fuente: extraida del nombre del archivo (lista_socios_DDMMAAAA.csv).
"""

import re
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CARPETA_SOCIOS = ROOT / "data" / "socios"
SALIDA_DEFAULT = CARPETA_SOCIOS / "socios_normalizados.csv"

COLUMNAS_CSV = {
    10: "nro_socio_raw",
    11: "nombre_socio",
    12: "documento_raw",
    13: "dom_consumo",
    14: "dom_postal",
    15: "servicio",
    16: "tarifa",
    17: "medidor",
    18: "estado",
}

# ---------------------------------------------------------------------------
# Helpers de normalizacion
# ---------------------------------------------------------------------------

def _normalizar_nro_socio(valor):
    """
    Convierte el formato TRYLOGYC al formato de la BD.
    "00000002/000001" -> "000002/0001"
    """
    valor = valor.strip()
    if "/" not in valor:
        return valor
    partes = valor.split("/", 1)
    titular = str(int(partes[0])).zfill(6)
    suministro = str(int(partes[1])).zfill(4)
    return f"{titular}/{suministro}"


def _split_documento(valor):
    """
    "DNI-9048914" -> ("DNI", "9048914")
    "S.D."        -> ("S.D.", "")
    """
    valor = valor.strip()
    match = re.match(r'^([^-]+)-(.+)$', valor)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return valor, ""


def _fecha_desde_nombre(path):
    """
    Extrae la fecha del nombre del archivo.
    "lista_socios_17062026.csv" -> "2026-06-17"
    """
    m = re.search(r'(\d{2})(\d{2})(\d{4})', path.stem)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Funcion principal
# ---------------------------------------------------------------------------

def normalizar(ruta_csv, ruta_salida=SALIDA_DEFAULT):
    ruta_csv = Path(ruta_csv)
    ruta_salida = Path(ruta_salida)

    print(f"Leyendo: {ruta_csv}")
    df_raw = pd.read_csv(
        ruta_csv,
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding="latin-1",
    )

    # Extraer columnas 11-19 (indices 10-18)
    indices = list(COLUMNAS_CSV.keys())
    df = df_raw.iloc[:, indices].copy()
    df.columns = list(COLUMNAS_CSV.values())

    print(f"  Filas originales: {len(df):,}")

    # Limpieza de espacios en todos los campos
    for col in df.columns:
        df[col] = df[col].str.strip()

    # nro_socio normalizado
    df["nro_socio"] = df["nro_socio_raw"].apply(_normalizar_nro_socio)
    df.drop(columns=["nro_socio_raw"], inplace=True)

    # documento -> tipo_doc + nro_doc
    doc_split = df["documento_raw"].apply(_split_documento)
    df["tipo_doc"] = doc_split.apply(lambda x: x[0])
    df["nro_doc"] = doc_split.apply(lambda x: x[1])
    df.drop(columns=["documento_raw"], inplace=True)

    # medidor vacio -> None
    df["medidor"] = df["medidor"].replace("", None)

    # fecha de extraccion desde nombre de archivo
    fecha_fuente = _fecha_desde_nombre(ruta_csv)
    df["fecha_fuente"] = fecha_fuente
    print(f"  Fecha del archivo: {fecha_fuente}")

    # Reordenar columnas
    df = df[[
        "nro_socio",
        "nombre_socio",
        "tipo_doc",
        "nro_doc",
        "dom_consumo",
        "dom_postal",
        "servicio",
        "tarifa",
        "medidor",
        "estado",
        "fecha_fuente",
    ]]

    # Resumen
    print("\n  Filas por servicio:")
    for servicio, cantidad in df["servicio"].value_counts().items():
        print(f"    {servicio:<30} {cantidad:>7,}")

    print("\n  Filas por estado:")
    for estado, cantidad in df["estado"].value_counts().items():
        print(f"    {estado:<30} {cantidad:>7,}")

    print(f"\n  Socios unicos (nro_socio): {df['nro_socio'].nunique():,}")
    print(f"  Socios con servicio Energia: {(df['servicio'] == 'Energia').sum():,}")

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta_salida, index=False, encoding="utf-8")
    print(f"\nGuardado: {ruta_salida}  ({len(df):,} filas)")
    return df


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _archivo_mas_reciente():
    candidatos = sorted(CARPETA_SOCIOS.glob("lista_socios_*.csv"), reverse=True)
    if not candidatos:
        raise FileNotFoundError(
            f"No se encontro ningun lista_socios_*.csv en {CARPETA_SOCIOS}"
        )
    return candidatos[0]


if __name__ == "__main__":
    ruta_entrada = Path(sys.argv[1]) if len(sys.argv) > 1 else _archivo_mas_reciente()
    ruta_salida = Path(sys.argv[2]) if len(sys.argv) > 2 else SALIDA_DEFAULT
    normalizar(ruta_entrada, ruta_salida)
