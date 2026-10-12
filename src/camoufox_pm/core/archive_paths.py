"""Portable archive names, including Windows device and namespace restrictions."""

import re


def portable_component(name: str) -> bool:
    if (
        not name
        or name.endswith((".", " "))
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or any(char in name for char in '<>:"|?*/\\')
    ):
        return False
    basename = name.split(".", 1)[0].rstrip(" .").upper()
    return not re.fullmatch(
        r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])",
        basename,
    )
