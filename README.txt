================================================================================
  AUTOMATIZACION CEEL - Guia de uso del proyecto
================================================================================

Base de datos: conecciones_energia (MySQL)

Este proyecto automatiza:
  - Carga mensual de conceptos de facturacion (TRYLOGYC -> facturacion_conceptos)
  - Sincronizacion mensual de socios por sector (TRYLOGYC -> BD MySQL)
  - Visualizacion de KPIs y graficos (dashboard Streamlit)

Sectores disponibles: energia, agua, internet, television, gas
(Actualmente implementado: energia)


================================================================================
0. CONFIGURACION INICIAL (solo la primera vez)
================================================================================

1) Crear entorno virtual e instalar dependencias:

   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt

2) Configurar conexion a MySQL:

   Copiar .env.example a .env y completar credenciales:

   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=tu_usuario
   DB_PASSWORD=tu_password
   DB_NAME=conecciones_energia

   (Alternativa: DB_URL=mysql+pymysql://usuario:password@host:3306/conecciones_energia)

3) Activar el entorno virtual antes de cada tarea:

   .\venv\Scripts\activate


================================================================================
1. INYECTAR DATOS DE FACTURACION (facturacion_conceptos)
================================================================================

Script: scripts/procesar.py

Que hace:
  - Lee archivos .txt exportados de TRYLOGYC desde data/<sector>/inbox/<anio>/<mes>/
  - Normaliza columnas y cruza con la tabla Conceptos_Maestro
  - Genera Excel de control en data/<sector>/processed/<anio>/<mes>/
  - Inserta filas en la tabla facturacion_conceptos (modo append)

Estructura esperada de archivos de entrada:

  data/energia/inbox/2026/05/adicionales/adicionales_910.txt
  data/energia/inbox/2026/05/energia/energia_123.txt
  ...

  Formato del nombre: <servicio>_<id_concepto>.txt
  Separador del TXT: punto y coma (;)
  Encoding: latin1

Pasos:

  A) Colocar los .txt del periodo en:
     data/energia/inbox/<AAAA>/<MM>/

  B) Ejecutar pasando sector, anio y mes como parametros:

     .\venv\Scripts\python.exe scripts\procesar.py --sector energia --año 2026 --mes 05

Salidas:
  - Excel: data/energia/processed/<anio>/<mes>/ENERGIA_conceptos_facturados_<anio>_<mes>.xlsx
  - Insercion en BD: tabla facturacion_conceptos

Validaciones automaticas:
  - Si no encuentra archivos en la carpeta del periodo, aborta.
  - Si hay conceptos en los TXT que no existen en Conceptos_Maestro, aborta
    e imprime la lista de faltantes (no inyecta datos invalidos).

Nota:
  - El script usa if_exists='append': cada ejecucion AGREGA filas.
    No borra periodos anteriores. Evitar ejecutar dos veces el mismo periodo
    si no corresponde duplicar datos.


================================================================================
2. INYECTAR / SINCRONIZAR DATOS DE SOCIOS
================================================================================

Flujo en 2 pasos: normalizar CSV crudo -> sincronizar contra BD.

Tablas afectadas (sector Energia):
  - socios_energia          (estado y nombre por nro_socio + servicio_tipo)
  - socios_medidores        (relacion 1:N de medidores; baja logica con estado=0)
  - socio_historial_tarifas (historial de tarifas con vigencias)

----------------------------------------------------------------------
PASO 2A - Normalizar export de TRYLOGYC
----------------------------------------------------------------------

Script: scripts/normalizar.py

Entrada:
  - CSV crudo exportado de TRYLOGYC, por ejemplo:
    data/energia/socios/lista_socios_17062026.csv

Salida:
  - data/energia/socios/socios_normalizados.csv

Comando (toma el lista_socios_*.csv mas reciente del sector):

  .\venv\Scripts\python.exe scripts\normalizar.py --sector energia

Comando con archivo explicito:

  .\venv\Scripts\python.exe scripts\normalizar.py --sector energia --input data\energia\socios\lista_socios_17062026.csv

Que normaliza:
  - Extrae columnas utiles (11 a 19 del export TRYLOGYC)
  - nro_socio al formato BD (ej: 00000002/000001 -> 000002/0001)
  - Separa documento en tipo_doc y nro_doc
  - Agrega fecha_fuente desde el nombre del archivo

----------------------------------------------------------------------
PASO 2B - Sincronizar contra MySQL
----------------------------------------------------------------------

Script: scripts/sincronizar.py

Procesa SOLO filas con servicio = "Energia".

Reglas principales:
  - socios_energia: inserta nuevos; actualiza estado si cambio; actualiza nombre
  - socios_medidores: inserta nuevos; mantiene existentes; inactiva (estado=0)
    los que no vienen en el snapshot mensual; NO modifica columna act
  - socio_historial_tarifas: cierra vigencia anterior e inserta nueva si cambia
    la tarifa asignada

RECOMENDADO - Simular antes de guardar (dry-run + reportes CSV):

  .\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --dry-run --export-reportes-csv

  - No persiste cambios en BD (hace rollback al final)
  - Genera CSVs de control en:
    data/energia/reportes_sincro/<fecha_fuente>_<timestamp>/
      socios_insertados.csv
      socios_actualizados.csv
      medidores_insertados.csv
      medidores_inactivados.csv
      tarifas_creadas.csv
      tarifas_cambiadas.csv
      tarifas_no_mapeadas.csv

