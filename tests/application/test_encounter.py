from pokedex_cli.application.encounter import DescribeEncounter, is_special


class FakeRepository:
    def __init__(self, *, species=(), variants=()):
        self._species = set(species)
        self._variants = set(variants)

    def is_species_captured(self, species):
        return species in self._species

    def is_variant_captured(self, species, form, shiny):
        return (species, form, shiny) in self._variants


def test_is_special_flags_shiny_and_non_regular_forms():
    assert is_special("regular", False) is False
    assert is_special("regular", True) is True
    assert is_special("alola", False) is True
    assert is_special("mega-x", False) is True


def test_plain_encounter_reports_species_level_state():
    describe = DescribeEncounter(FakeRepository(species={"sudowoodo"}))

    caught = describe.execute("sudowoodo", "regular", False)
    assert caught.captured is True
    assert caught.special is False

    missing = describe.execute("mew", "regular", False)
    assert missing.captured is False
    assert missing.special is False


def test_special_variant_requires_the_exact_variant():
    repository = FakeRepository(
        species={"pikachu", "raichu"},
        variants={("raichu", "alola", False), ("pikachu", "regular", True)},
    )
    describe = DescribeEncounter(repository)

    # Owned species, but an alt form we do have: captured, still marked special.
    owned_form = describe.execute("raichu", "alola", False)
    assert owned_form.captured is True
    assert owned_form.special is True

    # Owned species, but a shiny we do NOT have: flagged as special & missing.
    missing_shiny = describe.execute("raichu", "regular", True)
    assert missing_shiny.captured is False
    assert missing_shiny.special is True

    # Shiny we do own: captured.
    owned_shiny = describe.execute("pikachu", "regular", True)
    assert owned_shiny.captured is True
    assert owned_shiny.special is True


def test_execute_normalizes_species_and_form():
    repository = FakeRepository(variants={("raichu", "alola", False)})
    describe = DescribeEncounter(repository)

    assert describe.execute("Raichu", "Alola", False).captured is True
