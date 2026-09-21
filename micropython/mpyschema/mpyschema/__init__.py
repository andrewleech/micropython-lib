"""mpyschema: explicit parameter specs, turned into MCP inputSchema fragments.

Emits `{"type":"object","properties":{...},"required":[...]}` JSON-Schema
fragments (and MCP prompt `arguments` lists) from explicit spec objects, and
validates/coerces incoming tool arguments against those same specs.

Why explicit specs instead of reading type hints off the tool function:
MicroPython retains neither annotations nor parameter-name information on
function objects at runtime. `f.__annotations__` raises `AttributeError` on
the target binary, and there is no `inspect` module to fall back on. A
schema layer built on introspection therefore cannot exist on this runtime;
an explicit spec, written once per tool and consumed by both the emitter and
the validator, is the only representation that works unconditionally. (A
build-time CPython codegen step that derives these spec literals from a
type-hinted source of truth is an orthogonal, additive concern — out of
scope here — and would target this same spec API as its output.)

Runtime support: pure Python with no imports, so this module runs
unmodified on MicroPython and CPython alike (unlike `mpyjsonrpc` and
`mpyfastmcp`, which are MicroPython-only by construction).

Spec shape: a tool's parameters are a plain **list** of named `Field`
instances, in declaration order:

    SPEC = [
        Str("path", desc="File to read", required=True),
        Int("max_bytes", desc="Truncate after this many bytes", default=4096),
        Bool("follow_symlinks", desc="Resolve symlinks before reading"),
    ]

    input_schema = emit_schema(SPEC)
    arguments = validate(SPEC, incoming_arguments)

Anything JSON Schema can express beyond `type`/`description`/`default`
goes through the `schema=` escape hatch, whose keys are merged last and
therefore win:

    Int("limit", desc="Max rows.", default=100, schema={"maximum": 1000})
    Str("mode", schema={"enum": ["fast", "thorough"]})
    Str("tags", schema={"type": "array", "items": {"type": "string"}})

A list, not a dict keyed by parameter name, because plain dicts on this
runtime do not preserve insertion order (iteration order is hash-bucket
order, e.g. `{"zebra":1,"apple":2,"mango":3}` iterates as `zebra, mango,
apple`) — a dict-keyed spec could not guarantee the `required` array or a
prompt's `arguments` list come out in declaration order. A list always
does.

The same shape, read for `desc`/`required` only (the JSON type is
irrelevant to a prompt argument), backs MCP prompt argument lists:

    GREET_SPEC = [Str("name", desc="Who to greet", required=True)]
    prompt_arguments = emit_prompt_args(GREET_SPEC)

Errors: `validate()` raises `MissingParameter` or `InvalidParameter`, both
under a common `SchemaError` root, so a caller can tell a bad client
argument from an unrelated failure in the same block. These are plain
`Exception` subclasses, not `ValueError`/`TypeError`: MicroPython rejects
`class MissingParameter(SchemaError, ValueError)` with "multiple bases have
instance lay-out conflict", so keeping the builtins as a second base is not
available on the target runtime.
"""

__version__ = "0.1.0"


class SchemaError(Exception):
    """Root of every exception this module raises."""


class MissingParameter(SchemaError):
    """A `required=True` field was absent from the incoming arguments."""


class InvalidParameter(SchemaError):
    """A present value could not be coerced to its field's declared type."""


class _Unset:
    """Sentinel for "no default declared", distinct from a declared
    `default=None`. A plain `None` default cannot serve as its own sentinel
    -- that is what made an explicit JSON-null default unrepresentable."""


_UNSET = _Unset()


class Field:
    """Base parameter spec: a name, a JSON-Schema type, description,
    requiredness, an optional default, and an optional raw-schema overlay.

    Subclassing is supported and is the intended way to add a parameter
    type: set `kind` to the JSON-Schema `type` string and implement
    `coerce()` to accept the loosely-typed values MCP clients actually send
    (see `validate()`). `coerce()` returns the coerced value or raises
    `InvalidParameter`; a `TypeError` is also accepted, and translated, so a
    subclass written against the pre-`SchemaError` contract keeps working.
    Prefer `schema=` over a new subclass for anything that is a keyword
    rather than a type -- `enum`, `minimum`, `items` and friends need no
    class at all.

    `schema` is a dict merged over the emitted property, last, so its keys
    win over `type`, `description` and `default`. That ordering is what
    makes it a real escape hatch: a spec that needs to emit
    `{"type": "integer"}` from a `Num`, or replace a generated description,
    can do so without the emitter growing a special case.

    `default` is filled into the validated arguments when the client omits
    the field, and emitted into the schema so the model can see it.
    Declaring `default=None` is meaningful and emits `"default": null`;
    omitting `default` entirely leaves the key absent from both.
    """

    kind = None

    def __init__(self, name, desc=None, required=False, default=_UNSET, schema=None):
        self.name = name
        self.desc = desc
        self.required = required
        self.default = default
        self.schema = schema

    def has_default(self):
        """True if this field declares a default (including `None`)."""
        return not isinstance(self.default, _Unset)

    def coerce(self, value):
        raise NotImplementedError


class Str(Field):
    """A JSON-Schema `"type": "string"` parameter."""

    kind = "string"

    def coerce(self, value):
        if type(value) is bool:
            raise InvalidParameter("expected a string, got a bool")
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        raise InvalidParameter("expected a string")


