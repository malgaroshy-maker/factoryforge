using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// What a part is, from the editor's side of the fence (HP-34).
///
/// Before this existed, adding one part meant a new class <em>plus</em> edits to
/// eight shared integration points — a tag-registration switch, a node factory,
/// a suffix table, a per-tick dispatch switch, two halves of a settings
/// serialiser, an inspector branch and an operate-mode metadata dictionary. The
/// knowledge about one machine was spread across five files that nothing forced
/// to agree, and the drift that produces was measurable rather than theoretical:
/// the weighing conveyor's <c>.fault</c> tag was registered, appeared in the
/// inspector, could be forced, and stopped nothing, because registering a tag
/// and acting on it were edits to two different files and one was missed.
///
/// Now the editor <em>asks</em>. Everything below is per-part knowledge and all
/// of it lives on the part. Adding a part is one new file and one
/// <see cref="Editor.PartCatalog"/> entry, and
/// <c>--self-test=parts-are-not-special-cased</c> fails if that stops being
/// true.
///
/// Every member except <see cref="DeclareTags"/> has a default, so a part that
/// has no settings, no inspector rows and nothing to do each tick says so by
/// staying silent rather than by writing eight empty overrides.
/// </summary>
public interface IPart
{
    /// <summary>
    /// The tags this part exposes, as suffixes under its instance id.
    ///
    /// The instance id is a tag <em>prefix</em>, never a whole tag name, so a
    /// part declares <c>"rotate"</c> and the builder makes
    /// <c>&lt;id&gt;.rotate</c> out of it. This is also the single source of
    /// truth for which suffixes the per-tick dispatch caches and for whether a
    /// part has a drive that can be failed — both used to be separate tables
    /// that could disagree with this one.
    ///
    /// Must not depend on anything <c>_Ready</c> builds: the catalog probes a
    /// bare instance to answer "what I/O will this give my PLC" for the palette
    /// tooltip.
    /// </summary>
    void DeclareTags(PartTagBuilder tags);

    /// <summary>
    /// Settings worth saving — anything the part reads that a person chose.
    ///
    /// A value the part <em>computes</em> every tick is not a setting: saving it
    /// stores a sample and restores it as configuration.
    /// <c>VariableConveyor.Speed</c> is the case in point, and
    /// <c>--self-test=scene</c> asserts its absence.
    /// </summary>
    void CaptureSettings(PartSettings settings) { }

    /// <summary>
    /// Restore what <see cref="CaptureSettings"/> saved. Runs <em>before</em>
    /// the node enters the tree, because most parts build their geometry from
    /// these values in <c>_Ready</c> — and for the same reason a <c>_Ready</c>
    /// that assigns a default unconditionally will overwrite what was just
    /// restored.
    /// </summary>
    void ApplySettings(PartSettings settings) { }

    /// <summary>One physics tick: read the tags the controller writes, drive
    /// the machine, publish what the machine now reports.</summary>
    void StepPart(PartTick tick) { }

    /// <summary>The part's own rows in the property inspector. Every control
    /// offered here must actually reach the simulation — a setting the part
    /// only reads while building itself needs a rebuild in its setter, or the
    /// slider moves and nothing happens.</summary>
    void DescribeControls(IPartInspector ui) { }

    /// <summary>
    /// The part's tag prefix has changed. Anything the part holds that names
    /// one of its own tags has to follow it, or it points at a tag that no
    /// longer exists.
    /// </summary>
    void PrefixRenamed(string oldId, string newId) { }

    /// <summary>Put the machine back to its start state. A reset restarts the
    /// run; it does not undo the scene somebody built, so this clears what a
    /// <em>run</em> accumulated (a temperature, a count, a latched station) and
    /// nothing else.</summary>
    void ResetPart(PartReset reset) { }

    /// <summary>How a click in Operate mode reaches this part, or null if it is
    /// not operable by hand.</summary>
    PartOperation? Operation => null;

    /// <summary>For a <see cref="PartOperation.Precise"/> part: which of its own
    /// sub-regions the ray lands on, or null for a miss. A whole-body part
    /// leaves this alone and is tested against its bounding box instead.</summary>
    string? HitTestRegion(Vector3 from, Vector3 direction) => null;

    /// <summary>Apply a click. <see cref="PartOperate.Region"/> is whatever
    /// <see cref="HitTestRegion"/> returned, or the empty string for a
    /// whole-body part.</summary>
    void Operate(PartOperate op) { }
}

/// <summary>
/// How Operate mode reaches a part.
/// </summary>
/// <param name="Kind">A short noun for the "what is clickable in this scene"
/// banner — "conveyor", "guard door". Naming it here rather than in a table in
/// the editor is what stops a new operable part being one the banner never
/// mentions.</param>
/// <param name="PrimaryTag">The one tag a whole-body click drives, for the hover
/// outline and the hint. Null for a part with sub-regions of its own.</param>
/// <param name="Precise">True when the part hit-tests its own sub-regions
/// (<see cref="IPart.HitTestRegion"/>) rather than its bounding box. A box would
/// cover the whole housing and fire the nearest control wherever you clicked.
/// </param>
public sealed record PartOperation(string Kind, string? PrimaryTag = null, bool Precise = false);

/// <summary>
/// A part carrying a knob that is turned by dragging rather than pressed.
///
/// A role interface, not a type test: the editor owns the mouse and has to know
/// that <em>this</em> press begins a drag instead of a click, and it finds that
/// out by asking what the part can do rather than by naming which part it is.
/// </summary>
public interface IDialPart
{
    /// <summary>Is there a knob here worth telling the user about? A dial whose
    /// scale plate spans nothing cannot be turned and is not worth a cursor
    /// change.</summary>
    bool DialTurnable { get; }

    /// <summary>The tag the knob publishes. A drag takes it back from whoever
    /// forced it: the knob <em>is</em> the operator, and an operator who turns
    /// a knob that snaps back has been lied to.</summary>
    string DialTagSuffix { get; }

    /// <summary>Screen pixels of travel since the last motion event, positive
    /// upward.</summary>
    void TurnDial(float pixelsUp);
}
