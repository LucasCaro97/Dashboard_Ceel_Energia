# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Agua.
"""

SERVICIO_TIPO = "Agua"
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_agua"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Agua
TARIFA_EQUIVALENCIAS = {}