class Num(Field):
    """A JSON-Schema `"type": "number"` parameter.

    Accepts a JSON number as-is. Also accepts a numeric string (some MCP
    clients send every argument as a string regardless of the declared
    schema type) and parses it as int or float depending on its literal
    form.
    """

    kind = "number"

    def coerce(self, value):
        if type(value) is bool:
            raise InvalidParameter("expected a number, got a bool")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                if "." in value or "e" in value or "E" in value:
                    return float(value)
                return int(value)
            except ValueError:
                raise InvalidParameter("expected a number, got %r" % (value,))
        raise InvalidParameter("expected a number")


class Int(Field):
    """A JSON-Schema `"type": "integer"` parameter.

    Distinct from `Num`, which emits `"number"`: a parameter that is
    conceptually an integer should say so on the wire rather than leave the
    constraint to prose in its description.

    Accepts a JSON integer as-is, a float with no fractional part (some
    clients render every number as a float), and the string forms of
    either. A value with a real fractional part is rejected rather than
    silently truncated -- dropping the fraction would answer a different
    question than the caller asked.
    """

    kind = "integer"

    def coerce(self, value):
        if type(value) is bool:
            raise InvalidParameter("expected an integer, got a bool")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value != int(value):
                raise InvalidParameter("expected an integer, got %r" % (value,))
            return int(value)
        if isinstance(value, str):
            try:
                parsed = (
                    float(value) if ("." in value or "e" in value or "E" in value) else int(value)
                )
            except ValueError:
                raise InvalidParameter("expected an integer, got %r" % (value,))
            if isinstance(parsed, float) and parsed != int(parsed):
                raise InvalidParameter("expected an integer, got %r" % (value,))
            return int(parsed)
        raise InvalidParameter("expected an integer")


class Bool(Field):
    """A JSON-Schema `"type": "boolean"` parameter.

    Accepts a JSON boolean as-is, plus the string/int forms a lenient
    client may send instead ("true"/"false", "1"/"0", 1/0).
    """

    kind = "boolean"

    def coerce(self, value):
        if type(value) is bool:
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in ("true", "1", "yes"):
                return True
            if lowered in ("false", "0", "no"):
                return False
            raise InvalidParameter("expected a boolean, got %r" % (value,))
        if isinstance(value, int):
            return bool(value)
        raise InvalidParameter("expected a boolean")


def emit_schema(spec):
    """spec (list of named Field, declaration order) -> MCP `inputSchema`.

    Produces `{"type": "object", "properties": {...}, "required": [...]}`;
    `required` lists only the names of fields with `required=True`, in
    spec-declaration order. An empty spec yields `properties: {}` and
    `required: []` (the shape used by zero-argument tools).

    Each property carries `type`, then `description` and `default` when the
    field declares them, then any `schema=` overlay merged last so its keys
    win. `description` is omitted entirely rather than emitted as `null`
    when absent; `default` is emitted whenever declared, `None` included,
    because a default the client cannot see is worse than no default at all.
    """
    properties = {}
    required = []
    for field in spec:
        prop = {"type": field.kind}
        if field.desc is not None:
            prop["description"] = field.desc
        if field.has_default():
            prop["default"] = field.default
        if field.schema:
            prop.update(field.schema)
        properties[field.name] = prop
        if field.required:
            required.append(field.name)
    return {"type": "object", "properties": properties, "required": required}


def emit_prompt_args(spec):
    """spec (list of named Field, declaration order) -> MCP prompt
    `arguments` list.

    Produces a list of `{"name", "required"}` dicts carrying `description`
    only when the field declares one, in declaration order. The JSON type
    on each `Field` is unused here — MCP prompt arguments carry no type,
    only a description and whether they are required.
    """
    arguments = []
    for field in spec:
        arg = {"name": field.name, "required": bool(field.required)}
        if field.desc is not None:
            arg["description"] = field.desc
        arguments.append(arg)
    return arguments


def validate(spec, arguments):
    """Check and coerce incoming tool `arguments` against `spec`.

    Raises `MissingParameter` for an absent required field and
    `InvalidParameter` if a present value cannot be coerced to its field's
    declared type; both are `SchemaError` subclasses, so a caller that only
    needs "the client sent something bad" can catch the root. Returns a new
    dict containing only the names declared in `spec`, with declared
    defaults filled in for omitted fields; `arguments` is not mutated.

    Keys in `arguments` that are not named in `spec` are silently dropped,
    not rejected. This matches the emitted schema, which never sets
    `additionalProperties: false`, so extra keys are schema-legal; a
    validator stricter than the schema it emits would reject calls that
    schema advertises as valid. It also matches how MCP clients behave in
    practice, attaching their own fields to an arguments object.

    Coercion is deliberately lenient about the declared JSON type, because
    real MCP clients are: a `Num`/`Int` field accepts either a JSON number
    or its string form, and a `Bool` field accepts either a JSON boolean or
    its common string/int forms. A client that stringifies every argument
    is served rather than rejected.
    """
    if arguments is None:
        arguments = {}
    result = {}
    for field in spec:
        if field.name in arguments:
            try:
                result[field.name] = field.coerce(arguments[field.name])
            except (InvalidParameter, TypeError) as exc:
                # `TypeError` is accepted alongside `InvalidParameter` so a
                # `Field` subclass written against the pre-`SchemaError`
                # contract still reports a bad argument as one. Either way
                # the caller gets an `InvalidParameter` naming the field.
                raise InvalidParameter("parameter %r: %s" % (field.name, exc))
        elif field.required:
            raise MissingParameter("missing required parameter: %s" % field.name)
        elif field.has_default():
            result[field.name] = field.default
    return result
