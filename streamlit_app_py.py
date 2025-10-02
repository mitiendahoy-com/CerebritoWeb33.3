#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CerebritoWeb - Punto de entrada para Streamlit Cloud
Versión 33.3
"""

import streamlit as st
import sys
from pathlib import Path

# Agregar directorio actual al path
sys.path.insert(0, str(Path(__file__).parent))

# Importar la aplicación principal
from CerebritoWeb import main

if __name__ == '__main__':
    main()