using System.Collections.Generic;
using FactoryForge.Parts;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Captures and restores the settings that make a placed part *this* part
/// rather than a default one.
///
/// A scene file used to store only position and rotation, so saving and
/// reloading silently reset every tuned value — belt speeds, sensor ranges,
/// tank rates. Two of those resets were not merely annoying: a remover lost the
/// tag it counts into, and a sensor lost the flag saying the simulation owns its
/// tag, so a reloaded deterministic scene would have had its sensors fighting
/// the scene for the same tag.
///
/// Since HP-34 this file holds no knowledge of any part. It used to carry
/// forty-eight per-type branches across a capture switch and an apply switch
/// that had to stay each other's mirror image, in a file six hundred lines from
/// the classes they described. Both halves now live on the part, next to the
/// field they are saving, where forgetting one is a change you can see.
/// </summary>
public static class PartProperties
{
    /// <summary>
    /// <see cref="Capture"/> for a copy, paste or duplicate rather than for a
    /// save: the same settings, minus anything naming a tag outside the part
    /// (HP-16).
    ///
    /// Dropping the key rather than rewriting it is what makes this correct in
    /// both directions. An empty count-tag setting already means "my own
    /// {id}.count" everywhere it is read, so a remover that pointed at its own
    /// tag gets the copy's own tag, and one that pointed at a shared counter
    /// gets its own instead of fighting over somebody else's.
    ///
    /// Which keys those are is the part's own answer — <c>PartSettings.PutExternal</c>
    /// — rather than a list here that a new part would have to be added to by
    /// somebody who knew the list existed.
    /// </summary>
    public static Dictionary<string, string> CaptureForCopy(Node3D node)
    {
        var settings = Collect(node);
        foreach (string key in settings.ExternalKeys) settings.Values.Remove(key);
        return new Dictionary<string, string>(settings.Values);
    }

    public static Dictionary<string, string> Capture(Node3D node) =>
        new(Collect(node).Values);

    /// <summary>
    /// Apply saved settings. Must run <em>before</em> the node enters the tree:
    /// most parts build their geometry from these values in <c>_Ready</c>, so
    /// setting them afterwards would leave the mesh describing the old
    /// configuration — and a <c>_Ready</c> that assigns a default
    /// unconditionally would overwrite what was just restored.
    /// </summary>
    public static void Apply(Node3D node, IDictionary<string, string>? props)
    {
        if (props is null || props.Count == 0) return;
        if (node is IPart part) part.ApplySettings(new PartSettings(props));
    }

    private static PartSettings Collect(Node3D node)
    {
        var settings = new PartSettings();
        if (node is IPart part) part.CaptureSettings(settings);
        return settings;
    }
}
