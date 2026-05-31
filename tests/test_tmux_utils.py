from yolomux_lib.tmux_utils import tmux_exact_target_from_sessions
from yolomux_lib.tmux_utils import unique_session_names


def test_tmux_exact_target_disambiguates_numeric_session_names():
    assert tmux_exact_target_from_sessions("1", ["1", "2"]) == "1:"
    assert tmux_exact_target_from_sessions("%42", ["1"]) == "%42"
    assert tmux_exact_target_from_sessions("1:0.0", ["1"]) == "1:0.0"


def test_unique_session_names_uses_yolomux_sort_order():
    assert unique_session_names(["project2", "1", "project1", "1", "yolomux2", "yolomux1"]) == [
        "yolomux1",
        "yolomux2",
        "project1",
        "project2",
        "1",
    ]
