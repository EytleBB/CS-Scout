from perfectworld_experiment.auto_scout import resolve_roster, select_targets
from perfectworld_experiment.current_match import (
    CurrentMatch,
    LocalPerfectWorldState,
    RawMatchPlayer,
    SessionCredentials,
)
from perfectworld_experiment.pwa_client import PerfectWorldPlayer


ACCOUNT = "76561198000000000"


def _match():
    return CurrentMatch(
        "match-live",
        "de_mirage",
        (
            RawMatchPlayer(ACCOUNT, slot_id=0, team_id=1),
            RawMatchPlayer("76561198111111111", slot_id=1, team_id=1),
            RawMatchPlayer("76561198222222222", slot_id=5, team_id=2),
            RawMatchPlayer("76561198333333333", slot_id=6, team_id=2),
        ),
    )


class ResolverClient:
    def resolve_players(self, player_ids, *, jt):
        assert jt == "jt-secret"
        return [
            PerfectWorldPlayer(player_id, player_id, f"player-{index}")
            for index, player_id in enumerate(player_ids)
        ]


def test_resolve_roster_uses_game_start_order_and_profile_names():
    state = LocalPerfectWorldState(
        _match(),
        SessionCredentials(ACCOUNT, "token-secret", "jt-secret"),
    )
    roster = resolve_roster(state, client=ResolverClient())
    assert [player.player_id for player in roster] == [
        ACCOUNT,
        "76561198111111111",
        "76561198222222222",
        "76561198333333333",
    ]
    assert roster[2].nickname == "player-2"


def test_select_targets_defaults_to_opponents():
    roster = tuple(
        PerfectWorldPlayer(player.player_id, player.player_id, player.player_id)
        for player in _match().players
    )
    targets = select_targets(_match(), roster, ACCOUNT)
    assert targets.scope == "opponents_by_team"
    assert [player.steamid for player in targets.players] == [
        "76561198222222222",
        "76561198333333333",
    ]


def test_select_targets_can_keep_everyone():
    roster = tuple(
        PerfectWorldPlayer(player.player_id, player.player_id, player.player_id)
        for player in _match().players
    )
    targets = select_targets(_match(), roster, ACCOUNT, all_players=True)
    assert targets.scope == "all_players"
    assert len(targets.players) == 4
