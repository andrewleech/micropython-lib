"""Conformance suite for mpyschema: emitters, validator, and error types.

Self-contained: every spec here is a fixture defined in this file, so the
suite depends on nothing outside the package. It exercises the public
surface only -- `Str`/`Num`/`Int`/`Bool`/`Field`, `emit_schema`,
`emit_prompt_args`, `validate`, and the `SchemaError` hierarchy.

`mpyschema` has no imports, so unlike its two sibling packages it runs on
both runtimes, and this suite is the assertion of that. Run it directly
under either:

    micropython test_mpyschema.py
    python3 test_mpyschema.py

There is no `unittest` on the MicroPython target and no pytest either, so
the harness is a hand-rolled `check`/`check_raises` pair accumulating
failures and exiting non-zero. That is also why this is the one suite in
the family that is not pytest-based: it runs *under* the interpreter
rather than driving one as a subprocess.

`required` arrays are compared order-insensitively (sorted before
comparing). MicroPython dicts do not preserve insertion order, which is
why the spec API expresses declaration order via list position (see the
module docstring), but JSON Schema attaches no meaning to the order of
names inside `required` either -- so an order-insensitive compare is the
correct notion of "semantically identical" here, not a workaround.
"""

import os
import sys


def _up(path):
    """Directory containing `path`.

    `os.path` is a micropython-lib package rather than a built-in, so
    this suite does without it and runs on a bare interpreter.
    """
    if "/" in path:
        return path.rsplit("/", 1)[0]
    return ".." if path == "." else "."


def _import_root():
    """Directory to add to `sys.path` so `import mpyschema` resolves.

    Handles this file sitting either inside the package directory or
    beside it.
    """
    here = _up(__file__)
    try:
        os.stat(here + "/mpyschema")
    except OSError:
        return _up(here)
    return here


sys.path.insert(0, _import_root())

from mpyschema import (  # noqa: E402
    Bool,
    Field,
    Int,
    InvalidParameter,
    MissingParameter,
    Num,
    SchemaError,
    Str,
    emit_prompt_args,
    emit_schema,
    validate,
)

_failures = []


def check(label, got, want):
    if got != want:
        _failures.append("%s:\n  got:  %r\n  want: %r" % (label, got, want))


def check_schema(label, got, want):
    # See the module docstring on why `required` is compared sorted.
    check(label + " type", got["type"], want["type"])
    check(label + " properties", got["properties"], want["properties"])
    check(label + " required", sorted(got["required"]), sorted(want["required"]))


def check_raises(label, exc_type, fn, *args):
    try:
        fn(*args)
    except exc_type:
        return
    except Exception as exc:
        _failures.append("%s: expected %s, got %r" % (label, exc_type, exc))
        return
    _failures.append("%s: expected %s, nothing raised" % (label, exc_type))


# ── Fixture specs ───────────────────────────────────────────────────────

EMPTY = []

ONE_REQUIRED = [Str("name", desc="A name", required=True)]

TWO_REQUIRED_ONE_OPTIONAL = [
    Str("to", desc="Where it goes", required=True),
    Str("body", desc="What it says", required=True),
    Str("tag", desc="Optional label"),
]

ALL_OPTIONAL = [
    Str("filter", desc="A substring to match on"),
    Num("since", desc="How far back to look"),
    Num("limit", desc="How many to return"),
    Bool("verbose", desc="Whether to expand each entry"),
]


# ── emit_schema ─────────────────────────────────────────────────────────

check_schema(
    "emit_schema(EMPTY)",
    emit_schema(EMPTY),
    {"type": "object", "properties": {}, "required": []},
)

check_schema(
    "emit_schema(ONE_REQUIRED)",
    emit_schema(ONE_REQUIRED),
    {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "A name"}},
        "required": ["name"],
    },
)

check_schema(
    "emit_schema(TWO_REQUIRED_ONE_OPTIONAL)",
    emit_schema(TWO_REQUIRED_ONE_OPTIONAL),
    {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Where it goes"},
            "body": {"type": "string", "description": "What it says"},
            "tag": {"type": "string", "description": "Optional label"},
        },
        "required": ["to", "body"],
    },
)

