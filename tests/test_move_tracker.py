import time

import chess
import chess.pgn
import pytest

from chesslens.chess.tracker import GameTracker, TransitionKind


def placement(*sans: str, fen: str = chess.STARTING_FEN) -> chess.BaseBoard:
    board = chess.Board(fen)
    for san in sans:
        board.push_san(san)
    return chess.BaseBoard(board.board_fen())


def play(tracker: GameTracker, *sans: str) -> list[str]:
    """Show the tracker each position of a game in turn; return the SAN it confirmed."""
    board = chess.Board()
    seen = []
    for san in sans:
        board.push_san(san)
        t = tracker.observe(chess.BaseBoard(board.board_fen()))
        assert t.kind is TransitionKind.MOVES, (san, t)
        seen += t.sans
    return seen


def test_moves_are_confirmed_one_by_one_with_side_to_move():
    tracker = GameTracker()
    assert play(tracker, "e4", "e5", "Nf3") == ["e4", "e5", "Nf3"]
    assert tracker.board.turn == chess.BLACK
    assert tracker.board.fen() == chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2").fen()
    assert tracker.last_move == chess.Move.from_uci("g1f3")


def test_unchanged_screen_is_no_change():
    tracker = GameTracker()
    play(tracker, "d4")
    assert tracker.observe(placement("d4")).kind is TransitionKind.NO_CHANGE


def test_captures_castling_both_ways_and_castling_rights():
    tracker = GameTracker()
    sans = ["e4", "d5", "exd5", "Qxd5", "Nf3", "Bg4", "Be2", "Nc6", "O-O", "O-O-O"]
    assert play(tracker, *sans) == sans
    assert tracker.board.castling_rights == 0


def test_en_passant():
    tracker = GameTracker()
    assert play(tracker, "e4", "a6", "e5", "d5", "exd6") == ["e4", "a6", "e5", "d5", "exd6"]
    assert tracker.board.piece_at(chess.D5) is None


