from soularr_fork.normalize import (
    fold_unicode,
    normalize_title,
    remove_feat,
    strip_release_suffixes,
)


class TestFoldUnicode:
    def test_curly_apostrophes_to_straight(self):
        assert fold_unicode("What’s") == "What's"
        assert fold_unicode("‘quoted’") == "'quoted'"
        assert fold_unicode("‛back") == "'back"

    def test_curly_double_quotes(self):
        assert fold_unicode("“Heroes”") == '"Heroes"'

    def test_dash_family(self):
        for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
            assert fold_unicode(f"a{dash}b") == "a-b"

    def test_accents_stripped(self):
        assert fold_unicode("Beyoncé") == "Beyonce"
        assert fold_unicode("Motörhead") == "Motorhead"

    def test_plain_ascii_unchanged(self):
        assert fold_unicode("All the Small Things") == "All the Small Things"


class TestStripReleaseSuffixes:
    def test_year_remaster(self):
        assert strip_release_suffixes("Song (2026 Remaster)") == "Song"
        assert strip_release_suffixes("Song (1999 Remastered)") == "Song"

    def test_remaster_bare_and_year_after(self):
        assert strip_release_suffixes("Song (Remaster)") == "Song"
        assert strip_release_suffixes("Song (Remastered)") == "Song"
        assert strip_release_suffixes("Song (Remastered 2011)") == "Song"

    def test_bracket_form(self):
        assert strip_release_suffixes("Song [Explicit]") == "Song"

    def test_whitelist_variants(self):
        for suffix in (
            "Mono",
            "Stereo",
            "Single Version",
            "Album Version",
            "Radio Edit",
            "Clean",
            "Deluxe",
            "Deluxe Edition",
            "Bonus Track",
        ):
            assert strip_release_suffixes(f"Song ({suffix})") == "Song"

    def test_stacked_strips_only_whitelisted_layer(self):
        assert (
            strip_release_suffixes("Why Can't This Be Love (Extended Version) (2026 Remaster)")
            == "Why Can't This Be Love (Extended Version)"
        )

    def test_stacked_whitelisted_layers_all_strip(self):
        assert strip_release_suffixes("Song (Mono) (2011 Remaster)") == "Song"

    def test_exclusive_parentheticals_survive(self):
        for title in (
            "Walkin' the Dog (live at Paul's Mall, 1973)",
            "Mama Kin (2024 mix)",
            "Love Sensation (Afterhours mix)",
            "Why Can't This Be Love (Extended Version)",
            "RED FLAG. (Chromeo remix)",
            "MORNING DEW (DONK)",
        ):
            assert strip_release_suffixes(title) == title

    def test_non_trailing_parenthetical_untouched(self):
        assert strip_release_suffixes("(Mono) Song") == "(Mono) Song"

    def test_whole_title_parenthetical_kept(self):
        assert strip_release_suffixes("(Mono)") == "(Mono)"


class TestRemoveFeat:
    def test_trailing_clauses(self):
        assert remove_feat("Song feat. Artist") == "Song"
        assert remove_feat("Song featuring Artist B") == "Song"
        assert remove_feat("Song ft. Artist") == "Song"

    def test_bare_with_not_a_credit_marker(self):
        # 'with' appears in ordinary titles; only the parenthetical form counts
        assert remove_feat("Song with Artist") == "Song with Artist"
        assert remove_feat("Home with You") == "Home with You"

    def test_feat_parenthetical(self):
        assert remove_feat("Song (feat. Artist)") == "Song"
        assert remove_feat("Song (feat. Artist) (Remix)") == "Song (Remix)"
        assert remove_feat("Song [ft. Artist]") == "Song"
        assert remove_feat("Song (with Artist)") == "Song"

    def test_no_feat_untouched(self):
        title = "Walkin' the Dog (live at Paul's Mall, 1973)"
        assert remove_feat(title) == title

    def test_with_inside_parenthetical_not_truncated(self):
        title = "Song (Live with the Orchestra)"
        assert remove_feat(title) == title


class TestNormalizeTitle:
    def test_slash_title_survives(self):
        assert normalize_title("S/C/A/R/E/C/R/O/W") == "s c a r e c r o w"

    def test_curly_vs_straight_apostrophe_equal(self):
        assert normalize_title("What’s My Age Again?") == normalize_title(
            "What's My Age Again?"
        )

    def test_remaster_layer_strips_extended_version_remains(self):
        assert (
            normalize_title("Why Can’t This Be Love (Extended Version) (2026 Remaster)")
            == "why can t this be love extended version"
        )

    def test_feat_and_suffix_combined(self):
        assert normalize_title("Song (feat. Somebody) [2011 Remaster]") == "song"

    def test_trailing_feat_behind_suffix_parenthetical(self):
        # Suffix strip must run before feat removal or the trailing suffix
        # parenthetical blocks _FEAT_TRAILING's paren-free-remainder guard
        assert normalize_title("Song feat. Artist (2011 Remaster)") == "song"

    def test_home_with_you_not_truncated(self):
        # Regression: bare 'with' used to truncate to 'home', causing the F4
        # dedup to wrongly skip singles like 'Home with You'
        assert normalize_title("Home with You") == "home with you"
        assert normalize_title("Home with You") != normalize_title("Home")

    def test_idempotent_on_with_title(self):
        once = normalize_title("song live with strings")
        assert once == "song live with strings"
        assert normalize_title(once) == once

    def test_whitespace_collapsed(self):
        assert normalize_title("  A   B  ") == "a b"

    def test_unicode_dash_folds(self):
        assert normalize_title("Ke‐sha") == normalize_title("Ke-sha")
