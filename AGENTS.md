# Working agreement

@docs/engineering-standards.md
@docs/change-gates.md

Keep changes small and test the affected behavior first. After every accepted
change, run the light gates, then `./install.sh`, verify `~/bin/pokedex` from
`/tmp`, and validate `zsh -n ~/.zshrc`. Never finish with the checkout newer
than the installed copy.
