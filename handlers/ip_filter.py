"""
IP address filters for quizzes (``canvas.ip_filter``), shared by both engines.

Authors write one value for either engine: a comma-separated string or a YAML
list, where every entry is one of

  - a single IPv4 address   ``193.10.0.1``
  - an inclusive range      ``193.10.0.1-193.10.255.255``
  - a CIDR block            ``193.10.0.0/16``

The two engines want different shapes, so the value is parsed once into
inclusive ``(start, end)`` address pairs and each engine formats from those:

  - New Quizzes take the ranges directly:
    ``quiz_settings.filters.ips = [["start", "end"], ...]``.
  - Classic quizzes take ``quiz[ip_filter]`` as a comma-separated string of
    addresses and CIDR blocks, so ranges are split into the CIDR blocks that
    cover them exactly.

Everything here is pure and offline. Bad input raises ``ValueError`` with a
message naming the offending entry - ``validate_content.py`` reports it as an
ERROR, and at sync time it fails that one file (like an unreadable date) rather
than silently syncing a quiz with no IP restriction at all.
"""

import ipaddress


def _entries(value):
    """Split the raw frontmatter value into stripped, non-empty entry strings."""
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple)):
        raw = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(
                    f"ip_filter: every list entry must be text such as "
                    f"'193.10.0.0/16', got {item!r}")
            raw.append(item)
    else:
        raise ValueError(
            f"ip_filter: expected a string or a list of addresses, got {value!r}")

    entries = [part.strip() for item in raw for part in item.split(",")]
    entries = [e for e in entries if e]
    if not entries:
        raise ValueError("ip_filter: no addresses given - remove the key or add an address.")
    return entries


def _address(text, entry):
    try:
        return ipaddress.IPv4Address(text.strip())
    except ValueError:
        raise ValueError(
            f"ip_filter: {text.strip()!r} in {entry!r} is not a valid IPv4 address.") from None


def _parse_entry(entry):
    """One entry -> an inclusive (start, end) pair of IPv4Address."""
    if "/" in entry:
        try:
            net = ipaddress.IPv4Network(entry.replace(" ", ""), strict=True)
        except ValueError as e:
            if "host bits set" in str(e):
                loose = ipaddress.IPv4Network(entry.replace(" ", ""), strict=False)
                raise ValueError(
                    f"ip_filter: {entry!r} has host bits set - did you mean "
                    f"'{loose}'?") from None
            raise ValueError(f"ip_filter: {entry!r} is not a valid IPv4 CIDR block.") from None
        return net.network_address, net.broadcast_address

    if "-" in entry:
        parts = entry.split("-")
        if len(parts) != 2:
            raise ValueError(
                f"ip_filter: {entry!r} is not a valid range - write it as "
                f"'start-end', e.g. '193.10.0.1-193.10.255.255'.")
        start, end = _address(parts[0], entry), _address(parts[1], entry)
        if start > end:
            raise ValueError(
                f"ip_filter: range {entry!r} starts after it ends - put the lower "
                f"address first.")
        return start, end

    addr = _address(entry, entry)
    return addr, addr


def parse_ip_filter(value):
    """Parse ``canvas.ip_filter`` into a list of inclusive (start, end) pairs.

    Args:
        value: a comma-separated string or a list of strings (list entries may
            themselves hold commas).

    Returns:
        list of ``(IPv4Address, IPv4Address)`` tuples, in the order written.

    Raises:
        ValueError: on an unparseable address, a range whose start is after its
            end, a CIDR block with host bits set, or an empty value.
    """
    return [_parse_entry(e) for e in _entries(value)]


def ip_filter_to_new_quiz(value):
    """The New Quizzes ``quiz_settings.filters.ips`` list: ``[["start", "end"], ...]``."""
    return [[str(start), str(end)] for start, end in parse_ip_filter(value)]


def ip_filter_to_classic(value):
    """The Classic ``quiz[ip_filter]`` string: addresses and CIDR blocks, comma-separated.

    Ranges are expanded into the minimal set of CIDR blocks covering them
    exactly; a single-host block is written as the bare address.
    """
    parts = []
    for start, end in parse_ip_filter(value):
        for net in ipaddress.summarize_address_range(start, end):
            parts.append(str(net.network_address) if net.prefixlen == 32 else str(net))
    return ",".join(parts)
