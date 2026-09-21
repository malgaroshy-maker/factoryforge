using System;
using System.Collections.Generic;
using System.Globalization;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A part's saved settings, on the way out to a scene file and on the way back
/// in (HP-34).
///
/// Values travel as invariant-culture strings so numbers, bools, enums and tag
/// names all go through one map, and an unknown key is ignored rather than
/// breaking a scene saved by a newer build. Every reader returns null when the
/// key is absent, so a part restores what was saved and leaves its own default
/// alone otherwise.
/// </summary>
public sealed class PartSettings
{
    private readonly IDictionary<string, string> _values;
    private readonly HashSet<string> _external = new();

    public PartSettings() : this(new Dictionary<string, string>()) { }

    public PartSettings(IDictionary<string, string> values) => _values = values;

    public IDictionary<string, string> Values => _values;

    /// <summary>
    /// Keys naming a tag somewhere <em>else</em> in the scene rather than
    /// describing this machine. A copy must not carry them (HP-16): every other
    /// setting is a property of the machine and is exactly what a duplicate
    /// should inherit, but a tag id is a wire to a different part and copying
    /// the machine does not copy the wire's meaning.
    /// </summary>
    public IReadOnlyCollection<string> ExternalKeys => _external;

    // ---------- capture

    public void Put(string key, float value) =>
        _values[key] = value.ToString("R", CultureInfo.InvariantCulture);

    public void Put(string key, int value) =>
        _values[key] = value.ToString(CultureInfo.InvariantCulture);

    public void Put(string key, bool value) => _values[key] = value ? "true" : "false";

    public void Put(string key, string value) => _values[key] = value;

    public void PutEnum<T>(string key, T value) where T : struct, Enum =>
        _values[key] = value.ToString();

    /// <summary>A three-component value as <c>&lt;prefix&gt;_x/_y/_z</c>, which
    /// is the shape every vector setting already had in the file format.</summary>
    public void Put(string prefix, Vector3 value)
    {
        Put(prefix + "_x", value.X);
        Put(prefix + "_y", value.Y);
        Put(prefix + "_z", value.Z);
    }

    /// <summary>A setting that names a tag outside this part. See
    /// <see cref="ExternalKeys"/>.</summary>
    public void PutExternal(string key, string value)
    {
        _external.Add(key);
        _values[key] = value;
    }

    // ---------- apply

    public float? Number(string key) =>
        _values.TryGetValue(key, out var raw)
        && float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var v)
            ? v : null;

    public int? Whole(string key) => Number(key) is { } v ? (int)v : null;

    public bool? Flag(string key) => _values.TryGetValue(key, out var raw) ? raw == "true" : null;

    public string? Text(string key) => _values.TryGetValue(key, out var raw) ? raw : null;

    public T? Enumeration<T>(string key) where T : struct, Enum =>
        _values.TryGetValue(key, out var raw) && Enum.TryParse<T>(raw, out var parsed)
            ? parsed : null;

    /// <summary>All three components or none: a half-restored vector would be a
    /// size nobody chose.</summary>
    public Vector3? Vector(string prefix) =>
        Number(prefix + "_x") is { } x && Number(prefix + "_y") is { } y
                                       && Number(prefix + "_z") is { } z
            ? new Vector3(x, y, z) : null;
}
