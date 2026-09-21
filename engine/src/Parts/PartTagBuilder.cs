using System.Collections.Generic;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>
/// What a part is handed when it is asked to declare its I/O (HP-34).
///
/// It does two jobs with one pass. Against a real <see cref="TagTable"/> it
/// creates the tags; against no table at all it only collects the suffixes,
/// which is how the catalog answers "what will this give my PLC" for a palette
/// tooltip without placing anything, and how the per-tick dispatch learns which
/// ids to cache. Those were three separate lists before, and nothing made them
/// agree.
/// </summary>
public sealed class PartTagBuilder
{
    private readonly TagTable? _tags;
    private readonly List<string> _suffixes = new();

    /// <param name="tags">Where to create the tags, or null to only collect
    /// suffixes.</param>
    /// <param name="instanceId">The part's id, which is a tag
    /// <em>prefix</em>.</param>
    /// <param name="index">Which one of its type this is, for the human-readable
    /// name ("Conveyor 3 (Rotate)").</param>
    public PartTagBuilder(TagTable? tags, string instanceId, int index)
    {
        _tags = tags;
        InstanceId = instanceId;
        Index = index;
    }

    public string InstanceId { get; }
    public int Index { get; }

    /// <summary>Every suffix declared, in declaration order.</summary>
    public IReadOnlyList<string> Suffixes => _suffixes;

    /// <param name="initial">The state the part powers up in. A normally-closed
    /// contact reads true when healthy, and a parked actuator reports the
    /// position its geometry is drawn in — a scene that opens disagreeing with
    /// itself is a scene somebody debugs for an hour.</param>
    public PartTagBuilder Bit(string suffix, string name, TagKind kind, bool initial = false) =>
        Add(suffix, name, TagType.Bit, kind, initial ? true : null);

    public PartTagBuilder Int(string suffix, string name, TagKind kind, int initial = 0) =>
        Add(suffix, name, TagType.Int, kind, initial != 0 ? initial : null);

    public PartTagBuilder Float(string suffix, string name, TagKind kind, double initial = 0.0) =>
        Add(suffix, name, TagType.Float, kind, initial != 0.0 ? (object)initial : null);

    private PartTagBuilder Add(string suffix, string name, TagType type, TagKind kind,
                               object? initial)
    {
        _suffixes.Add(suffix);
        if (_tags is null) return this;

        string id = $"{InstanceId}.{suffix}";
        _tags.Add(new Tag(id, name, type, kind));
        if (initial is not null) _tags.Set(id, initial);
        return this;
    }
}
