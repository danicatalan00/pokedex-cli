"""Modo combate: reglas puras de Gen 3 sobre datos de PokeAPI.

``model`` fija el contrato (estado, acciones y eventos); el resto de módulos
son funciones puras que lo transforman. Nada aquí conoce terminal, red ni
SQLite: el azar llega inyectado como ``RandomSource``.
"""
