using System;
using System.Collections.Generic;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>
/// One physics tick, as a part sees it (HP-34).
///
/// Tags are addressed by suffix, never by full id: the part knows it owns
/// <c>"rotate"</c> and this turns that into <c>&lt;id&gt;.rotate</c> from a
/// cache built once per part rather than a fresh string concatenation per tag
/// per part per tick (the ~4,500 allocations/sec FF-15 measured on a 30-part
/// scene).
///
/// Every read answers false/zero when the tag is missing, and every write is a
/// <c>TrySet</c>, because a part can be a <em>view</em> of tags something else
/// owns and will then find nothing under its own prefix at all.
/// </summary>
public sealed class PartTick
{
    private readonly TagTable _tags;
    private readonly IReadOnlyDictionary<string, string> _ids;

    public PartTick(TagTable tags, IReadOnlyDictionary<string, string> ids, string instanceId,
                    float dt, IPartHost host)
    {
        _tags = tags;
        _ids = ids;
        InstanceId = instanceId;
        Dt = dt;
        Host = host;
    }

    /// <summary>The part's own id — its tag prefix, and the name a person gave
    /// it.</summary>
    public string InstanceId { get; }

    /// <summary>Scaled simulation seconds, so a part that integrates obeys pause
    /// and the time-scale control like everything else.</summary>
    public float Dt { get; }

    /// <summary>The few things a part legitimately needs from the world outside
    /// itself.</summary>
    public IPartHost Host { get; }

    /// <summary>The raw table, for the rare part addressing a tag that is not
    /// under its own prefix.</summary>
    public TagTable Tags => _tags;

    public bool Has(string suffix) => _ids.ContainsKey(suffix);

    /// <summary>The full id behind a suffix, or null when this part has no such
    /// tag.</summary>
    public string? IdFor(string suffix) => _ids.TryGetValue(suffix, out var id) ? id : null;

    public bool TryRead(string suffix, out object value)
    {
        if (_ids.TryGetValue(suffix, out var id)) return _tags.TryGetVisible(id, out value);
        value = false;
        return false;
    }

    public bool TryBit(string suffix, out bool value)
    {
        if (TryRead(suffix, out var raw)) { value = Convert.ToBoolean(raw); return true; }
        value = false;
        return false;
    }

    public bool Bit(string suffix, bool fallback = false) =>
        TryBit(suffix, out var value) ? value : fallback;

    public int Whole(string suffix, int fallback = 0) =>
        TryRead(suffix, out var raw) ? Convert.ToInt32(raw) : fallback;

    public float Number(string suffix, float fallback = 0.0f) =>
        TryRead(suffix, out var raw) ? (float)Convert.ToDouble(raw) : fallback;

    public void Write(string suffix, bool value) => WriteRaw(suffix, value);

    public void Write(string suffix, int value) => WriteRaw(suffix, value);

    public void Write(string suffix, double value) => WriteRaw(suffix, value);

    /// <summary>Write a tag by its full id. Only for a setting that names a tag
    /// somewhere else — a remover counting into a shared total, for
    /// instance.</summary>
    public void WriteTo(string tagId, object value) => _tags.TrySet(tagId, value);

    private void WriteRaw(string suffix, object value)
    {
        if (_ids.TryGetValue(suffix, out var id)) _tags.TrySet(id, value);
    }
}

/// <summary>
/// The world outside a part, narrowed to what parts actually ask of it.
///
/// Deliberately small. Everything a part can answer for itself belongs on the
/// part; this is only for the handful of facts that are genuinely the scene's —
/// how many cartons are already live, and whether a deterministic scene is
/// mirroring this belt.
/// </summary>
public interface IPartHost
{
    /// <summary>Is there room under the live-carton cap for one more? An emitter
    /// that is capped must <em>not</em> consume its rising edge, so the same
    /// edge spawns the instant the count drops.</summary>
    bool CanSpawnItem { get; }

    /// <summary>Advance and return the tall/short alternation the feed emitters
    /// share, so a line with two emitters still gets a mixed stream rather than
    /// two independent ones that happen to agree.</summary>
    bool NextAlternate();

    /// <summary>A belt's current surface speed, offered every tick. The
    /// deterministic scene mirrors one named belt and transports its boxes in
    /// code, so changing that belt's speed in the inspector has to reach it —
    /// the host decides whether this id is that belt.</summary>
    void NoteTransportSpeed(string instanceId, float speed);
}
