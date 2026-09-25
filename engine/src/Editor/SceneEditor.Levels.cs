using System.Collections.Generic;
using FactoryForge.Parts;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>
    /// The work plane new parts land on (IP-15): 0 for the floor, n for
    /// <see cref="PartLayout.LevelHeight"/> x n above it.
    ///
    /// One number the editor holds, and three things follow it: the ghost of a
    /// part in hand, the plane the cursor is projected onto, and the grid,
    /// which rises to that level's floor so what you are placing on is on
    /// screen rather than only in a label. PgUp / PgDn and the palette's level
    /// buttons change it; nothing about a part's own type is involved, so a
    /// level is available to every part the catalog has.
    /// </summary>
    public int ActiveLevel { get; private set; }

    /// <summary>The active level changed. The palette shows it.</summary>
    [Signal] public delegate void ActiveLevelChangedEventHandler(int level);

    /// <summary>Choose the level to place on. Clamped to the levels there
    /// are; a part in hand goes with it.</summary>
    public void SetActiveLevel(int level)
    {
        level = Mathf.Clamp(level, 0, PartLayout.MaxLevel);
        if (_previewNode is not null)
        {
            var at = _previewNode.Position;
            _previewNode.Position = new Vector3(at.X, PartLayout.PlaneY(level), at.Z);
        }

        if (level == ActiveLevel) return;
        ActiveLevel = level;
        Grid?.ShowFloorAt(PartLayout.FloorY(level));
        EmitSignal(SignalName.ActiveLevelChanged, level);
        GD.Print($"Placing on level {level} ({PartLayout.FloorY(level):0.00} m above the floor)");
    }

    /// <summary>
    /// What PgUp / PgDn and the palette's level buttons do.
    ///
    /// With a selection and nothing in hand, the selection goes up or down a
    /// level, as one undoable move -- build a section on the floor where it is
    /// easy to see, then lift it. Otherwise the active level changes, and a
    /// part in hand (a placement, or an M-move) goes with it. Refused, with a
    /// reason, rather than half-done if any selected part would leave the
    /// levels there are.
    /// </summary>
    public void StepLevel(int delta)
    {
        if (Mode != EditorMode.Edit || delta == 0) return;

        if (_previewNode is not null || _selection.Count == 0)
        {
            int wanted = ActiveLevel + delta;
            if (wanted < 0 || wanted > PartLayout.MaxLevel)
            {
                IdleHint?.Announce($"Levels run 0 to {PartLayout.MaxLevel}; already on level {ActiveLevel}.");
                return;
            }
            SetActiveLevel(wanted);
            IdleHint?.Announce(wanted == 0
                ? "Placing on the floor (level 0)."
                : $"Placing on level {wanted}, {PartLayout.FloorY(wanted):0.0} m up. Raised parts "
                  + "stand on whatever deck you give them; PgUp/PgDn change level.");
            return;
        }

        LiftSelection(delta);
    }

    private void LiftSelection(int delta)
    {
        float rise = delta * PartLayout.LevelHeight;
        foreach (var entry in _selection)
        {
            int to = PartLayout.LevelOf(entry.Node.Position.Y) + delta;
            if (to < 0 || to > PartLayout.MaxLevel)
            {
                IdleHint?.Announce($"'{entry.InstanceId}' cannot go {(delta > 0 ? "up" : "down")} a "
                                   + $"level: levels run 0 to {PartLayout.MaxLevel}.");
                GD.Print($"Level change refused: '{entry.InstanceId}' would leave levels 0..{PartLayout.MaxLevel}");
                return;
            }
        }

        var moves = new List<IEditorCommand>(_selection.Count);
        foreach (var entry in _selection)
        {
            Vector3 from = entry.Node.Position;
            moves.Add(new MoveCommand(this, entry.Key, from, from + new Vector3(0, rise, 0)));
        }

        _history.ExecuteCommand(CompositeCommand.Of(moves));
        MarkDirty();

        if (_selectedPart is { } primary)
            SetActiveLevel(PartLayout.LevelOf(primary.Node.Position.Y));
        GD.Print(moves.Count == 1
            ? $"Moved '{_selection[0].InstanceId}' to level {ActiveLevel} (Ctrl+Z to put it back)"
            : $"Moved {moves.Count} parts {(delta > 0 ? "up" : "down")} a level (Ctrl+Z to put them back)");
    }

    /// <summary>The level a placed part stands on, for tests and tooling.</summary>
    public int? LevelOf(string instanceId) =>
        PositionOf(instanceId) is { } at ? PartLayout.LevelOf(at.Y) : null;
}
