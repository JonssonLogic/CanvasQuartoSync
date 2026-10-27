"""Tests for handlers/qmd_preprocessor_yh.py — the YH study guide variant."""

from handlers.qmd_preprocessor_yh import (
    _grade_colors,
    _process_grading_criteria,
    _process_teaching_staff,
    preprocess_study_guide,
)
from handlers.qmd_preprocessor import GRADE_COLORS


YH_TABLE = (
    "| Läranderesultat | IG | G | VG |\n"
    "|:--|:--|:--|:--|\n"
    "| LR 1 | Kan inte | Kan | Kan väl |"
)


class TestGradingCriteria:

    def test_labels_come_from_the_header_row(self):
        out = _process_grading_criteria(YH_TABLE)
        assert "IG:</strong> Kan inte" in out
        assert "G:</strong> Kan</p>" in out
        assert "VG:</strong> Kan väl" in out
        assert "Fail:" not in out

    def test_cards_are_collapsible(self):
        out = _process_grading_criteria(YH_TABLE)
        assert "<details" in out and "<summary" in out and "LR 1" in out

    def test_numbered_title_does_not_start_a_list(self):
        """'1. LR' at the start of <summary> made Quarto drop the rest of the PDF."""
        table = YH_TABLE.replace("| LR 1 |", "| 1. Beskriva principer |")
        out = _process_grading_criteria(table)
        assert ">1&#46; Beskriva principer</summary>" in out

    def test_fail_is_red_and_top_grade_green(self):
        assert _grade_colors(3) == [GRADE_COLORS['Fail'], GRADE_COLORS['3'], GRADE_COLORS['5']]
        assert _grade_colors(2) == [GRADE_COLORS['Fail'], GRADE_COLORS['5']]


class TestTeachingStaff:

    TABLE = (
        "| Namn | Roll | Bild | Länk | Kontakt |\n"
        "|:--|:--|:--|:--|:--|\n"
        "| Mirza | Kursansvarig | assets/Mirza.jpg |  |  |\n"
        "| Maja | Utbildningsledare |  |  |  |"
    )

    def test_photo_is_optional(self):
        out = _process_teaching_staff(self.TABLE)
        assert out.count("<img") == 1
        assert "Maja" in out

    def test_pdf_table_uses_the_authors_headers(self):
        out = _process_teaching_staff(self.TABLE)
        assert "Namn" in out and "Roll" in out and "Kontakt" in out
        assert "Name" not in out

    def test_contact_defaults_to_canvas(self):
        out = _process_teaching_staff(self.TABLE)
        assert "Canvas-meddelande" in out

    def test_email_contact_is_a_mailto_link(self):
        table = self.TABLE.replace("| Maja | Utbildningsledare |  |  |  |",
                                   "| Mats | Examinator |  |  | mats@ju.se |")
        out = _process_teaching_staff(table)
        assert '<a href="mailto:mats@ju.se">mats@ju.se</a>' in out


class TestPreprocess:

    QMD = "---\ntitle: T\ncanvas:\n  preprocess: true\n---\n\n# Intro\n\nHej\n"

    def test_cover_says_yh_points(self):
        out = preprocess_study_guide(self.QMD, {'language': 'swedish', 'credits': '25 yhp',
                                                'course_code': 'YTOPK3'})
        assert "Yh-poäng: & 25 yhp" in out
        assert "Högskolepoäng" not in out

    def test_opt_in_required(self):
        qmd = "---\ntitle: T\n---\n\n# Intro\n"
        assert preprocess_study_guide(qmd, {}) == qmd
