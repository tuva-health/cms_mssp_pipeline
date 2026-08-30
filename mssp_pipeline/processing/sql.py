import re


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def validate_identifier(name: str, *, field_name: str = "identifier") -> str:
    if not name:
        raise ValueError(f"Invalid {field_name}: must not be empty")
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid {field_name}: {name!r}. Allowed format: letters/underscore, then letters, numbers, or underscore"
        )
    return name


def join_identifiers(*parts: str, field_name: str = "identifier") -> str:
    return ".".join(validate_identifier(part, field_name=field_name) for part in parts)
