import logging

import pytest

from soularr_fork import queries
from soularr_fork.queries import SCOPE_ARTIST, SCOPE_TITLE, build_query_ladder, run_ladder


def texts(ladder):
    return [query.text for query in ladder]


class TestStockBehavior:
    """Every fork option off = upstream's single query."""

    def test_bare_title(self):
        ladder = build_query_ladder("Lynyrd Skynyrd", "Icon 2")
        assert texts(ladder) == ["Icon 2"]
        assert ladder[0].scope == SCOPE_TITLE

    def test_one_character_title_always_gets_the_artist(self):
        ladder = build_query_ladder("Ed Sheeran", "5")
        assert texts(ladder) == ["Ed Sheeran 5"]
        assert ladder[0].scope == SCOPE_ARTIST

    def test_upstream_prepend_has_no_fallback(self):
        assert texts(build_query_ladder("Tracy Lawrence", "Strong", prepend_artist=True)) == ["Tracy Lawrence Strong"]

    def test_title_fallback_alone_changes_nothing(self):
        assert texts(build_query_ladder("Tracy Lawrence", "Strong", title_fallback=True)) == ["Strong"]

    def test_sanitize_keeps_raw_as_second_attempt(self):
        ladder = build_query_ladder("Madonna", "Frozen (Fireboy DML remix)", sanitize=True)
        assert texts(ladder) == ["Frozen Fireboy DML remix", "Frozen (Fireboy DML remix)"]
        assert [query.sanitized for query in ladder] == [True, False]

    def test_sanitize_without_effect_searches_once(self):
        assert texts(build_query_ladder("Madonna", "Rebel Heart", sanitize=True)) == ["Rebel Heart"]

    def test_unsearchable_sanitized_form_is_skipped(self):
        # searching '' errors out in slskd
        assert texts(build_query_ladder("Sigur Ros", "( )", sanitize=True)) == ["( )"]


class TestArtistFirstLadder:
    def test_artist_then_title(self):
        ladder = build_query_ladder("Travis Tritt", "The Storm", prepend_artist=True, title_fallback=True, sanitize=True)
        assert texts(ladder) == ["Travis Tritt The Storm", "The Storm"]
        assert [query.scope for query in ladder] == [SCOPE_ARTIST, SCOPE_TITLE]

    def test_full_ladder_order(self):
        ladder = build_query_ladder("AC/DC", "Who Made Who?", prepend_artist=True, title_fallback=True, sanitize=True)
        assert texts(ladder) == ["AC DC Who Made Who", "AC/DC Who Made Who?", "Who Made Who", "Who Made Who?"]

    def test_accents_fold_in_the_sanitized_form(self):
        ladder = build_query_ladder("Beyoncé", "Lemonade", prepend_artist=True, sanitize=True)
        assert texts(ladder) == ["Beyonce Lemonade", "Beyoncé Lemonade"]

    @pytest.mark.parametrize("artist", [
        {"artistName": "Various Artists", "foreignArtistId": queries.VARIOUS_ARTISTS_MBID},
        {"artistName": "Verschiedene Interpreten", "foreignArtistId": queries.VARIOUS_ARTISTS_MBID},
        {"artistName": "various artists", "foreignArtistId": "x"},
        "Various Artists",
        "VA",
    ])
    def test_various_artists_is_never_prepended(self, artist):
        assert queries.is_various_artists(artist)
        ladder = build_query_ladder("Various Artists", "Ladies Night", prepend_artist=True, title_fallback=True, various=True)
        assert texts(ladder) == ["Ladies Night"]

    @pytest.mark.parametrize("artist", [{"artistName": "Madonna", "foreignArtistId": "79239441"}, "Madonna", "Vangelis", None, {}])
    def test_regular_artists_are_not_various(self, artist):
        assert not queries.is_various_artists(artist)

    @pytest.mark.parametrize("artist,title", [
        ("Shwayze", "Shwayze"),
        ("Toby Keith", "20th Century Masters: The Millennium Collection: The Best of Toby Keith"),
        ("Lynyrd Skynyrd", "The Essential Lynyrd Skynyrd"),
        ("Beyoncé", "BEYONCE"),
    ])
    def test_artist_already_in_title_is_not_repeated(self, artist, title):
        assert texts(build_query_ladder(artist, title, prepend_artist=True, title_fallback=True)) == [title]

    @pytest.mark.parametrize("artist,title", [
        ("Yes", "Yesterdays"),
        ("America", "American Authors"),
        ("Heart", "Rebel Heartache"),
    ])
    def test_artist_inside_a_longer_word_does_not_count(self, artist, title):
        assert texts(build_query_ladder(artist, title, prepend_artist=True)) == [f"{artist} {title}"]

    def test_one_character_title_has_no_title_fallback(self):
        assert texts(build_query_ladder("Ed Sheeran", "5", prepend_artist=True, title_fallback=True)) == ["Ed Sheeran 5"]

    def test_missing_artist_name_falls_back_to_title(self):
        assert texts(build_query_ladder("", "Strong", prepend_artist=True, title_fallback=True)) == ["Strong"]

    def test_blacklist_applies_to_every_rung(self):
        ladder = build_query_ladder("Alabama", "Mountain Music (Deluxe)", prepend_artist=True, title_fallback=True, sanitize=True, blacklist=["deluxe"])
        assert texts(ladder) == ["Alabama Mountain Music", "Alabama Mountain Music ()", "Mountain Music", "Mountain Music ()"]
        assert ladder[0].source == "Alabama Mountain Music (Deluxe)"

    def test_blacklist_that_empties_the_query_leaves_no_rung(self):
        assert build_query_ladder("Artist", "Deluxe", blacklist=["deluxe"]) == []

    def test_duplicates_differing_only_in_case_collapse(self):
        ladder = build_query_ladder("Artist", "Title", prepend_artist=True, title_fallback=True, sanitize=True)
        assert len(texts(ladder)) == len({text.casefold() for text in texts(ladder)}) == 2


