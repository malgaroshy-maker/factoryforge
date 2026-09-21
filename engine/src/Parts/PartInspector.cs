using System;

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
    /// A dropdown of the int input tags the scene actually has, for a setting
    /// that wires this part to a tag somewhere else.
    ///
    /// Never a free-text field: the value is published through
    /// <c>TagTable.TrySet</c>, which ignores an id nothing owns, so a typo would
    /// be a silent no-op — a new one, in the panel built to remove them.
    /// </summary>
    /// <param name="ownTagId">The part's own tag, offered first and meaning
    /// "count into myself".</param>
    void TagPicker(string label, string current, string ownTagId, Action<string> onChanged);
}
