using System;
using System.Text.Json.Nodes;

namespace FactoryForge.TagBus;

/// <summary>Data type of a tag's value.</summary>
public enum TagType { Bit, Int, Float }

/// <summary>
/// Direction, always from the <em>controller's</em> point of view.
/// Output: the PLC writes it, the simulator reads it (motor, valve, lamp).
/// Input: the simulator writes it, the PLC reads it (sensor, button, counter).
/// </summary>
public enum TagKind { Input, Output }

/// <summary>
/// One I/O point. Mirrors sidecar/factoryforge_sidecar/tags.py — the two must
/// stay in step, and tests/test_engine_parity.py checks that they do.
/// </summary>
public sealed class Tag
{
    /// <summary>Floats closer than this count as equal, so physics jitter in
    /// the low bits does not emit an update every tick.</summary>
    public const double FloatEpsilon = 1e-6;

    /// <summary>An <c>int</c> tag is a signed 32-bit integer, and that range is
    /// part of the protocol rather than an artefact of this implementation —
    /// Python would hold any integer you like, and a bus whose two engines
    /// disagree about what 2147483648 means is not a contract. Stated in
    /// docs/tag-bus.md, pinned by engine/fixtures/tag_cases.json. It is also
    /// what a PLC has: an S7 DInt is exactly this.</summary>
    public const int IntMin = int.MinValue;
    public const int IntMax = int.MaxValue;

    public string Id { get; }
    public string Name { get; }
    public TagType Type { get; }
    public TagKind Kind { get; }
    public object Value { get; private set; }

    public Tag(string id, string name, TagType type, TagKind kind, object? value = null)
    {
        Id = id;
        Name = name;
        Type = type;
        Kind = kind;
        Value = value is null ? DefaultFor(type) : Coerce(value);
    }

    private static object DefaultFor(TagType t) => t switch
    {
        TagType.Bit => false,
        TagType.Int => 0,
        _ => 0.0,
    };

    /// <summary>Convert to this tag's type, rejecting what cannot represent it.</summary>
    public object Coerce(object value) => Type switch
    {
        TagType.Bit => value switch
        {
            bool b => b,
            // Accept 0/1 but nothing else — a stray 2 is a bug, not a bit.
            int i when i is 0 or 1 => i == 1,
            long l when l is 0 or 1 => l == 1,
            _ => throw new ArgumentException($"{Id}: {value} is not a bit"),
        },
        TagType.Int => value switch
        {
            // bool is deliberately not accepted: silently turning a mis-typed
            // bit write into 0/1 would hide the mistake.
            int i => i,
            // ArgumentException, not the OverflowException a `checked` cast
            // throws. Everything that coerces a value catches ArgumentException
            // and carries on with the rest of the batch; an OverflowException
            // escaped that catch and took the whole message with it, which is
            // the same failure HP-18 closed on the Python side.
            long l when l >= IntMin && l <= IntMax => (int)l,
            long l => throw new ArgumentException(
                $"{Id}: {l} is outside the 32-bit range [{IntMin}, {IntMax}]"),
            _ => throw new ArgumentException($"{Id}: {value} is not an int"),
        },
        _ => value switch
        {
            float f => (double)f,
            double d => d,
            int i => (double)i,
            long l => (double)l,
            _ => throw new ArgumentException($"{Id}: {value} is not a float"),
        },
    };

    /// <summary>True if <paramref name="value"/> is meaningfully different.
    ///
    /// Coerces first, for every type. The float branch used to go straight to
    /// <c>Convert.ToDouble</c>, which happily turns <c>true</c> into 1.0 — so a
    /// bool written to a float tag holding 1.0 compared equal, was never
    /// coerced, and was accepted in silence by anything that only stores when
    /// this says so. Python rejected the same write. Mirrors
    /// sidecar/factoryforge_sidecar/tags.py.</summary>
    public bool Differs(object value)
    {
        var coerced = Coerce(value);
        if (Type == TagType.Float)
            return Math.Abs(Convert.ToDouble(Value) - (double)coerced) > FloatEpsilon;
        return !Equals(Value, coerced);
    }

    public void Set(object value) => Value = Coerce(value);

    public static string TypeName(TagType t) => t switch
    {
        TagType.Bit => "bit",
        TagType.Int => "int",
        _ => "float",
    };

    public static string KindName(TagKind k) => k == TagKind.Input ? "input" : "output";

    public JsonObject ToJson(object visibleValue, bool forced)
    {
        var o = new JsonObject
        {
            ["id"] = Id,
            ["name"] = Name,
            ["type"] = TypeName(Type),
            ["kind"] = KindName(Kind),
            ["value"] = JsonValue.Create(visibleValue),
        };
        if (forced) o["forced"] = true;
        return o;
    }
}
