# Documentación

Este índice sigue Diátaxis: entra por la clase de pregunta que estás intentando
resolver. Los documentos evitan repetir código y enlazan al detalle operativo
solo cuando hace falta.

## Quiero hacer algo

- [Instalar, actualizar y activar el hook](installation.md)
- [Cambiar el Pokémon del logo](logo.md)
- [Escribir y ejecutar tests](testing.md)
- [Elegir los gates de un cambio](change-gates.md)
- [Hacer backup, diagnosticar o recuperar datos](operations.md)

## Necesito una referencia

- [Estándares de ingeniería](engineering-standards.md)
- [Modelo de datos e invariantes](data-model.md)
- [Adaptadores e infraestructura](infrastructure.md)

## Quiero entender el diseño

- [Arquitectura y dirección de dependencias](architecture.md)

El recorrido recomendado para contribuir es: estándares → testing → gates. La
última acción de cualquier cambio aceptado es reinstalar la copia estable, tal
como define la [guía de gates](change-gates.md#dejar-la-versión-efectiva-lista).
