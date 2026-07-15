# Personalizar el logo

El README referencia siempre `docs/assets/project-logo.svg`. El archivo es
estático para GitHub, pero se regenera desde Krabby con una configuración
versionada.

Edita [assets/logo.toml](assets/logo.toml):

```toml
[logo]
pokemon = "charizard"
form = "mega-x"
shiny = false
```

Después ejecuta:

```bash
.venv/bin/python tools/render_logo.py
```

El generador conserva los colores del sprite y deriva de sus tonos dominantes
el gradiente de fondo y el acento del título. Sobrescribe el mismo SVG, así que
no hace falta editar el README. Usa `krabby list` y `krabby name --help` para
consultar nombres y formas válidas.
