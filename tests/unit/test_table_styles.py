"""Tests for table styling (handlers/table_styles.py, #47).

The HTML below is what Quarto 1.7.32 renders for these tables, so the pass is
tested against the markup it really receives."""

import logging
import os
import re

import pytest

from handlers.table_styles import (
    TABLE_FEATURES,
    TABLE_VALUES,
    TARGETS,
    VALUE_KINDS,
    VALUE_RULES,
    TableSettings,
    check_value,
    inline_table_styles,
    load_table_settings,
    resolve_vars,
)

# | A | B |  with three body rows
PLAIN = """<table class="caption-top table">
<thead>
<tr class="header">
<th>A</th>
<th>B</th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td>1</td>
<td>2</td>
</tr>
<tr class="even">
<td>3</td>
<td>4</td>
</tr>
<tr class="odd">
<td>5</td>
<td>6</td>
</tr>
</tbody>
</table>"""

# : My caption {.striped .bordered .sm .hover}, with aligned columns
ALL_FEATURES = """<table class="table-striped table-bordered table-sm small table-hover caption-top table">
<caption>My caption</caption>
<thead>
<tr class="header">
<th style="text-align: left;">Left</th>
<th style="text-align: right;">Right</th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td style="text-align: left;">a</td>
<td style="text-align: right;">b</td>
</tr>
</tbody>
</table>"""

# : Results {#tbl-res .striped .schema}
CROSSREF = """<div id="tbl-res" class="striped schema quarto-float quarto-figure quarto-figure-center anchored">
<figure class="quarto-float quarto-float-tbl figure">
<figcaption class="quarto-float-caption-top quarto-float-caption quarto-float-tbl" id="tbl-res-caption-0ceaefa1">
Table&nbsp;1: Results
</figcaption>
<div aria-describedby="tbl-res-caption-0ceaefa1">
<table class="table-striped schema caption-top table">
<thead>
<tr class="header">
<th>A</th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td>1</td>
</tr>
</tbody>
</table>
</div>
</figure>
</div>"""

# ::: {.cqs-table stripe="#eef3f8" border="#999" header-bg="#003865" header-fg="white"}
WRAPPED = """<div class="cqs-table" data-stripe="#eef3f8" data-border="#999" data-header-bg="#003865" data-header-fg="white">
<table class="table-striped table-bordered caption-top table">
<thead>
<tr class="header">
<th>Vecka</th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td>44</td>
</tr>
<tr class="even">
<td>45</td>
</tr>
</tbody>
</table>
</div>"""

# A hand-written <table> in the .qmd: Quarto adds no table class.
RAW_HTML = """<table class="mine">
<tbody><tr>
<th>
H1
</th>
</tr>
<tr>
<td>
x
</td>
</tr>
</tbody></table>"""

# A grid table without a header row.
GRID = """<table class="caption-top table">
<colgroup>
<col style="width: 8%">
<col style="width: 8%">
</colgroup>
<tbody>
<tr class="odd">
<td>g1</td>
<td>g2</td>
</tr>
<tr class="even">
<td>g3</td>
<td>g4</td>
</tr>
</tbody>
</table>"""


def _on(**kw):
    """Settings for a course that uses table styling."""
    return TableSettings(on=True, **kw)


def _table_class(html, n=0):
    return re.findall(r'<table class="([^"]*)"', html)[n]


def _styles(html, tag):
    """The style attribute of every <tag>, '' where there is none."""
    return [m.group(1) or '' for m in re.finditer(rf'<{tag}\b(?:[^>]*?style="([^"]*)")?[^>]*>', html)]


def _body_rows(html):
    tbody = html[html.index('<tbody'):]
    return _styles(tbody, 'tr')


# --- Layer 0: no settings ------------------------------------------------------

class TestNoSettings:

    @pytest.mark.parametrize("html", [PLAIN, ALL_FEATURES, CROSSREF, RAW_HTML, GRID])
    def test_html_comes_back_byte_for_byte(self, html):
        assert inline_table_styles(html, TableSettings()) == html
        assert inline_table_styles(html, None) == html

    def test_a_wrapper_styles_only_its_own_table(self):
        page = PLAIN + "\n" + WRAPPED + "\n" + ALL_FEATURES
        out = inline_table_styles(page, TableSettings())
        assert out.startswith(PLAIN + "\n")
        assert out.endswith("\n" + ALL_FEATURES)
        assert "cqs-table" not in out
        assert "#eef3f8" in out

    def test_commented_out_markup_is_left_alone(self):
        html = "<!-- " + WRAPPED + " -->\n" + PLAIN
        assert inline_table_styles(html, _on(default=("striped",))) == (
            "<!-- " + WRAPPED + " -->\n" + inline_table_styles(PLAIN, _on(default=("striped",))))


