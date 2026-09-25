namespace FactoryForge.Parts;

/// <summary>
/// The mounting convention every part shares.
///
/// A part's local origin sits on the **work plane** — the same plane the scene
/// editor snaps placements to (see <c>SceneEditor.UpdatePreviewPosition</c>).
/// Each part offsets its own geometry from there, so dropping any part on a grid
/// point puts it at the right height with no per-part fudging, and a scene file
/// only ever stores grid-aligned coordinates.
///
/// X and Z are snapped to <see cref="View.VoxelGrid.CellSize"/>; Y is always a
/// work plane. That is why every part in the default scene sits at y = 0.5 --
/// level 0's plane. Raised levels stack above it (<see cref="LevelHeight"/>).
/// </summary>
public static class PartLayout
{
    /// <summary>Height of the conveyor work plane above the floor.</summary>
    public const float WorkPlaneY = 0.5f;

    /// <summary>Conveyor frame thickness; the belt's origin is its centre line.</summary>
    public const float BeltThickness = 0.12f;

    /// <summary>Carrying surface, relative to a part's origin on the work plane.</summary>
    public const float BeltSurface = BeltThickness / 2.0f;

    /// <summary>Distance from the work plane down to the floor, for support legs.
    /// On a raised level (see <see cref="LevelHeight"/>) the floor a part's legs
    /// reach is that level's floor -- a mezzanine deck -- so this is the same
    /// number on every level and no part needs to know which one it is on.</summary>
    public const float FloorDrop = WorkPlaneY;

    // --- Levels (IP-15). A part sits on one of several stacked work planes. A
    // part on level n has its origin on WorkPlaneY + n * LevelHeight, so "the
    // origin is on the work plane" still holds; it is simply that level's work
    // plane. Level n's floor is n * LevelHeight, exactly FloorDrop below it,
    // which is why every part's legs already land on a mezzanine deck without
    // any part learning about levels.

    /// <summary>
    /// Rise from one level to the next, metres.
    ///
    /// The lift's pitch, on purpose: <see cref="VerticalLift"/> takes its
    /// default level spacing from here, so a lift standing on level 0 puts its
    /// level-1 carriage deck exactly flush with a belt placed on level 1. Any
    /// other number would make the first thing anybody builds with levels --
    /// a lift between two belts -- a step a carton cannot cross. 0.9 m is also
    /// a plausible pitch between a floor conveyor and one on a low mezzanine.
    /// </summary>
    public const float LevelHeight = 0.90f;

    /// <summary>The highest level the editor will place on. Level 3 puts a
    /// work plane at 3.2 m, already above everything the default camera
    /// frames; the cap is what turns a typo in a hand-edited scene into a
    /// refusal rather than a belt nine metres up.</summary>
    public const int MaxLevel = 3;

    /// <summary>The height of level <paramref name="level"/>'s work plane.</summary>
    public static float PlaneY(int level) => WorkPlaneY + level * LevelHeight;

    /// <summary>The height of level <paramref name="level"/>'s floor: the top of
    /// the deck the parts on that level stand on.</summary>
    public static float FloorY(int level) => level * LevelHeight;

    /// <summary>
    /// The level a part at height <paramref name="y"/> belongs to: the nearest
    /// work plane, clamped to the levels there are.
    ///
    /// Nearest rather than "at or below", because some parts are deliberately
    /// placed off their plane -- a remover's zone is often centred 0.2-0.35 m
    /// below it -- and still belong to it. Anything within half a level of a
    /// plane is on that plane.
    /// </summary>
    public static int LevelOf(float y) =>
        Godot.Mathf.Clamp(Godot.Mathf.RoundToInt((y - WorkPlaneY) / LevelHeight), 0, MaxLevel);

    // --- Reference dimensions, sourced from real equipment, so new parts get
    // built to the same scale system instead of each guessing independently.
    // See FF-25 -- a control panel modelled to no shared reference read as
    // roughly as tall as the transport line it stood next to.

    /// <summary>Conveyor carrying-surface width. <see cref="Parts.ConveyorBelt"/>'s
    /// default <c>Size</c> already matches this; new transport parts should too.</summary>
    public const float StandardBeltWidth = 0.5f;

    /// <summary>A waist-height pushbutton station's housing footprint --
    /// enough for a row of 22 mm caps, no wider.</summary>
    public const float PanelWidth = 0.40f;
    public const float PanelHeight = 0.60f;

    /// <summary>Stack light lamp dome diameter, matching a standard 70 mm
    /// beacon once its curvature reads in the render.</summary>
    public const float StackLightDiameter = 0.08f;
}