class FakeSearch:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return self.answers.get(text)


class TestRunLadder:
    def setup_method(self):
        self.ladder = build_query_ladder("AC/DC", "Who Made Who?", prepend_artist=True, title_fallback=True, sanitize=True)
        self.pauses = []
        self.lines = []

    def run(self, answers):
        search = FakeSearch(answers)
        responses, query = run_ladder(self.ladder, search, pause=lambda: self.pauses.append(1), log=self.lines.append)
        return responses, query, search.calls

    def test_first_hit_stops_the_ladder(self):
        responses, query, calls = self.run({"AC DC Who Made Who": [{"username": "peer"}]})
        assert responses == [{"username": "peer"}]
        assert query.scope == SCOPE_ARTIST and query.sanitized
        assert calls == ["AC DC Who Made Who"]
        assert self.pauses == []
        assert self.lines == ["Sanitized search query: 'AC/DC Who Made Who?' -> 'AC DC Who Made Who'", "Searching for album: AC DC Who Made Who"]

    def test_empty_and_failed_searches_both_fall_through(self):
        # [] = nobody answered, None = the search itself failed
        responses, query, calls = self.run({"AC DC Who Made Who": [], "AC/DC Who Made Who?": None, "Who Made Who": [{"username": "peer"}]})
        assert responses == [{"username": "peer"}]
        assert query.scope == SCOPE_TITLE
        assert calls == ["AC DC Who Made Who", "AC/DC Who Made Who?", "Who Made Who"]
        assert len(self.pauses) == 2
        assert self.lines[-1] == "No results for 'AC/DC Who Made Who?'. Retrying with sanitized title-only query: Who Made Who"

    def test_nothing_anywhere(self):
        responses, query, calls = self.run({})
        assert responses is None and query is None
        assert len(calls) == 4 and len(self.pauses) == 3

    def test_empty_ladder(self):
        assert run_ladder([], FakeSearch({})) == (None, None)

    def test_blacklist_filter_is_logged(self):
        ladder = build_query_ladder("Alabama", "Mountain Music Deluxe", blacklist=["deluxe"])
        run_ladder(ladder, FakeSearch({}), log=self.lines.append)
        assert self.lines == ["Filtered search query: 'Mountain Music Deluxe' -> 'Mountain Music'", "Searching for album: Mountain Music"]

    def test_runs_without_pause_or_log(self):
        responses, _ = run_ladder(self.ladder, FakeSearch({"Who Made Who?": [1]}))
        assert responses == [1]


class TestPositiveLimit:
    @pytest.mark.parametrize("raw,expected", [("", 100), (None, 100), ("  ", 100), ("250", 250), (" 1000 ", 1000)])
    def test_values(self, raw, expected):
        assert queries.positive_limit(raw, 100, "response_limit") == expected

    @pytest.mark.parametrize("raw", ["0", "-5", "lots", "2.5"])
    def test_bad_values_warn_and_use_the_default(self, raw, caplog):
        with caplog.at_level(logging.WARNING):
            assert queries.positive_limit(raw, 100, "response_limit", logging.getLogger("soularr")) == 100
        assert "response_limit" in caplog.text


class TestPollDeadline:
    @pytest.mark.parametrize("timeout_ms,expected", [(5000, 65), (15000, 75), (0, 60), (-1, 60)])
    def test_milliseconds_become_seconds_plus_grace(self, timeout_ms, expected):
        assert queries.poll_deadline(timeout_ms) == expected

    def test_upstream_default_no_longer_means_83_minutes(self):
        assert queries.poll_deadline(5000) < 120
