# -*- coding: utf-8 -*-
"""
Logica base de normalizacion para exportaciones TRYLOGYC.

Reutilizable por todos los sectores que exportan el mismo formato
de listado de socios (columnas 10-18 del CSV crudo de TRYLOGYC).
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

# Indices de columnas extraidas del CSV crudo de TRYLOGYC (0-based)
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


def _normalizar_nro_socio(valor: str) -> str:
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


def _split_documento(valor: str):
    """
    "DNI-9048914" -> ("DNI", "9048914")
    "S.D."        -> ("S.D.", "")
    """
    valor = valor.strip()
    match = re.match(r'^([^-]+)-(.+)$', valor)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return valor, ""


def _limpiar_texto_celda(valor: str) -> str:
    """Quita saltos de linea embebidos (TRYLOGYC) y colapsa espacios."""
    texto = str(valor).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", texto).strip()


def _fecha_desde_nombre(path: Path):
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


def normalizar(ruta_csv: Path, ruta_salida: Path, servicio_tipo: str = None) -> pd.DataFrame:
    """
    Normaliza un CSV de socios exportado desde TRYLOGYC.

    El CSV resultante conserva todos los servicios; el filtro por sector
    se aplica en el paso de sincronizacion. Si se indica ``servicio_tipo``
    se imprime el conteo especifico de ese servicio al final.

    Args:
        ruta_csv: Ruta del CSV crudo de TRYLOGYC.
        ruta_salida: Ruta donde guardar el CSV normalizado.
        servicio_tipo: Nombre del servicio a reportar en el resumen (opcional).

    Returns:
        pd.DataFrame con los registros normalizados (todos los servicios).
    """
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

    indices = list(COLUMNAS_CSV.keys())
    df = df_raw.iloc[:, indices].copy()
    df.columns = list(COLUMNAS_CSV.values())

    print(f"  Filas originales: {len(df):,}")

    for col in df.columns:
        df[col] = df[col].apply(_limpiar_texto_celda)

    df["nro_socio"] = df["nro_socio_raw"].apply(_normalizar_nro_socio)
    df.drop(columns=["nro_socio_raw"], inplace=True)

    doc_split = df["documento_raw"].apply(_split_documento)
    df["tipo_doc"] = doc_split.apply(lambda x: x[0])
    df["nro_doc"] = doc_split.apply(lambda x: x[1])
    df.drop(columns=["documento_raw"], inplace=True)

    df["medidor"] = df["medidor"].replace("", None)

    fecha_fuente = _fecha_desde_nombre(ruta_csv)
    df["fecha_fuente"] = fecha_fuente
    print(f"  Fecha del archivo: {fecha_fuente}")

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

    print("\n  Filas por servicio:")
    for servicio, cantidad in df["servicio"].value_counts().items():
        print(f"    {servicio:<30} {cantidad:>7,}")

    print("\n  Filas por estado:")
    for estado, cantidad in df["estado"].value_counts().items():
        print(f"    {estado:<30} {cantidad:>7,}")

    print(f"\n  Socios unicos (nro_socio): {df['nro_socio'].nunique():,}")
    if servicio_tipo:
        conteo = (df['servicio'] == servicio_tipo).sum()
        print(f"  Socios con servicio {servicio_tipo}: {conteo:,}")

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta_salida, index=False, encoding="utf-8")
    print(f"\nGuardado: {ruta_salida}  ({len(df):,} filas)")
    return df


def archivo_mas_reciente(carpeta: Path) -> Path:
    """Devuelve el lista_socios_*.csv mas reciente en la carpeta indicada."""
    candidatos = sorted(carpeta.glob("lista_socios_*.csv"), reverse=True)
    if not candidatos:
        raise FileNotFoundError(
            f"No se encontro ningun lista_socios_*.csv en {carpeta}"
        )
    return candidatos[0]
