from unittest.mock import MagicMock

from pokedex_cli.application.species import RefreshSpeciesData, SpeciesIdentity


def test_refresh_updates_living_captures_without_erasing_evolved_profiles() -> None:
    catalog = MagicMock()
    catalog.captured.return_value = (
        SpeciesIdentity("pichu", "regular"),
        SpeciesIdentity("slowking", "galar"),
    )
    species = MagicMock()
    species.execute.side_effect = [{"pokedex_id": 172}, None]

    result = RefreshSpeciesData(catalog=catalog, species=species).execute()

    catalog.clear.assert_not_called()
    assert species.execute.call_args_list[0].args == ("pichu", "regular")
    assert species.execute.call_args_list[0].kwargs == {"refresh": True}
    assert species.execute.call_args_list[1].args == ("slowking", "galar")
    assert result.total == 2
    assert result.refreshed == 1
    assert result.failed == (SpeciesIdentity("slowking", "galar"),)
