# -*- coding: utf-8 -*-
"""
Processor for Energia sector billing.
Injects billing concept data from TRYLOGYC into the database.
"""

import pandas as pd
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db_manager import get_db_config, build_sqlalchemy_engine, inyectar_a_mysql, obtener_maestro_conceptos
from .config import TABLA_FACTURACION, SERVICIO_TIPO


def procesar_periodo(anio, mes, sector="energia"):
    """
    Processes TXT files for a specific billing period.
    
    Args:
        anio (str): Year to process (e.g. "2026")
        mes (str): Month to process (e.g. "05")
        sector (str): Sector (default "energia")
        
    Returns:
        pd.DataFrame: Normalized data, or None if error
    """
    ruta_periodo = f'./data/{sector}/inbox/{anio}/{mes}'
    ruta_busqueda = os.path.join(ruta_periodo, "**", "*.txt")
    archivos = glob.glob(ruta_busqueda, recursive=True)
    
    if not archivos:
        print(f"No files found in {ruta_periodo}")
        return None

    dataframes = []
    for archivo in archivos:
        print(f"Processing: {os.path.basename(archivo)}")
        df = pd.read_csv(archivo, sep=';', encoding='latin1')
        df = df.drop(df.columns[0], axis=1).iloc[:, 0:8]
        df.columns = ['Socio_Con', 'Nombre', 'Direccion', 'Nro_Factura', 'Socio', 'Cantidad', 'Importe', 'Total']
        df = df.dropna(how='all')
        
        nombre_base = os.path.basename(archivo).replace('.txt', '')
        partes = nombre_base.rsplit('_', 1)
        df['id_concepto'] = int(partes[1])
        df['servicio'] = partes[0]
        df['periodo'] = f"{anio}-{mes}-01"
        dataframes.append(df)

    return pd.concat(dataframes, ignore_index=True)


def procesar_facturacion(anio, mes, sector="energia"):
    """
    Complete billing processing pipeline:
    1. Reads TXTs
    2. Normalizes data
    3. Validates against master concepts
    4. Injects into database
    5. Generates control Excel
    
    Args:
        anio (str): Year to process
        mes (str): Month to process
        sector (str): Sector
        
    Returns:
        bool: True if successful, False if error
    """
    
    print("--- Starting billing processing ---")
    
    # 1. Process files
    df_final = procesar_periodo(anio, mes, sector)
    if df_final is None:
        print("CRITICAL: Could not process text files.")
        return False
    print(f"Files processed. Total rows: {len(df_final)}")
    
    # 2. Connect to database
    df_maestro = obtener_maestro_conceptos()
    if df_maestro is None:
        print("CRITICAL: Database connection failed.")
        return False
    print("Database connected and master loaded.")

    # 3. Transformation logic
    cols_a_limpiar = ['Importe', 'Total', 'Cantidad']
    for col in cols_a_limpiar:
        df_final[col] = df_final[col].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
        df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0).round(2)
    
    # Merge with master
    df_final = pd.merge(df_final, df_maestro, on=['servicio', 'id_concepto'], how='left')
    
    # Integrity check
    faltantes = df_final[df_final['nombre_concepto'].isna()][['servicio', 'id_concepto']].drop_duplicates()
    
    if not faltantes.empty:
        print("\n--- ERROR: CONCEPTS NOT FOUND IN MASTER! ---")
        print("The following concepts are in files but NOT in database:")
        print(faltantes)
        print("-------------------------------------------------------\n")
        return False
    
    # Final rename
    renombrar = {
        'Socio_Con': 'nro_socio', 
        'Nombre': 'nombre_socio', 
        'Nro_Factura': 'nro_factura', 
        'Socio': 'es_socio', 
        'Cantidad': 'cantidad_cons', 
        'Importe': 'importe', 
        'Total': 'total'
    }
    df_final = df_final.rename(columns=renombrar)
    
    # Clean extra columns
    cols_a_borrar = ['nombre_concepto', 'es_consumo_total', 'grupo_usuario', 'es_consumo_escalonado', 'Direccion']
    df_final = df_final.drop(columns=cols_a_borrar, errors='ignore')
    
    # 4. Save final Excel
    ruta_salida = f'./data/{sector}/processed/{anio}/{mes}'
    os.makedirs(ruta_salida, exist_ok=True)
    nombre_archivo = f'{ruta_salida}/ENERGIA_conceptos_facturados_{anio}_{mes}.xlsx'
    df_final.to_excel(nombre_archivo, index=False)
    print(f"File generated: {nombre_archivo}")
    
    # 5. Inject into database
    if inyectar_a_mysql(df_final, TABLA_FACTURACION):
        print("--- Processing finished successfully ---")
        return True
    else:
        print("--- Processing finished with error ---")
        return False


if __name__ == "__main__":
    anio_a_procesar = "2026"
    mes_a_procesar = "05"
    procesar_facturacion(anio_a_procesar, mes_a_procesar)
