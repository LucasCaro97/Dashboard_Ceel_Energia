# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Internet.
"""

SERVICIO_TIPO = "internet"
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"
TIENE_MEDIDORES = False

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Internet
TARIFA_EQUIVALENCIAS = {}