EJECUCION REAL (persiste cambios + historial CSV):

  .\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --export-reportes-csv

Opciones utiles:

  --sector <nombre>          Sector a sincronizar (ej: energia)
  --input <ruta_csv>         CSV normalizado (default: data/<sector>/socios/socios_normalizados.csv)
  --dry-run                  Simula sin commit
  --export-reportes-csv      Exporta reportes de cambios para auditoria


================================================================================
3. DASHBOARD DE FACTURACION (visualizacion)
================================================================================

Script: dashboards/energia_dashboard.py

Muestra KPIs, graficos de distribucion, top consumidores, correlacion, etc.
Consulta vistas pre-agregadas en MySQL (v_kpi_facturacion,
v_totalizado_por_tarifa_base, v_reporte_facturacion_energia, etc.).

Ejecutar:

  .\venv\Scripts\activate
  streamlit run dashboards\energia_dashboard.py

Se abre en el navegador (por defecto http://localhost:8501).

Filtros en sidebar:
  - Periodo
  - Cantidad de categorias (Top N)

Nota: el dashboard solo LEE datos de la BD; no modifica tablas.


================================================================================
4. FLUJO MENSUAL RECOMENDADO (checklist)
================================================================================

[ ] 1. Exportar desde TRYLOGYC:
        - TXT de conceptos -> data/energia/inbox/<anio>/<mes>/
        - Listado de socios -> data/energia/socios/lista_socios_DDMMAAAA.csv

[ ] 2. Socios:
        - Ejecutar: scripts\normalizar.py --sector energia
        - Simular:  scripts\sincronizar.py --sector energia --dry-run --export-reportes-csv
        - Revisar CSVs en data/energia/reportes_sincro/
        - Si todo OK: scripts\sincronizar.py --sector energia --export-reportes-csv

[ ] 3. Facturacion:
        - Ejecutar: scripts\procesar.py --sector energia --año 2026 --mes 05
        - Verificar Excel en data/energia/processed/

[ ] 4. Dashboard:
        - streamlit run dashboards\energia_dashboard.py
        - Validar KPIs y graficos del periodo cargado


================================================================================
5. ESTRUCTURA DE CARPETAS PRINCIPAL
================================================================================

automatizacion_ceel/
  core/
    db_manager.py               Conexion a BD compartida por todos los sectores
  sectors/
    energia/
      config.py                 Configuracion del sector (tablas, constantes)
      procesador.py             Logica de procesamiento de facturacion
      normalizar.py             Normalizacion de CSV de socios
      sincronizador.py          Sincronizacion de socios/medidores/tarifas
    agua/                       (pendiente de implementar)
    internet/                   (pendiente de implementar)
    television/                 (pendiente de implementar)
    gas/                        (pendiente de implementar)
  scripts/
    procesar.py                 Wrapper CLI: inyecta facturacion_conceptos
    normalizar.py               Wrapper CLI: normaliza CSV de socios TRYLOGYC
    sincronizar.py              Wrapper CLI: sincroniza socios/medidores/tarifas
  dashboards/
    energia_dashboard.py        Dashboard Streamlit de Energia
  data/
    energia/
      inbox/<anio>/<mes>/       TXT conceptos facturacion (entrada)
      processed/<anio>/<mes>/   Excel procesado (salida control)
      socios/                   CSV socios TRYLOGYC y normalizados
      reportes_sincro/          Historial de cambios por corrida de sincronizacion
    agua/                       (estructura identica a energia/)
    internet/
    television/
    gas/
  requirements.txt              Dependencias Python
  .env                          Credenciales BD (no subir a GitHub)


================================================================================
6. TABLAS MYSQL REFERENCIADAS
================================================================================

Facturacion:
  - Conceptos_Maestro       (maestro de conceptos; requerido por procesar.py)
  - facturacion_conceptos   (destino de scripts/procesar.py)

Socios (sector Energia):
  - socios_energia
  - socios_medidores
  - socio_historial_tarifas
  - tarifa_base             (maestro de tarifas)

Dashboard (vistas, solo lectura):
  - v_kpi_facturacion
  - v_totalizado_por_tarifa_base
  - v_reporte_facturacion_energia
  - vista_socios_tarifa_actual


================================================================================
7. SOLUCION DE PROBLEMAS FRECUENTES
================================================================================

"No files found in data/energia/inbox/..."
  -> Verificar que los .txt esten en data/energia/inbox/<anio>/<mes>/ (no en data/inbox/).

"CONCEPTS NOT FOUND IN MASTER"
  -> Agregar los conceptos faltantes a Conceptos_Maestro antes de reintentar.

"Faltan variables de entorno..."
  -> Crear/completar archivo .env en la raiz del proyecto.

Dashboard sin datos / KPIs en cero:
  -> Verificar periodo seleccionado y que existan datos cargados para ese mes.

Sincronizacion socios - revisar cambios antes de commit:
  -> Siempre usar --dry-run --export-reportes-csv primero.

Error de importacion al ejecutar scripts:
  -> Asegurarse de ejecutar desde la raiz del proyecto (automatizacion_ceel/).
  -> Usar .\venv\Scripts\python.exe, no python directamente.


================================================================================
Fin de la guia
================================================================================
