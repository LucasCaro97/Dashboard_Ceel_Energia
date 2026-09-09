# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Television.
"""

SERVICIO_TIPO = "Television"

# Nombres de servicio en TRYLOGYC -> valor canonico en socios_normalizados.csv
SERVICIO_ALIASES = {
    "TV Cable": SERVICIO_TIPO,
}

# Prefijo en archivos TXT -> clave servicio en conceptos_maestro (nombre_servicio normalizado).
# Los TXT usan television_<id>.txt y servicios.nombre_servicio = 'television'.
SERVICIO_TXT_ALIASES = {}

DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"
TIENE_MEDIDORES = False

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Television
TARIFA_EQUIVALENCIAS = {}
