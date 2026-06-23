#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test script to verify module imports."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from core.db_manager import get_db_config
    print("? core.db_manager importable")
except Exception as e:
    print(f"? Error importing core.db_manager: {e}")

try:
    from sectors.energia.config import SERVICIO_TIPO
    print(f"? sectors.energia.config importable (SERVICIO_TIPO={SERVICIO_TIPO})")
except Exception as e:
    print(f"? Error importing sectors.energia: {e}")

try:
    from sectors.energia.procesador import procesar_facturacion
    print("? sectors.energia.procesador importable")
except Exception as e:
    print(f"? Error importing sectors.energia.procesador: {e}")

print("\nAll imports successful!")
