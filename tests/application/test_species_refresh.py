from unittest.mock import MagicMock

from pokedex_cli.application.species import RefreshSpeciesData, SpeciesIdentity


def test_refresh_clears_cache_then_refetches_every_captured_identity() -> None:
    catalog = MagicMock()
    catalog.captured.return_value = (
        SpeciesIdentity("pichu", "regular"),
        SpeciesIdentity("slowking", "galar"),
    )
    species = MagicMock()
    species.execute.side_effect = [{"pokedex_id": 172}, None]

    result = RefreshSpeciesData(catalog=catalog, species=species).execute()

    catalog.clear.assert_called_once_with()
    assert species.execute.call_args_list[0].args == ("pichu", "regular")
    assert species.execute.call_args_list[0].kwargs == {"refresh": True}
    assert species.execute.call_args_list[1].args == ("slowking", "galar")
    assert result.total == 2
    assert result.refreshed == 1
    assert result.failed == (SpeciesIdentity("slowking", "galar"),)
