from celia import games_routes


def test_voice_parser_identifies_actor_and_maps_cell():
    parse = games_routes.parse_explicit_game_play_command
    assert parse("Célia, joga")["requested_action"] is None
    assert parse("joga, Célia")["actor"] == "celia"
    assert parse("é tua vez, joga")["action"] == "play"
    intent = parse("Célia, joga na sete")
    assert intent["requested_position"] == 7
    assert intent["requested_action"] == "6"
    assert parse("eu vou jogar na 7") is None
    assert parse("vou marcar na sete") is None
    assert parse("acho que tu devia jogar na sete") is None
    assert parse("a casa sete tá livre?") is None
    assert parse("não joga ainda") is None
