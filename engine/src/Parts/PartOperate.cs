using System;
using System.Collections.Generic;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>
/// A click in Operate mode, as the part it landed on sees it (HP-34).
///
/// Every write goes through <see cref="TagTable.Force"/>, the same call the Tag
/// Inspector and the property panel make, so a part operated by hand stays
/// sticky exactly as they do.
/// </summary>
public sealed class PartOperate
{
    /// <summary>The region a press on a turnable knob reports. The editor owns
    /// the mouse, so it has to know that <em>this</em> press begins a drag
    /// rather than a click.</summary>
    public const string DialRegion = "dial";

    private readonly TagTable _tags;
    private readonly IReadOnlyDictionary<string, string> _ids;
    private readonly Action<string> _pulse;

    /// <param name="pulse">Raise a tag and drop it again a moment later. The
    /// timer belongs to the scene tree, which a part has no business reaching
    /// into.</param>
    public PartOperate(TagTable tags, IReadOnlyDictionary<string, string> ids, string instanceId,
                       string region, Action<string> pulse)
    {
        _tags = tags;
        _ids = ids;
        InstanceId = instanceId;
        Region = region;
        _pulse = pulse;
    }

    public string InstanceId { get; }

    /// <summary>Whatever <see cref="IPart.HitTestRegion"/> returned, or the
    /// empty string for a whole-body part.</summary>
    public string Region { get; }

    public bool TryBit(string suffix, out bool value)
    {
        if (_ids.TryGetValue(suffix, out var id) && _tags.TryGetVisible(id, out var raw))
        {
            value = Convert.ToBoolean(raw);
            return true;
        }
        value = false;
        return false;
    }

    public void ToggleBit(string suffix)
    {
        if (!TryBit(suffix, out bool current)) return;
        Force(suffix, !current);
    }

    /// <summary>A click on an analog actuator means fully on or fully shut. The
    /// tag is a percent, not a bit, and "open the valve and watch the level
    /// rise" needs no finer control than that from a single click — the
    /// property panel already has a slider for the rest.</summary>
    public void ToggleAnalog(string suffix)
    {
        if (!_ids.TryGetValue(suffix, out var id)) return;
        if (!_tags.TryGetVisible(id, out var raw)) return;
        _tags.Force(id, Convert.ToDouble(raw) > 0.5 ? 0.0 : 100.0);
    }

    /// <summary>A rising edge, not a level. Holding an emitter's tag high spawns
    /// nothing new, so a click has to pulse and release rather than latch
    /// on.</summary>
    public void PulseBit(string suffix)
    {
        if (!_ids.TryGetValue(suffix, out var id)) return;
        _pulse(id);
    }

    public void Force(string suffix, object value)
    {
        if (_ids.TryGetValue(suffix, out var id)) _tags.Force(id, value);
    }
}

/// <summary>
/// A reset, as a part sees it: put the machine back where a run started.
/// </summary>
public sealed class PartReset
{
    private readonly TagTable? _tags;

    public PartReset(TagTable? tags, string instanceId)
    {
        _tags = tags;
        InstanceId = instanceId;
    }

    public string InstanceId { get; }

    /// <summary>Write one of the part's own tags, if the scene actually has it.
    /// A part that is a view of tags the simulation owns never registered its
    /// own, and there is nothing here to write.</summary>
    public void Write(string suffix, object value) => WriteTo($"{InstanceId}.{suffix}", value);

    public void WriteTo(string tagId, object value)
    {
        if (_tags is not null && _tags.Contains(tagId)) _tags.Set(tagId, value);
    }
}
