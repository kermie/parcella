"""Defense against CSV formula injection (see docs/security.md).

Cells that start with `=`, `+`, `-`, or `@` are interpreted as formulas by
Excel and LibreOffice Calc when the file is opened. Any export column that
carries user-entered free text needs to go through `csv_safe()` before
`writer.writerow()`.
"""

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@")


def csv_safe(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _FORMULA_TRIGGER_CHARS:
        return "'" + text
    return text