# --- Layer 1: the course -------------------------------------------------------

class TestCourseSettings:

    def test_header_colours_reach_every_table(self):
        out = inline_table_styles(PLAIN, _on(root={"header-bg": "#003865", "header-fg": "#fff"}))
        assert _styles(out, 'th') == ["background-color: #003865; color: #fff;"] * 2
        assert _styles(out, 'td') == [""] * 6
        assert _table_class(out) == "caption-top table"

    def test_default_style_stripes_the_first_third_fifth_body_row(self):
        out = inline_table_styles(PLAIN, _on(default=("striped",)))
        assert _body_rows(out) == ["background-color: #f0f4f7;", "", "background-color: #f0f4f7;"]

    def test_own_features_replace_the_default(self):
        bordered = PLAIN.replace('class="caption-top table"', 'class="table-bordered caption-top table"')
        out = inline_table_styles(bordered, _on(default=("striped",)))
        assert _body_rows(out) == ["", "", ""]
        assert all("border: 1px solid #dddddd" in s for s in _styles(out, 'td'))

    def test_plain_opts_a_table_out(self):
        plain = PLAIN.replace('class="caption-top table"', 'class="plain caption-top table"')
        settings = _on(default=("striped",), root={"header-bg": "#003865"})
        assert inline_table_styles(plain, settings) == plain

    def test_hand_written_html_tables_are_left_alone(self):
        settings = _on(default=("striped",), root={"header-bg": "#003865"})
        assert inline_table_styles(RAW_HTML, settings) == RAW_HTML

    def test_inlined_features_drop_their_class_and_hover_keeps_its_own(self):
        out = inline_table_styles(ALL_FEATURES, _on())
        assert _table_class(out) == "small table-hover caption-top table"
        assert 'style="border-collapse: collapse;"' in out

    def test_hover_from_the_default_adds_the_class_canvas_needs(self):
        out = inline_table_styles(PLAIN, _on(default=("hover",)))
        assert _table_class(out) == "caption-top table table-hover"
        assert "style=" not in out

    def test_existing_declarations_stay_first(self):
        out = inline_table_styles(ALL_FEATURES, _on(root={"header-bg": "#003865"}))
        assert _styles(out, 'th')[0] == (
            "text-align: left; background-color: #003865; "
            "border: 1px solid #dddddd; padding: 4px 5px;")

    def test_a_border_colour_recolours_the_row_lines(self):
        out = inline_table_styles(PLAIN, _on(root={"border": "#999"}))
        assert _styles(out, 'td') == ["border-top: 1px solid #999;"] * 6
        assert _styles(out, 'th') == ["", ""]

    def test_bordered_cells_get_one_border_declaration(self):
        out = inline_table_styles(ALL_FEATURES, _on(root={"border": "#999"}))
        assert _styles(out, 'td')[0] == "text-align: left; border: 1px solid #999; padding: 4px 5px;"

    def test_a_table_in_a_cross_reference_float_is_styled_and_the_float_is_not(self):
        out = inline_table_styles(CROSSREF, _on())
        assert out.startswith(CROSSREF[:CROSSREF.index("<table")])
        assert _table_class(out) == "schema caption-top table"
        assert _body_rows(out) == ["background-color: #f0f4f7;"]

    def test_rows_without_a_header_are_counted_from_the_first(self):
        out = inline_table_styles(GRID, _on(default=("striped",)))
        assert _body_rows(out) == ["background-color: #f0f4f7;", ""]
        assert '<col style="width: 8%">' in out


# --- Layers 2 and 3: one table -------------------------------------------------

