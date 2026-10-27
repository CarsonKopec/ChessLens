import time

from chesslens.persistence.sqlite_store import SessionStore, StoredMove, sessions_path


def test_records_moves_and_pgn(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    game = store.new_game()
    store.record_moves(game, [StoredMove(1, "e4", "e2e4", "fen1", 0.99), StoredMove(2, "e5", "e7e5", "fen2", 0.6)])
    store.save_pgn(game, "1. e4 e5 *")
    assert store.counts() == (1, 2)
    assert store.moves(game)[1] == StoredMove(2, "e5", "e7e5", "fen2", 0.6)
    assert store.games()[0][2] == "1. e4 e5 *"
    store.close()


def test_retention_deletes_old_games_and_their_moves(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    now = time.time()
    old = store.new_game(at=now - 40 * 86400)
    store.record_moves(old, [StoredMove(1, "d4", "d2d4", "f", 1.0)], at=now - 40 * 86400)
    recent = store.new_game(at=now - 86400)
    store.record_moves(recent, [StoredMove(1, "c4", "c2c4", "f", 1.0)], at=now - 86400)
    assert store.prune(30, now=now) == 1
    assert store.counts() == (1, 1)
    assert [m.san for m in store.moves(recent)] == ["c4"]
    store.close()


def test_delete_all(tmp_path):
    store = SessionStore(tmp_path / "s.sqlite3")
    store.record_moves(store.new_game(), [StoredMove(1, "e4", "e2e4", "f", 1.0)])
    store.delete_all()
    assert store.counts() == (0, 0)
    store.close()


def test_lives_in_appdata(isolated_appdata):
    assert sessions_path() == isolated_appdata / "ChessLens" / "sessions.sqlite3"