check_schema(
    "emit_schema(ALL_OPTIONAL)",
    emit_schema(ALL_OPTIONAL),
    {
        "type": "object",
        "properties": {
            "filter": {"type": "string", "description": "A substring to match on"},
            "since": {"type": "number", "description": "How far back to look"},
            "limit": {"type": "number", "description": "How many to return"},
            "verbose": {
                "type": "boolean",
                "description": "Whether to expand each entry",
            },
        },
        "required": [],
    },
)

# Declaration order drives `required`'s order, which a list-shaped spec can
# guarantee and a dict-keyed one could not.
check(
    "emit_schema required follows declaration order",
    emit_schema(
        [
            Str("zebra", required=True),
            Str("apple", required=True),
            Str("mango", required=True),
        ]
    )["required"],
    ["zebra", "apple", "mango"],
)

# Each type's `type` keyword.
for field, want in (
    (Str("x"), "string"),
    (Num("x"), "number"),
    (Int("x"), "integer"),
    (Bool("x"), "boolean"),
):
    check(
        "emit_schema type for %s" % type(field).__name__,
        emit_schema([field])["properties"]["x"]["type"],
        want,
    )


# ── emit_prompt_args ────────────────────────────────────────────────────

check(
    "emit_prompt_args(ONE_REQUIRED)",
    emit_prompt_args(ONE_REQUIRED),
    [{"name": "name", "description": "A name", "required": True}],
)

check(
    "emit_prompt_args preserves declaration order",
    [a["name"] for a in emit_prompt_args(TWO_REQUIRED_ONE_OPTIONAL)],
    ["to", "body", "tag"],
)

# An absent `desc` omits `description` rather than emitting JSON null --
# `emit_prompt_args` and `emit_schema` agree on this.
check(
    "emit_prompt_args(no desc) omits description",
    emit_prompt_args([Str("bare", required=True)]),
    [{"name": "bare", "required": True}],
)

# The JSON type is unused here: MCP prompt arguments carry no type.
check(
    "emit_prompt_args ignores the field type",
    emit_prompt_args([Num("n", desc="a number")]),
    [{"name": "n", "description": "a number", "required": False}],
)


# ── validate: presence, absence, extra keys ─────────────────────────────

check("validate(EMPTY, {})", validate(EMPTY, {}), {})
check("validate(EMPTY, None)", validate(EMPTY, None), {})
check("validate(ALL_OPTIONAL, {}) stays empty", validate(ALL_OPTIONAL, {}), {})

check(
    "validate full round-trip",
    validate(TWO_REQUIRED_ONE_OPTIONAL, {"to": "a", "body": "b", "tag": "c"}),
    {"to": "a", "body": "b", "tag": "c"},
)

check(
    "validate without the optional field",
    validate(TWO_REQUIRED_ONE_OPTIONAL, {"to": "a", "body": "b"}),
    {"to": "a", "body": "b"},
)

# Extra keys are dropped, not rejected: `emit_schema` never sets
# `additionalProperties: false`, so they are schema-legal, and a validator
# stricter than the schema it emits would reject calls that schema
# advertises as valid.
check(
    "validate drops unrecognised keys",
    validate(ONE_REQUIRED, {"name": "x", "bogus": 1, "also_bogus": [2]}),
    {"name": "x"},
)
check(
    "validate drops unrecognised keys alongside several real ones",
    validate(
        TWO_REQUIRED_ONE_OPTIONAL,
        {"to": "a", "body": "b", "tag": "c", "extra": "ignored", "more": 123},
    ),
    {"to": "a", "body": "b", "tag": "c"},
)

# `arguments` is not mutated.
_incoming = {"name": "x", "bogus": 1}
validate(ONE_REQUIRED, _incoming)
check("validate does not mutate its input", _incoming, {"name": "x", "bogus": 1})


# ── validate: missing required fields ───────────────────────────────────

for label, spec, args in (
    ("nothing supplied", ONE_REQUIRED, {}),
    ("both missing", TWO_REQUIRED_ONE_OPTIONAL, {}),
    ("one of two missing", TWO_REQUIRED_ONE_OPTIONAL, {"to": "a"}),
    ("only the optional supplied", TWO_REQUIRED_ONE_OPTIONAL, {"tag": "c"}),
):
    check_raises(
        "validate missing required (%s)" % label,
        MissingParameter,
        validate,
        spec,
        args,
    )