class TestNamedStylesAndWrappers:

    def test_a_named_style_overrides_the_course(self):
        schema = PLAIN.replace('class="caption-top table"', 'class="table-striped schema caption-top table"')
        striped = PLAIN.replace('class="caption-top table"', 'class="table-striped caption-top table"')
        settings = _on(root={"stripe": "#111111"}, named={"schema": {"stripe": "#eef3f8"}})
        out = inline_table_styles(schema + "\n" + striped, settings)
        first, second = out.split("</table>")[:2]
        assert "#eef3f8" in first and "#111111" not in first
        assert "#111111" in second and "#eef3f8" not in second

    def test_a_named_style_alone_reaches_only_its_tables(self):
        schema = PLAIN.replace('class="caption-top table"', 'class="table-striped schema caption-top table"')
        settings = TableSettings(named={"schema": {"stripe": "#eef3f8"}})
        out = inline_table_styles(PLAIN + "\n" + schema, settings)
        assert out.startswith(PLAIN + "\n")
        assert "#eef3f8" in out

    def test_the_wrapper_overrides_named_style_and_course(self):
        named = WRAPPED.replace('class="table-striped', 'class="schema table-striped')
        settings = _on(root={"stripe": "#111111"}, named={"schema": {"stripe": "#222222"}})
        out = inline_table_styles(named, settings)
        assert _body_rows(out)[0] == "background-color: #eef3f8;"

    def test_the_innermost_wrapper_wins(self):
        nested = '<div class="cqs-table" data-stripe="#111111" data-border="#999">\n' + WRAPPED + '\n</div>'
        out = inline_table_styles(nested, TableSettings())
        assert _body_rows(out)[0] == "background-color: #eef3f8;"
        assert "cqs-table" not in out
        assert out.count("<div") == out.count("</div>") == 0

    def test_the_wrapper_values_reach_every_part(self):
        out = inline_table_styles(WRAPPED, TableSettings())
        assert _styles(out, 'th') == ["background-color: #003865; color: white; border: 1px solid #999;"]
        assert _body_rows(out) == ["background-color: #eef3f8;", ""]
        assert _styles(out, 'td') == ["border: 1px solid #999;"] * 2

    def test_a_bad_wrapper_value_is_ignored_and_reported(self, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        bad = WRAPPED.replace('data-stripe="#eef3f8"', 'data-stripe="red; display: none"')
        out = inline_table_styles(bad, TableSettings())
        assert "display" not in out
        assert _body_rows(out)[0] == "background-color: #f0f4f7;"
        assert "stripe" in caplog.text

    def test_a_wrapper_value_may_name_a_brand_colour(self):
        brand = WRAPPED.replace('data-header-bg="#003865"', 'data-header-bg="var(--brand-heading)"')
        out = inline_table_styles(brand, TableSettings(custom={"--brand-heading": "#961B81"}))
        assert "background-color: #961B81" in _styles(out, 'th')[0]

    def test_plain_inside_a_wrapper_still_wins(self):
        plain = WRAPPED.replace('class="table-striped', 'class="plain table-striped')
        out = inline_table_styles(plain, TableSettings())
        assert "style=" not in out
        assert "cqs-table" not in out


# --- Reading config.toml and branding.css --------------------------------------

def _course(tmp_path, config="", css=None):
    toml = '[branding]\ncss = "branding.css"\n' + config
    (tmp_path / "config.toml").write_text(toml, encoding="utf-8")
    if css is not None:
        (tmp_path / "branding.css").write_text(css, encoding="utf-8")
    return str(tmp_path)


class TestLoadTableSettings:

    def test_a_course_with_neither_file_set_is_off(self, tmp_path):
        css = ":root { --brand-heading: #003865; }\ntable th { background-color: red; }\n"
        settings = load_table_settings(_course(tmp_path, css=css))
        assert not settings.on and not settings.named and not settings.problems

    def test_a_tables_section_turns_course_styling_on(self, tmp_path):
        settings = load_table_settings(_course(tmp_path, '[tables]\nstyle = ["striped", "hover"]\n'))
        assert settings.on
        assert settings.default == ("striped", "hover")

    def test_an_empty_tables_section_is_enough(self, tmp_path):
        assert load_table_settings(_course(tmp_path, "[tables]\n")).on

    def test_unknown_options_and_keys_are_problems(self, tmp_path):
        settings = load_table_settings(
            _course(tmp_path, '[tables]\nstyle = ["stripes"]\ncolour = "red"\n'))
        messages = " | ".join(m for _, m in settings.problems)
        assert "'stripes' is not an option" in messages
        assert "colour" in messages
        assert settings.default == ()

    def test_reads_root_and_named_values_but_not_older_rules(self, tmp_path):
        css = """
:root { --brand-heading: #003865; --table-header-bg: var(--brand-heading); --table-header-fg: #fff; }
table th, table thead tr { background-color: var(--brand-heading) !important; }
table tr:nth-child(even) { background-color: #f0f4f7; }
.schema { --table-stripe: #eef3f8; --table-border: #999; }
/* .draft { --table-stripe: #ffeeee; } */
"""
        settings = load_table_settings(_course(tmp_path, css=css))
        assert settings.on
        assert settings.root == {"header-bg": "#003865", "header-fg": "#fff"}
        assert settings.named == {"schema": {"stripe": "#eef3f8", "border": "#999"}}
        assert not settings.problems

    def test_a_named_style_alone_does_not_turn_the_course_on(self, tmp_path):
        settings = load_table_settings(_course(tmp_path, css=".schema { --table-stripe: #eef3f8; }"))
        assert not settings.on
        assert settings.named == {"schema": {"stripe": "#eef3f8"}}

    def test_what_cannot_be_used_is_a_problem(self, tmp_path):
        css = """
:root { --table-strip: #eee; --table-border: 1px solid; }
table.schema { --table-stripe: #eef3f8; }
"""
        settings = load_table_settings(_course(tmp_path, css=css))
        messages = " | ".join(m for _, m in settings.problems)
        assert "--table-strip is not a table setting" in messages
        assert "--table-border: '1px solid' is not a colour" in messages
        assert "table.schema" in messages
        assert {f for f, _ in settings.problems} == {"branding.css"}
        assert not settings.on and not settings.named

    @pytest.mark.parametrize("folder", [
        ("content_kit", "starter"), ("tests", "fixtures", "e2e_content"), ("Example",)])
    def test_shipped_courses_leave_tables_to_canvas(self, folder):
        """New courses start from the starter, so its examples must stay
        comments; the E2E course relies on its unwrapped tables being Canvas's."""
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        settings = load_table_settings(os.path.join(root, *folder))
        assert not settings.on and not settings.named and not settings.problems

    def test_problems_are_logged_once_for_the_sync(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        root = _course(tmp_path, '[tables]\nstyle = ["stripes"]\n')
        load_table_settings(root)
        assert "stripes" not in caplog.text
        load_table_settings(root, warn=True)
        load_table_settings(root, warn=True)
        assert caplog.text.count("'stripes'") == 1


# --- Values --------------------------------------------------------------------

class TestValues:

    @pytest.mark.parametrize("value", ["#abc", "#aabbcc", "#aabbccdd", "rgb(0, 56, 101)",
                                       "rgba(0,0,0,.5)", "hsl(210 40% 96%)", "white"])
    def test_accepts_colours(self, value):
        assert check_value("stripe", f" {value} ") == value

    @pytest.mark.parametrize("value", ["red; display: none", '#fff" onclick="x', "url(x.png)",
                                       "#12", "", "var(--missing)"])
    def test_rejects_what_is_not_a_colour(self, value):
        with pytest.raises(ValueError):
            check_value("stripe", value)

    def test_var_resolves_through_other_properties_and_falls_back(self):
        custom = {"--a": "var(--b)", "--b": "#123456"}
        assert resolve_vars("var(--a)", custom) == "#123456"
        assert resolve_vars("var(--nope, #fff)", custom) == "#fff"
        assert resolve_vars("var(--nope)", custom) == "var(--nope)"

    def test_a_reference_loop_stops(self):
        assert "var(" in resolve_vars("var(--a)", {"--a": "var(--a)"})


# --- The registries: what makes a new style a matter of adding entries ----------

class TestRegistry:

    def _all_rules(self):
        return list(VALUE_RULES) + [r for f in TABLE_FEATURES.values() for r in f.rules]

    def test_every_rule_names_a_known_target(self):
        assert {r.target for r in self._all_rules()} <= set(TARGETS)

    def test_every_rule_uses_only_known_values(self):
        used = {u for r in self._all_rules() for u in r.uses}
        assert used <= set(TABLE_VALUES)

    def test_unless_names_features(self):
        assert {u for r in self._all_rules() for u in r.unless} <= set(TABLE_FEATURES)

    def test_every_value_has_a_kind_and_a_valid_default(self):
        for value in TABLE_VALUES.values():
            assert value.kind in VALUE_KINDS
            if value.default:
                assert check_value(value.name, value.default) == value.default

    def test_every_feature_is_drawn_by_someone(self):
        for feature in TABLE_FEATURES.values():
            assert feature.rules or feature.canvas_draws, feature.name

    def test_quarto_classes_are_distinct(self):
        classes = [f.quarto_class for f in TABLE_FEATURES.values()]
        assert len(classes) == len(set(classes))

    def test_a_new_feature_needs_nothing_but_its_entry(self, monkeypatch):
        """The point of the registry: a style added there is drawn, with no
        other code to touch."""
        from handlers import table_styles
        from handlers.table_styles import Rule, TableFeature
        features = dict(TABLE_FEATURES)
        features["first-col"] = TableFeature(
            "first-col", "first-col", rules=(Rule("first_cell", "font-weight: bold"),))
        targets = dict(TARGETS, first_cell=lambda e: e.kind == "cell" and e.col == 0)
        monkeypatch.setattr(table_styles, "TABLE_FEATURES", features)
        monkeypatch.setattr(table_styles, "TARGETS", targets)
        html = PLAIN.replace('class="caption-top table"', 'class="first-col caption-top table"')
        out = inline_table_styles(html, _on())
        assert _styles(out, 'td') == ["font-weight: bold;", ""] * 3
        assert _table_class(out) == "caption-top table"
