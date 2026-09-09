# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Agua.
"""

SERVICIO_TIPO = "Agua"

# Nombres de servicio en TRYLOGYC -> valor canonico en socios_normalizados.csv
SERVICIO_ALIASES = {
    "Agua Potable": SERVICIO_TIPO,
}

# Prefijo en archivos TXT (agua_<id>.txt) -> clave en conceptos_maestro (join maestro).
SERVICIO_TXT_ALIASES = {
    "agua": "agua_potable",
}
ID_SERVICIO = "2"
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Agua
TARIFA_EQUIVALENCIAS = {}
