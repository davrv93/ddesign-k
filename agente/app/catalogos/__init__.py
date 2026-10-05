"""Catálogos semánticos de intenciones (varios, separados) y resolvedor de referencias.

Un catálogo CLASIFICA frases de WhatsApp; no responde ni inventa datos de la prenda. Detalle en LEEME.md.

    from app.catalogos import cargar_clasificador, resolver_referencia
"""
from .catalogos import (Catalogo, Intencion, CatalogoInvalido, cargar_catalogo, cargar_catalogo_prueba,
                        listar_catalogos, normalizar_clave, preparar_texto, validar)
from .clasificador import Clasificador, cargar_clasificador, cargar_embedder
from .enrutador import Enrutador, cargar_todos
from .referencias import Resolucion, resolver_referencia

__all__ = ["Catalogo", "Intencion", "CatalogoInvalido", "cargar_catalogo", "cargar_catalogo_prueba", "listar_catalogos",
           "normalizar_clave", "preparar_texto", "validar", "Clasificador", "cargar_clasificador", "cargar_embedder",
           "Enrutador", "cargar_todos", "Resolucion", "resolver_referencia"]
