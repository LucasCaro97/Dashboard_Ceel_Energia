import pandas as pd
import glob
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
import pymysql

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def get_db_config():
    db_url = os.getenv('DB_URL')
    if db_url:
        return {'db_url': db_url}

    required_env_vars = ['DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD', 'DB_NAME']
    missing_env_vars = [var_name for var_name in required_env_vars if not os.getenv(var_name)]
    if missing_env_vars:
        raise RuntimeError(
            'Faltan variables de entorno para conectar a la base de datos: '
            + ', '.join(missing_env_vars)
        )

    return {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME'),
    }


def build_sqlalchemy_engine(db_config):
    if 'db_url' in db_config:
        return create_engine(db_config['db_url'])

    return create_engine(
        URL.create(
            drivername='mysql+pymysql',
            username=db_config['user'],
            password=db_config['password'],
            host=db_config['host'],
            port=db_config['port'],
            database=db_config['database'],
        )
    )

def inyectar_a_mysql(df):
    try:
        db_config = get_db_config()
        engine = build_sqlalchemy_engine(db_config)
        
        # 'append' añade los datos a la tabla existente sin borrar nada
        # index=False evita que se guarde el número de fila de pandas
        df.to_sql(name='facturacion_conceptos', con=engine, if_exists='append', index=False)
        
        print("¡Datos inyectados exitosamente en la base de datos!")
    except Exception as e:
        print(f"Error al inyectar datos en MySQL: {e}")

def obtener_maestro_conceptos():
    try:
        db_config = get_db_config()

        if 'db_url' in db_config:
            engine = build_sqlalchemy_engine(db_config)
            query = "SELECT * FROM Conceptos_Maestro"
            df_maestro = pd.read_sql(query, engine)
            print("DEBUG: Conexión a BD establecida con SQLAlchemy (DB_URL).")
            return df_maestro

        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            port=db_config['port'],
        )
        print("DEBUG: Conexión a BD establecida con pymysql.")
        
        query = "SELECT * FROM Conceptos_Maestro"
        df_maestro = pd.read_sql(query, conn)
        conn.close()
        return df_maestro
    except Exception as e:
        print(f"DEBUG: ERROR EN CONEXIÓN: {e}")
        return None
    
def procesar_periodo(anio, mes):
    ruta_periodo = f'./data/inbox/{anio}/{mes}'
    ruta_busqueda = os.path.join(ruta_periodo, "**", "*.txt")
    archivos = glob.glob(ruta_busqueda, recursive=True)
    
    if not archivos:
        print(f"No se encontraron archivos en {ruta_periodo}")
        return None

    dataframes = []
    for archivo in archivos:
        print(f"Procesando: {os.path.basename(archivo)}")
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


# --- Ejecución ---
if __name__ == "__main__":
    anio_a_procesar = "2026"
    mes_a_procesar = "04"
    
    print("--- Iniciando proceso ---")
    
    # 1. Procesar archivos
    df_final = procesar_periodo(anio_a_procesar, mes_a_procesar)
    if df_final is None:
        print("CRÍTICO: No se pudieron procesar los archivos de texto.")
        exit()
    print(f"Archivos procesados. Filas totales: {len(df_final)}")
    
    # 2. Conectar a BD
    df_maestro = obtener_maestro_conceptos()
    if df_maestro is None:
        print("CRÍTICO: La conexión a la base de datos falló.")
        exit()
    print("Base de datos conectada y maestro cargado.")

    # 3. Lógica de transformación
    # Limpieza inicial de las columnas crudas antes del merge
    cols_a_limpiar = ['Importe', 'Total', 'Cantidad']
    for col in cols_a_limpiar:
        df_final[col] = df_final[col].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
        df_final[col] = pd.to_numeric(df_final[col], errors='coerce').fillna(0).round(2)
    
    # Merge con maestro
    df_final = pd.merge(df_final, df_maestro, on=['servicio', 'id_concepto'], how='left')
    
    # --- DETECCIÓN DE ERRORES DE INTEGRIDAD ---
    # Si alguna columna que venía del maestro es NaN, significa que el merge falló
    # 'nombre_concepto' es un ejemplo de columna del maestro
    faltantes = df_final[df_final['nombre_concepto'].isna()][['servicio', 'id_concepto']].drop_duplicates()
    
    if not faltantes.empty:
        print("\n--- ¡ERROR: CONCEPTOS NO ENCONTRADOS EN EL MAESTRO! ---")
        print("Los siguientes conceptos están en los archivos pero NO en la base de datos:")
        print(faltantes)
        print("-------------------------------------------------------\n")
        exit() # Detenemos el proceso para que no intente inyectar datos inválidos
    
    # Renombrado final
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
    
    # Limpieza de columnas sobrantes
    cols_a_borrar = ['nombre_concepto', 'es_consumo_total', 'grupo_usuario', 'es_consumo_escalonado', 'Direccion']
    df_final = df_final.drop(columns=cols_a_borrar, errors='ignore')
    
    # 4. Guardado final en Excel
    ruta_salida = f'./data/processed/{anio_a_procesar}/{mes_a_procesar}'
    os.makedirs(ruta_salida, exist_ok=True)
    nombre_archivo = f'{ruta_salida}/ENERGIA_conceptos_facturados_{anio_a_procesar}_{mes_a_procesar}.xlsx'
    df_final.to_excel(nombre_archivo, index=False)
    print(f"Archivo generado: {nombre_archivo}")
    
    # 5. Inyectar a la base de datos
    inyectar_a_mysql(df_final)
    print("--- Proceso finalizado ---")