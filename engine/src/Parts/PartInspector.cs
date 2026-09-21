using System;
using FactoryForge.TagBus;

namespace FactoryForge.Parts;

/// <summary>
/// The property inspector, as a part sees it (HP-34).
///
/// A part describes the rows it wants and never touches a <c>Control</c>: the
/// widths, the clipping and the scroll bound are the panel's problem, and they
/// are problems — an <c>OptionButton</c> takes the width of its longest menu
/// item, which once pushed the whole fixed-width panel off the right of the
/// screen.
///
/// Every control offered here must reach the simulation. A setting the part only
/// reads while building itself needs a rebuild inside the setter, or the slider
/// moves and nothing happens.
/// </summary>
public interface IPartInspector
{
    /// <summary>The selected part's id, for a row that has to name one of its
    /// own tags.</summary>
    string InstanceId { get; }

    void Slider(string label, float value, float min, float max, float step,
                Action<float> onChanged);

    /// <summary>A short free-text setting — a caption, a unit, a list of
    /// labels. There is no sensible slider for "g".</summary>
    void Text(string label, string value, int maxLength, Action<string> onChanged);

    /// <summary>
    /// A dropdown of the tags in the scene that a setting could legitimately
    /// point at, for a setting that wires this part to another machine.
    ///
    /// Never a free-text field: a tag id that matches nothing is a wire to
    /// nowhere, and every one of the things that read one of these — a count
    /// published through <c>TagTable.TrySet</c>, a channel a safety relay
    /// watches, a motor a starter powers — ignores an id nothing owns. A typo
    /// would be a silent no-op, which is the failure the picker exists to
    /// remove rather than to introduce.
    /// </summary>
    /// <param name="ownTagId">The part's own tag, offered first and meaning
    /// "myself". Empty when the setting has no such option, in which case the
    /// list opens with "(none)" — a wire that is not connected is a real and
    /// often correct answer.</param>
    /// <param name="type">Which tag type this setting can point at.</param>
    /// <param name="kind">Which direction. A starter powers an <em>output</em>
    /// (the machine's own command); a safety relay watches an <em>input</em>
    /// (a contact something else publishes). Offering the wrong half of the
    /// table is how a picker becomes a list to hunt through.</param>
    void TagPicker(string label, string current, string ownTagId, TagType type, TagKind kind,
                   Action<string> onChanged);
}