# ── validate: coercion ──────────────────────────────────────────────────
#
# Deliberately lenient, because real MCP clients are: a client that
# stringifies every argument regardless of the declared schema type is
# served rather than rejected.

for label, sent, want in (
    ("number as-is", 30, 30),
    ("int string", "30", 30),
    ("float string", "100.5", 100.5),
    ("exponent string", "1e2", 100),
    ("negative int string", "-5", -5),
):
    check(
        "validate(Num, %s)" % label,
        validate(ALL_OPTIONAL, {"since": sent}),
        {"since": want},
    )

for label, sent, want in (
    ("string as-is", "abc", "abc"),
    ("int coerced", 123, "123"),
    ("float coerced", 3.14, "3.14"),
):
    check(
        "validate(Str, %s)" % label,
        validate(ONE_REQUIRED, {"name": sent}),
        {"name": want},
    )

# `Int` accepts the forms clients actually send, and rejects a real
# fraction rather than silently truncating it.
for label, sent, want in (
    ("int", 7, 7),
    ("integral float", 7.0, 7),
    ("int string", "7", 7),
    ("integral float string", "7.0", 7),
):
    check("validate(Int, %s)" % label, validate([Int("n")], {"n": sent}), {"n": want})

for label, sent in (
    ("fractional float", 7.5),
    ("fractional string", "7.5"),
    ("non-numeric", "abc"),
    ("bool", True),
):
    check_raises(
        "validate(Int, %s) rejected" % label,
        InvalidParameter,
        validate,
        [Int("n")],
        {"n": sent},
    )

# `Bool`, both directions, across every accepted form.
for sent, want in (
    (True, True),
    (False, False),
    ("true", True),
    ("TRUE", True),
    ("yes", True),
    ("1", True),
    ("false", False),
    ("False", False),
    ("no", False),
    ("0", False),
    (1, True),
    (0, False),
):
    check(
        "validate(Bool, %r)" % (sent,),
        validate([Bool("flag")], {"flag": sent}),
        {"flag": want},
    )
check_raises(
    "validate(Bool, 'maybe') rejected",
    InvalidParameter,
    validate,
    [Bool("flag")],
    {"flag": "maybe"},
)


# ── validate: non-coercible values ──────────────────────────────────────

for label, spec, args in (
    ("non-numeric string for Num", ALL_OPTIONAL, {"since": "abc"}),
    ("dict for Num", ALL_OPTIONAL, {"since": {}}),
    ("list for Num", ALL_OPTIONAL, {"limit": []}),
    ("dict for Str", ONE_REQUIRED, {"name": {}}),
    ("list for Str", ONE_REQUIRED, {"name": []}),
    ("None for Str", ONE_REQUIRED, {"name": None}),
):
    check_raises("validate rejects %s" % label, InvalidParameter, validate, spec, args)

# A bool is technically an int, so both Str and Num reject it explicitly
# rather than letting `isinstance(value, int)` quietly accept it.
for label, spec, args in (
    ("bool for Str", ONE_REQUIRED, {"name": True}),
    ("bool for Str (False)", ONE_REQUIRED, {"name": False}),
    ("bool for Num", ALL_OPTIONAL, {"since": True}),
    ("bool for Num (False)", ALL_OPTIONAL, {"limit": False}),
):
    check_raises("validate rejects %s" % label, InvalidParameter, validate, spec, args)

# The failing parameter is named in the message, which is the whole point
# of validate re-raising rather than letting `coerce`'s error through.
try:
    validate(ALL_OPTIONAL, {"since": "abc"})
except InvalidParameter as exc:
    check("InvalidParameter names the field", "since" in str(exc), True)


# ── Defaults ────────────────────────────────────────────────────────────

DEFAULTED = [Int("limit", desc="Max rows.", default=100)]
check(
    "emit_schema(default) emits the default",
    emit_schema(DEFAULTED)["properties"]["limit"],
    {"type": "integer", "description": "Max rows.", "default": 100},
)
check("validate(default) fills an omitted field", validate(DEFAULTED, {}), {"limit": 100})
check(
    "validate(default) does not override a supplied value",
    validate(DEFAULTED, {"limit": 5}),
    {"limit": 5},
)

