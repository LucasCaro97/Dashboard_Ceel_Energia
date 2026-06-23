================================================================================
  AUTOMATIZACION CEEL
================================================================================

Base de datos: conecciones_energia (MySQL)
Sectores: energia | agua | gas | internet | television


================================================================================
0. CONFIGURACION INICIAL (solo la primera vez)
================================================================================

  python -m venv venv
  .\venv\Scripts\activate
  pip install -r requirements.txt

  Copiar .env.example -> .env y completar:

    DB_HOST=localhost
    DB_PORT=3306
    DB_USER=tu_usuario
    DB_PASSWORD=tu_password
    DB_NAME=conecciones_energia


================================================================================
1. PREPARAR ARCHIVOS DE ENTRADA
================================================================================

Cada sector tiene su propio directorio bajo data/. Los archivos exportados
desde TRYLOGYC se depositan antes de ejecutar cualquier script.

----------------------------------------------------------------------
1A. Facturacion (TXT de conceptos)
----------------------------------------------------------------------

Ruta: data/<sector>/inbox/<AAAA>/<MM>/

  data/energia/inbox/2026/06/
    energia_123.txt
    adicionales_910.txt
    ...

Nombre del archivo: <servicio>_<id_concepto>.txt
Formato interno:    columnas separadas por ";" , encoding latin1
Encoding:           latin1

----------------------------------------------------------------------
1B. Socios (listado TRYLOGYC)
----------------------------------------------------------------------

Ruta: data/<sector>/socios/

  data/energia/socios/lista_socios_17062026.csv

Nombre del archivo: lista_socios_DDMMAAAA.csv  (la fecha permite
  detectar el archivo mas reciente automaticamente)
Encoding:           latin1 (el normalizador lo convierte a UTF-8)

Nota: el CSV exportado por TRYLOGYC contiene TODOS los servicios en un
  mismo archivo. El normalizador lo procesa completo; el sincronizador
  filtra por el servicio del sector correspondiente.


================================================================================
2. PROCESAR FACTURACION
================================================================================

Lee los TXT del periodo, los cruza con Conceptos_Maestro e inserta
en facturacion_conceptos.

# 1. Verificar sin riesgo
python scripts/procesar.py --sector internet --año 2026 --mes 05 --dry-run

# 2. Si todo OK, inyectar
python scripts/procesar.py --sector internet --año 2026 --mes 05

Salidas:
  - Excel de control: data/energia/processed/2026/06/
  - Insercion en BD:  facturacion_conceptos (modo append)

Precaucion: no ejecutar dos veces el mismo periodo (duplica registros).


================================================================================
3. NORMALIZAR SOCIOS
================================================================================

Extrae las columnas utiles del CSV crudo y los deja listos para sincronizar.

  # Toma el lista_socios_*.csv mas reciente
  .\venv\Scripts\python.exe scripts\normalizar.py --sector energia

  # Con archivo explicito
  .\venv\Scripts\python.exe scripts\normalizar.py --sector agua --input data\agua\socios\lista_socios_17062026.csv

Salida: data/<sector>/socios/socios_normalizados.csv

Transformaciones aplicadas:
  - nro_socio: "00000002/000001" -> "000002/0001"
  - documento: "DNI-9048914"    -> tipo_doc=DNI, nro_doc=9048914
  - fecha_fuente extraida del nombre del archivo


================================================================================
4. SINCRONIZAR SOCIOS CONTRA LA BD
================================================================================

Tablas afectadas:
  socios_<sector>         upsert por (nro_socio, servicio_tipo)
  socios_medidores        inserta nuevos; inactiva ausentes (estado=0)
  socio_historial_tarifas cierra vigencia anterior e inserta nueva si cambia

SIEMPRE simular primero:

  .\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --dry-run --export-reportes-csv

  Revisa los CSVs generados en data/<sector>/reportes_sincro/<fecha>_<ts>/
    socios_insertados.csv
    socios_actualizados.csv
    medidores_insertados.csv / medidores_inactivados.csv
    tarifas_creadas.csv / tarifas_cambiadas.csv / tarifas_no_mapeadas.csv

Ejecucion real (solo si el dry-run se ve correcto):

  .\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --export-reportes-csv

Mismo flujo para cualquier otro sector (--sector agua, --sector gas, etc.).


================================================================================
5. DASHBOARD
================================================================================

  .\venv\Scripts\activate
  streamlit run dashboards\energia_dashboard.py

Abre en http://localhost:8501  (solo lectura, no modifica la BD).


================================================================================
6. FLUJO MENSUAL RESUMIDO
================================================================================

[ ] Exportar desde TRYLOGYC:
      TXT conceptos  -> data/<sector>/inbox/<AAAA>/<MM>/
      CSV socios     -> data/<sector>/socios/lista_socios_DDMMAAAA.csv

[ ] Socios (por sector):
      scripts\normalizar.py  --sector <sector>
      scripts\sincronizar.py --sector <sector> --dry-run --export-reportes-csv
      -- revisar reportes --
      scripts\sincronizar.py --sector <sector> --export-reportes-csv

[ ] Facturacion:
      scripts\procesar.py --sector <sector> --año AAAA --mes MM

[ ] Dashboard:
      streamlit run dashboards\energia_dashboard.py


================================================================================
7. ESTRUCTURA DE CARPETAS
================================================================================

automatizacion_ceel/
  core/
    db_manager.py          Conexion MySQL compartida
    normalizar_base.py     Logica de normalizacion TRYLOGYC (todos los sectores)
    sector_sync.py         Logica de sincronizacion generica (todos los sectores)
  sectors/
    energia/               Implementado
      config.py
      procesador.py
      normalizar.py
      sincronizador.py
    agua/                  Implementado
    gas/                   Implementado
    internet/              Implementado
    television/            Implementado
  scripts/
    procesar.py            Wrapper CLI -> facturacion_conceptos
    normalizar.py          Wrapper CLI -> socios_normalizados.csv
    sincronizar.py         Wrapper CLI -> BD (socios/medidores/tarifas)
  dashboards/
    energia_dashboard.py   Streamlit
  data/
    <sector>/
      inbox/<AAAA>/<MM>/   TXT conceptos (entrada procesar.py)
      processed/           Excel de control (salida procesar.py)
      socios/              CSV crudo y normalizado
      reportes_sincro/     Historial de cambios por corrida


================================================================================
8. ERRORES FRECUENTES
================================================================================

"No files found in data/.../inbox/..."
  -> Verificar que los .txt esten en data/<sector>/inbox/<AAAA>/<MM>/

"CONCEPTS NOT FOUND IN MASTER"
  -> Agregar los conceptos faltantes a Conceptos_Maestro antes de reintentar.

"Faltan variables de entorno..."
  -> Crear/completar .env en la raiz del proyecto.

"No hay filas de servicio X para procesar"
  -> El valor SERVICIO_TIPO en sectors/<sector>/config.py debe coincidir
     exactamente con el que TRYLOGYC escribe en la columna 'servicio' del CSV.

Error de importacion:
  -> Ejecutar siempre desde la raiz del proyecto.
  -> Usar .\venv\Scripts\python.exe, no python directamente.


================================================================================