@pytest.mark.parametrize("piece, san", [("Q", "a8=Q"), ("N", "a8=N")])
def test_promotion_and_underpromotion(piece, san):
    tracker = GameTracker(chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1"))
    after = chess.BaseBoard(f"{piece}7/7k/8/8/8/8/8/K7")
    t = tracker.observe(after)
    assert t.kind is TransitionKind.MOVES and t.sans == (san,)


def test_promotion_picker_waits_until_a_piece_is_chosen():
    tracker = GameTracker(chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1"))
    # The app's picker covers a8-a5 while the player chooses; those squares read as unsure.
    picker = chess.BaseBoard("8/7k/8/8/8/8/8/K7")
    t = tracker.observe(picker, unsure=frozenset({chess.A8, chess.A7, chess.A6, chess.A5}))
    # Nothing it can read has changed, so nothing is committed, but it says what it can't see.
    assert t.kind is TransitionKind.NO_CHANGE and t.detail == "a5, a6, a7, a8 are unclear on screen."
    assert tracker.board.ply() == 0
    assert tracker.observe(chess.BaseBoard("Q7/7k/8/8/8/8/8/K7")).sans == ("a8=Q",)


def test_two_moves_seen_at_once_are_reconstructed():
    tracker = GameTracker()
    t = tracker.observe(placement("e4", "c5"))
    assert t.kind is TransitionKind.MOVES and t.sans == ("e4", "c5")


# -- bot games: fast replies, premoves, playing Black ------------------------------------------


def seen_at_once(start_fen: str, *sans: str) -> tuple[chess.BaseBoard, chess.Board]:
    board = chess.Board(start_fen)
    for san in sans:
        board.push_san(san)
    return chess.BaseBoard(board.board_fen()), board


MIDDLEGAME = "r1bq1rk1/pp2bppp/2n1pn2/2pp4/2PP4/2N1PN2/PP2BPPP/R1BQ1RK1 w - - 0 8"


@pytest.mark.parametrize(
    "sans",
    [
        ("dxc5", "Bxc5", "b3"),  # your move, the bot's instant reply, your premove
        ("dxc5", "Bxc5", "b3", "Qe7"),  # ...and the bot again
    ],
)
def test_three_or_four_moves_between_readings_are_caught_up(sans):
    tracker = GameTracker(chess.Board(MIDDLEGAME))
    shown, truth = seen_at_once(MIDDLEGAME, *sans)
    t = tracker.observe(shown)
    assert t.kind is TransitionKind.MOVES and t.sans == sans
    assert tracker.board.fen() == truth.fen()


def test_order_follows_the_last_move_highlight():
    tracker = GameTracker()
    shown, truth = seen_at_once(chess.STARTING_FEN, "e4", "e5", "Nf3")
    t = tracker.observe(shown, last_move=frozenset({chess.G1, chess.F3}), last_mover=chess.WHITE)
    assert t.sans == ("e4", "e5", "Nf3")
    assert [san for _, san, _ in tracker.move_list()] == ["e4", "e5", "Nf3"]


def test_without_a_highlight_the_order_is_flagged_as_a_guess():
    tracker = GameTracker()
    shown, truth = seen_at_once(chess.STARTING_FEN, "e4", "e5", "Nf3")
    t = tracker.observe(shown)
    assert t.kind is TransitionKind.MOVES and sorted(t.sans) == ["Nf3", "e4", "e5"]
    assert "doesn't show which order" in t.detail
    assert tracker.board.fen().split()[:4] == truth.fen().split()[:4]  # the position is right either way


def test_highlight_settles_an_order_that_changes_en_passant():
    start = "rnbqkbnr/ppp1pppp/8/8/3p4/8/PPPPPPPP/RNBQKBNR w KQkq - 0 3"
    shown, truth = seen_at_once(start, "Nf3", "h6", "e4")  # e4 last: d4xe3 en passant is possible
    assert GameTracker(chess.Board(start)).observe(shown).kind is TransitionKind.WAITING  # order matters, unknown
    tracker = GameTracker(chess.Board(start))
    t = tracker.observe(shown, last_move=frozenset({chess.E2, chess.E4}), last_mover=chess.WHITE)
    assert t.sans == ("Nf3", "h6", "e4") and tracker.board.fen() == truth.fen()


def test_highlight_stops_a_wrong_side_to_move():
    # a3 ... a4 looks like a single a2-a4: four moves would explain the pieces, but end with Black moving,
    # while the highlight shows White (a3-a4) moved last. ChessLens must not take the four-move version.
    shown, _ = seen_at_once(MIDDLEGAME, "a3", "h6", "b3", "g6", "a4")
    tracker = GameTracker(chess.Board(MIDDLEGAME))
    t = tracker.observe(shown, last_move=frozenset({chess.A3, chess.A4}), last_mover=chess.WHITE)
    assert t.kind is not TransitionKind.MOVES and tracker.board.fen() == chess.Board(MIDDLEGAME).fen()


def test_catch_up_stays_quick():
    start = "r3k2r/pppq1ppp/2npbn2/2b1p3/2B1P3/2NPBN2/PPPQ1PPP/R3K2R w KQkq - 0 8"
    shown, truth = seen_at_once(start, "O-O", "O-O-O", "Bxc5", "dxc5")
    tracker = GameTracker(chess.Board(start))
    began = time.perf_counter()
    t = tracker.observe(shown, last_move=frozenset({chess.D6, chess.C5}), last_mover=chess.BLACK)
    assert time.perf_counter() - began < 2.0
    assert t.kind is TransitionKind.MOVES and tracker.board.fen().split()[:4] == truth.fen().split()[:4]
    hopeless = GameTracker(chess.Board(start))
    began = time.perf_counter()
    assert hopeless.observe(chess.BaseBoard("4k3/8/8/8/8/8/8/4K3")).kind is TransitionKind.UNEXPLAINED
    assert time.perf_counter() - began < 0.2


def test_new_bot_game_as_black_shows_a_turned_round_start():
    tracker = GameTracker()
    play(tracker, "e4", "e5", "Nf3")
    turned = chess.BaseBoard().transform(chess.flip_vertical).transform(chess.flip_horizontal)
    t = tracker.observe(turned)
    assert t.kind is TransitionKind.NEW_GAME and t.flipped
    assert tracker.board.fen() == chess.STARTING_FEN and len(tracker.archived) == 1


BOT_GAME = ("Nh3", "c5", "g3", "b5", "Na3", "Nc6", "e4", "Qa5", "b4")  # live Chess.com bot game, user as Black


def test_a_piece_being_dragged_is_waited_for_not_explained_away():
    # Reading from that game: the queen has left a5 and is under the finger, hiding six squares. A knight
    # trip (Qa6 Nc4 Qa3 Nxa3) used to "explain" the queen vanishing, and the game was lost from there on.
    tracker = GameTracker()
    play(tracker, *BOT_GAME)
    held = placement(*BOT_GAME)
    held.remove_piece_at(chess.A5)
    under_finger = frozenset({chess.A3, chess.A4, chess.B3, chess.B4, chess.B5, chess.C4})
    assert tracker.observe(held, unsure=under_finger).kind is TransitionKind.WAITING
    assert tracker.observe(placement(*BOT_GAME, "Qxb4"), last_move=frozenset({chess.A5, chess.B4})).sans == ("Qxb4",)


def test_catch_up_rejects_a_piece_going_out_and_back():
    # The same vanished queen with a3 merely half-sure: a knight leaving a3 and capturing back on it fits
    # the reading, but leaves no trace of having moved.
    tracker = GameTracker()
    play(tracker, *BOT_GAME)
    gone = placement(*BOT_GAME)
    gone.remove_piece_at(chess.A5)
    t = tracker.observe(gone, half_sure=frozenset({chess.A3}))
    assert t.kind is TransitionKind.WAITING and tracker.board.fen() == placement_board(*BOT_GAME).fen()


def test_screen_going_on_from_an_earlier_position_resyncs():
    # A wrong guess (forced here), then the move really played from before it, with its highlight.
    tracker = GameTracker()
    play(tracker, *BOT_GAME, "Qa6", "Nc4", "Qa3", "Nxa3")
    t = tracker.observe(placement(*BOT_GAME, "Qxb4"), last_move=frozenset({chess.A5, chess.B4}))
    assert t.kind is TransitionKind.MOVES and t.sans == ("Qxb4",)
    assert "after 5. b4" in t.detail and "4 moves" in t.detail
    assert [san for _, san, _ in tracker.move_list()] == [*BOT_GAME, "Qxb4"]
    assert "5... Qa6 6. Nc4 Qa3 7. Nxa3" in tracker.pgn()  # kept as a side line
    assert tracker.observe(placement(*BOT_GAME, "Qxb4", "Rg1")).sans == ("Rg1",)  # and followed from there


def test_resync_needs_the_highlighted_move():
    tracker = GameTracker()
    play(tracker, *BOT_GAME, "Qa6", "Nc4", "Qa3", "Nxa3")
    t = tracker.observe(placement(*BOT_GAME, "Qxb4"), last_move=frozenset({chess.G1, chess.H1}))
    assert t.kind is TransitionKind.UNEXPLAINED and len(tracker.move_list()) == len(BOT_GAME) + 4


def placement_board(*sans: str) -> chess.Board:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


def test_unsure_square_blocks_a_move_only_where_it_changes_something():
    tracker = GameTracker()
    blurred = frozenset({chess.E4})
    assert tracker.observe(placement("e4"), unsure=blurred).kind is TransitionKind.WAITING  # e4 is where the move lands
    assert tracker.observe(placement("d4"), unsure=blurred).sans == ("d4",)  # e4 unchanged by d4


def test_half_sure_landing_square_is_accepted_when_its_guess_matches_the_only_legal_move():
    tracker = GameTracker()
    half = frozenset({chess.E4})
    # The reading's best guess on e4 is the pawn e4 puts there.
    assert tracker.observe(placement("e4"), half_sure=half).sans == ("e4",)


def test_half_sure_landing_square_with_another_guess_waits():
    tracker = GameTracker()
    misread = placement("e4")
    misread.set_piece_at(chess.E4, chess.Piece(chess.BISHOP, chess.WHITE))  # best guess: a bishop on e4
    t = tracker.observe(misread, half_sure=frozenset({chess.E4}))
    assert t.kind is TransitionKind.WAITING and tracker.board.ply() == 0


def test_half_sure_square_that_did_not_change_is_no_change():
    tracker = GameTracker()
    play(tracker, "e4")
    king_as_knight = placement("e4")
    king_as_knight.set_piece_at(chess.E8, chess.Piece(chess.KNIGHT, chess.BLACK))
    t = tracker.observe(king_as_knight, half_sure=frozenset({chess.E8}))
    assert t.kind is TransitionKind.NO_CHANGE and t.detail == "e8 is unclear on screen."
    # A half-sure square whose guess agrees with the game isn't worth mentioning.
    assert tracker.observe(placement("e4"), half_sure=frozenset({chess.E8})).detail == ""


def test_a_move_elsewhere_is_seen_while_a_square_stays_half_sure():
    tracker = GameTracker()
    play(tracker, "e4")
    king_as_knight = placement("e4", "c5")
    king_as_knight.set_piece_at(chess.E8, chess.Piece(chess.KNIGHT, chess.BLACK))
    assert tracker.observe(king_as_knight, half_sure=frozenset({chess.E8})).sans == ("c5",)


def test_returning_to_the_start_layout_by_moves_is_a_move():
    tracker = GameTracker()
    assert play(tracker, "Nf3", "Nf6", "Ng1", "Ng8")[-1] == "Ng8"
    assert tracker.board.ply() == 4 and not tracker.archived


def test_review_arrows_step_through_the_game_without_changing_it():
    tracker = GameTracker()
    play(tracker, "e4", "e5", "Nf3", "Nc6")
    t = tracker.observe(placement("e4", "e5", "Nf3"))
    assert t.kind is TransitionKind.NAVIGATED and t.detail == "after 2. Nf3"
    assert tracker.observe(placement("e4")).kind is TransitionKind.NAVIGATED  # jump back two
    assert tracker.observe(placement("e4", "e5", "Nf3", "Nc6")).kind is TransitionKind.NAVIGATED  # all the way forward
    assert [san for _, san, _ in tracker.move_list()] == ["e4", "e5", "Nf3", "Nc6"]


def test_stepping_back_to_the_first_position_is_not_a_new_game():
    tracker = GameTracker()
    play(tracker, "e4")
    assert tracker.observe(chess.BaseBoard()).kind is TransitionKind.NAVIGATED
    assert tracker.board.ply() == 0 and not tracker.archived


def test_playing_something_else_after_stepping_back_keeps_the_old_line_as_a_variation():
    tracker = GameTracker()
    play(tracker, "e4", "e5", "Nf3")
    tracker.observe(placement("e4", "e5"))
    assert tracker.observe(placement("e4", "e5", "Bc4")).sans == ("Bc4",)
    pgn = tracker.pgn()
    assert "2. Bc4 ( 2. Nf3 )" in pgn
    assert [san for _, san, _ in tracker.move_list()] == ["e4", "e5", "Bc4"]


def test_start_position_after_a_game_is_a_new_game_and_both_are_kept():
    tracker = GameTracker()
    play(tracker, "f3", "e5", "g4", "Qh4#")
    assert tracker.observe(chess.BaseBoard()).kind is TransitionKind.NEW_GAME
    play(tracker, "d4")
    games = tracker.pgn().strip().split("\n\n[Event")
    assert len(games) == 2
    assert "Qh4# 0-1" in games[0] and "1. d4 *" in games[1]


def test_flipped_board_is_not_a_move():
    tracker = GameTracker()
    play(tracker, "e4")
    rotated = placement("e4").transform(chess.flip_vertical).transform(chess.flip_horizontal)
    assert tracker.observe(rotated).kind is TransitionKind.FLIPPED
    assert tracker.board.ply() == 1


def test_unexplained_screen_is_reported_not_guessed():
    tracker = GameTracker()
    play(tracker, "e4")
    t = tracker.observe(chess.BaseBoard("4k3/8/8/8/8/8/8/4K3"))
    assert t.kind is TransitionKind.UNEXPLAINED and t.detail.startswith("No legal move explains the board")
    assert tracker.board.ply() == 1


def test_wrong_side_to_move_is_pointed_out():
    tracker = GameTracker(chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"))  # should be Black
    t = tracker.observe(placement("e4", "e5"))
    assert t.kind is TransitionKind.UNEXPLAINED
    assert "It looks like Black moved, but the game has White to move" in t.detail


def test_game_from_a_middlegame_position_numbers_moves_and_records_the_setup():
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 5 23"
    tracker = GameTracker(chess.Board(fen))
    t = tracker.observe(placement("Bc5", fen=fen))
    assert t.sans == ("Bc5",)
    pgn = tracker.pgn()
    assert f'[FEN "{fen}"]' in pgn and "23... Bc5" in pgn


def test_undo_takes_back_a_misread_move():
    tracker = GameTracker()
    play(tracker, "e4", "e5")
    assert tracker.undo() == "e5"
    assert tracker.board.ply() == 1 and tracker.board.turn == chess.BLACK
    tracker.observe(chess.BaseBoard())  # step back; e4 now has a move after it
    assert tracker.undo() is None


def test_clear_forgets_the_whole_session():
    tracker = GameTracker()
    play(tracker, "e4", "e5")
    tracker.new_game()
    play(tracker, "d4")
    assert tracker.has_moves and len(tracker.archived) == 1
    tracker.clear()
    assert not tracker.has_moves and tracker.archived == [] and tracker.board.fen() == chess.STARTING_FEN
    assert "e4" not in tracker.pgn() and "d4" not in tracker.pgn()


def test_annotations_go_on_the_analysed_move():
    tracker = GameTracker()
    play(tracker, "e4")
    assert not tracker.annotate("not this position", 10, None, 5)
    assert tracker.annotate(tracker.board.fen(), 31, None, 18)
    play_more = chess.Board()
    play_more.push_san("e4")
    play_more.push_san("e5")
    tracker.observe(chess.BaseBoard(play_more.board_fen()))
    assert tracker.annotate(tracker.board.fen(), None, -3, 30)
    assert tracker.current_pgn().splitlines()[-1] == "1. e4 { [%eval 0.31,18] } 1... e5 { [%eval #-3,30] } *"
    assert [n.san() for n in tracker.last_moves(5)] == ["e4", "e5"]


def test_start_keeps_moves_already_played():
    board = chess.Board()
    for san in ("e4", "e5"):
        board.push_san(san)
    tracker = GameTracker(board)
    assert [san for _, san, _ in tracker.move_list()] == ["e4", "e5"]
    assert tracker.move_list()[-1][2]  # the current move is the last one


def test_finished_game_result_in_pgn():
    tracker = GameTracker()
    play(tracker, "e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#")
    assert tracker.pgn().rstrip().endswith("4. Qxf7# 1-0")
    assert tracker.observe(placement("e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#")).kind is TransitionKind.NO_CHANGE
