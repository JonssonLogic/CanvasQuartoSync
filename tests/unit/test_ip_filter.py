"""Tests for handlers/ip_filter.py - canvas.ip_filter parsing and formatting.

One frontmatter value serves both engines: New Quizzes take [start, end] pairs,
Classic quizzes take a comma-separated string of addresses and CIDR blocks.
"""

from ipaddress import IPv4Address as A

import pytest

from handlers.ip_filter import ip_filter_to_classic, ip_filter_to_new_quiz, parse_ip_filter


class TestParse:

    def test_single_address(self):
        assert parse_ip_filter("193.10.0.1") == [(A("193.10.0.1"), A("193.10.0.1"))]

    def test_range(self):
        assert parse_ip_filter("193.10.0.1-193.10.255.255") == [
            (A("193.10.0.1"), A("193.10.255.255"))]

    def test_range_tolerates_whitespace_around_dash(self):
        assert parse_ip_filter(" 193.10.0.1 - 193.10.255.255 ") == [
            (A("193.10.0.1"), A("193.10.255.255"))]

    def test_cidr(self):
        assert parse_ip_filter("193.10.0.0/16") == [(A("193.10.0.0"), A("193.10.255.255"))]

    def test_comma_separated_string_keeps_order(self):
        assert parse_ip_filter("10.0.0.1, 192.168.0.0/24,172.16.0.1-172.16.0.9") == [
            (A("10.0.0.1"), A("10.0.0.1")),
            (A("192.168.0.0"), A("192.168.0.255")),
            (A("172.16.0.1"), A("172.16.0.9")),
        ]

    def test_yaml_list(self):
        assert parse_ip_filter(["10.0.0.1", "192.168.0.0/24"]) == [
            (A("10.0.0.1"), A("10.0.0.1")),
            (A("192.168.0.0"), A("192.168.0.255")),
        ]

    def test_stray_commas_are_ignored(self):
        assert parse_ip_filter("10.0.0.1,,10.0.0.2,") == [
            (A("10.0.0.1"), A("10.0.0.1")), (A("10.0.0.2"), A("10.0.0.2"))]


class TestParseErrors:

    @pytest.mark.parametrize("value,fragment", [
        ("", "no addresses"),
        (" , ", "no addresses"),
        ([], "no addresses"),
        (None, "expected a string or a list"),
        (193, "expected a string or a list"),
        ([193], "must be text"),
        ("193.10.0.256", "not a valid IPv4 address"),
        ("campus", "not a valid IPv4 address"),
        ("a-b-c", "not a valid range"),
        ("::1", "not a valid IPv4 address"),
        ("193.10.255.255-193.10.0.1", "starts after it ends"),
        ("1.1.1.1-1.1.1.2-1.1.1.3", "not a valid range"),
        ("193.10.0.0/33", "not a valid IPv4 CIDR"),
        ("193.10.0.1/16", "did you mean '193.10.0.0/16'"),
    ])
    def test_bad_input_raises_with_clear_message(self, value, fragment):
        with pytest.raises(ValueError, match="ip_filter") as exc:
            parse_ip_filter(value)
        assert fragment in str(exc.value)


class TestNewQuizFormat:

    def test_pairs_of_strings(self):
        assert ip_filter_to_new_quiz("193.10.0.0/16, 10.0.0.5") == [
            ["193.10.0.0", "193.10.255.255"], ["10.0.0.5", "10.0.0.5"]]


class TestClassicFormat:

    def test_single_address_stays_bare(self):
        assert ip_filter_to_classic("193.10.0.1") == "193.10.0.1"

    def test_cidr_passes_through(self):
        assert ip_filter_to_classic("193.10.0.0/16") == "193.10.0.0/16"

    def test_aligned_range_becomes_one_block(self):
        assert ip_filter_to_classic("193.10.0.0-193.10.255.255") == "193.10.0.0/16"

    def test_unaligned_range_becomes_exact_cover(self):
        # 193.10.0.1 .. 193.10.255.255 cannot be one block: .0 is excluded.
        result = ip_filter_to_classic("193.10.0.1-193.10.255.255")
        assert result.split(",") == [
            "193.10.0.1", "193.10.0.2/31", "193.10.0.4/30", "193.10.0.8/29",
            "193.10.0.16/28", "193.10.0.32/27", "193.10.0.64/26", "193.10.0.128/25",
            "193.10.1.0/24", "193.10.2.0/23", "193.10.4.0/22", "193.10.8.0/21",
            "193.10.16.0/20", "193.10.32.0/19", "193.10.64.0/18", "193.10.128.0/17",
        ]

    def test_several_entries_are_comma_joined(self):
        assert ip_filter_to_classic(["10.0.0.1", "192.168.0.0/24", "172.16.0.0-172.16.0.3"]) == (
            "10.0.0.1,192.168.0.0/24,172.16.0.0/30")

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            ip_filter_to_classic("10.0.0.300")