# Falsy defaults are defaults, not absences -- the reason `default` needs a
# sentinel distinct from `None`.
for label, field, want in (
    ("zero", Int("n", default=0), {"n": 0}),
    ("false", Bool("flag", default=False), {"flag": False}),
    ("empty string", Str("s", default=""), {"s": ""}),
    ("null", Str("s", default=None), {"s": None}),
):
    check("validate(default=%s) filled" % label, validate([field], {}), want)

check(
    "emit_schema(default=None) emits JSON null",
    emit_schema([Str("s", default=None)])["properties"]["s"],
    {"type": "string", "default": None},
)

# No default declared: the key stays absent from both schema and result.
check(
    "emit_schema(no default) omits the key",
    emit_schema([Str("s")])["properties"]["s"],
    {"type": "string"},
)
check("validate(no default) omits the key", validate([Str("s")], {}), {})

# A default on a required field is unreachable by construction: the field
# must be supplied, so the default has nothing to fill.
check_raises(
    "validate(required with default) raises rather than filling",
    MissingParameter,
    validate,
    [Str("s", required=True, default="fallback")],
    {},
)


# ── schema= overlay ─────────────────────────────────────────────────────

check(
    "schema= adds keywords",
    emit_schema([Int("limit", desc="Max rows.", schema={"maximum": 1000})])["properties"]["limit"],
    {"type": "integer", "description": "Max rows.", "maximum": 1000},
)
check(
    "schema= overrides type",
    emit_schema([Str("tags", schema={"type": "array", "items": {"type": "string"}})])[
        "properties"
    ]["tags"],
    {"type": "array", "items": {"type": "string"}},
)
check(
    "schema= overrides description",
    emit_schema([Str("s", desc="generated", schema={"description": "explicit"})])["properties"][
        "s"
    ],
    {"type": "string", "description": "explicit"},
)
check(
    "schema= enum",
    emit_schema([Str("mode", schema={"enum": ["fast", "thorough"]})])["properties"]["mode"],
    {"type": "string", "enum": ["fast", "thorough"]},
)
check(
    "schema= overrides default",
    emit_schema([Int("n", default=1, schema={"default": 2})])["properties"]["n"],
    {"type": "integer", "default": 2},
)
# `schema=` is an emit-time overlay only: it does not make the validator
# enforce the keyword it adds. Pinned by a test rather than only stated in
# the docstring, since it is the obvious wrong assumption to make about it.
check(
    "schema= does not add validation",
    validate([Int("limit", schema={"maximum": 10})], {"limit": 9999}),
    {"limit": 9999},
)


# ── Exception hierarchy and the subclassing contract ────────────────────

check_raises(
    "MissingParameter is a SchemaError",
    SchemaError,
    validate,
    ONE_REQUIRED,
    {},
)
check_raises(
    "InvalidParameter is a SchemaError",
    SchemaError,
    validate,
    ALL_OPTIONAL,
    {"since": "abc"},
)


class _Upper(Field):
    """A third-party Field subclass: the documented extension point."""

    kind = "string"

    def coerce(self, value):
        if not isinstance(value, str):
            raise InvalidParameter("expected a string")
        return value.upper()


check(
    "a Field subclass drives emit_schema",
    emit_schema([_Upper("s", desc="shouty")])["properties"]["s"],
    {"type": "string", "description": "shouty"},
)
check(
    "a Field subclass drives validate",
    validate([_Upper("s")], {"s": "quiet"}),
    {"s": "QUIET"},
)
check_raises(
    "a Field subclass can reject",
    InvalidParameter,
    validate,
    [_Upper("s")],
    {"s": 1},
)


class _LegacyField(Str):
    """A subclass written against the pre-SchemaError contract."""

    def coerce(self, value):
        raise TypeError("legacy subclass contract")


check_raises(
    "TypeError from a subclass coerce becomes InvalidParameter",
    InvalidParameter,
    validate,
    [_LegacyField("s")],
    {"s": "x"},
)

# The base class is abstract: `coerce` must be implemented.
check_raises(
    "Field.coerce is not implemented",
    NotImplementedError,
    validate,
    [Field("s")],
    {"s": "x"},
)


if _failures:
    for f in _failures:
        print("FAIL:", f)
    print("\n%d failure(s)" % len(_failures))
    sys.exit(1)
else:
    print("mpyschema conformance: PASS")
