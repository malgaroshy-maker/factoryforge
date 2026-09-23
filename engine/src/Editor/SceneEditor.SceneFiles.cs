using System.Collections.Generic;
using FactoryForge.Scenes;
using Godot;

namespace FactoryForge.Editor;

public partial class SceneEditor
{
    /// <summary>
    /// Empty the world: every part gone, and the sorting line's engine-declared
    /// tags with them.
    ///
    /// <see cref="ClearAllPlacedParts"/> alone cannot do this. Those ten tags
    /// are declared at startup rather than owned by a part, so clearing the
    /// parts leaves them in the table and the next scene inherits a conveyor and
    /// two box counters it does not have. Left alone when a
    /// <see cref="SortingScene"/> is running, because that owns them.
    /// </summary>
    public void NewEmptyScene()
    {
        ClearAllPlacedParts();
        if (Scene is null && Tags is not null) SortingTags.Undeclare(Tags);

        SceneName = "untitled";
        IsDirty = false;
        NotifyTagsChanged();
        GD.Print("New empty scene");
    }

    /// <summary>Rebuild the sorting line the engine ships with.</summary>
    public void LoadDefaultSortingScene()
    {
        ClearAllPlacedParts();
        if (Scene is null && Tags is not null)
        {
            SortingTags.Undeclare(Tags);
            SortingTags.Declare(Tags);
        }

        SceneName = "sorting-by-height";
        RegisterDefaultSceneParts(physical: Scene is null);
        IsDirty = false;
        NotifyTagsChanged();
    }

    /// <summary>
    /// Start from a shipped template. Same as loading any scene file, except the
    /// sorting line's tags are dropped first so a template starts from a clean
    /// I/O list rather than inheriting the demo's.
    /// </summary>
    /// <param name="path">The template to open.</param>
    /// <remarks>The undeclare used to happen here, before the load was
    /// attempted — the same destroy-before-validating shape as HP-02, one level
    /// up. A template that could not be opened left the sorting demo's tags
    /// gone and the scene that was running with nothing declaring them.</remarks>
    public bool LoadTemplate(string path) => LoadSceneFromFile(path, dropSortingTags: true);

    /// <summary>
    /// Write the scene to disk. Returns false if it did not land.
    ///
    /// It used to return nothing and report success unconditionally: the open
    /// was <c>file?.StoreString(json)</c>, so a path that could not be opened at
    /// all took the null-conditional branch and fell straight through to
    /// <c>IsDirty = false</c> and "Saved scene to …". A read-only directory, a
    /// removed USB stick or a full disk all printed the same cheerful line, and
    /// the title bar stopped saying there was anything unsaved — which is the
    /// one signal a person has that their afternoon is still only in memory
    /// (HP-01).
    ///
    /// On failure <see cref="IsDirty"/> is left alone and
    /// <see cref="SaveFailed"/> is raised, because the console is not where
    /// somebody who just pressed Save is looking.
    /// </summary>
    public bool SaveSceneToFile(string path = "user://custom_scene.json")
    {
        // Name the scene after the file it lives in, so saving as "palletiser"
        // makes the bus report "palletiser" rather than every scene claiming to
        // be the sorting demo.
        string stem = System.IO.Path.GetFileNameWithoutExtension(path);
        if (stem.Length > 0 && stem != "custom_scene") SceneName = stem;

        var data = new SceneData { Name = SceneName, Parts = CapturePartsSnapshot() };
        string json = data.ToJson();

        // Write somewhere else first (HP-47). Opening the destination for
        // writing *truncates it*, so the old code destroyed the last good scene
        // file before it had written a single byte of the new one: a crash, a
        // full disk or a drive that goes away halfway through left a truncated
        // file where a working scene used to be, and that file was usually the
        // only copy. Nothing touches the destination until a complete, verified
        // file exists beside it.
        string partial = path + ".part";

        var file = Godot.FileAccess.Open(partial, Godot.FileAccess.ModeFlags.Write);
        if (file is null)
            return SaveDidNotLand(path, $"could not open {partial} for writing " +
                                        $"({Godot.FileAccess.GetOpenError()})");

        file.StoreString(json);
        // Before Close(), because Close() clears the file's error state — and
        // after StoreString, because that is the call that can fail on a full
        // disk or a drive that has gone away mid-write.
        var wrote = file.GetError();
        file.Close();

        if (wrote != Error.Ok)
        {
            Discard(partial);
            return SaveDidNotLand(path, $"the write failed ({wrote})");
        }

        // Read it back before believing it. A short write is what a disk that
        // filled up during the save looks like, and on some filesystems it does
        // not raise an error at all — the bytes simply are not there.
        using (var check = Godot.FileAccess.Open(partial, Godot.FileAccess.ModeFlags.Read))
        {
            if (check is null)
            {
                Discard(partial);
                return SaveDidNotLand(path, "the file it wrote could not be read back " +
                                            $"({Godot.FileAccess.GetOpenError()})");
            }

            string readBack = check.GetAsText();
            if (readBack != json)
            {
                Discard(partial);
                return SaveDidNotLand(path,
                    $"only {readBack.Length} of {json.Length} characters reached the disk");
            }
        }

        var moved = Godot.DirAccess.RenameAbsolute(partial, path);
        if (moved != Error.Ok)
        {
            // The destination is still whatever it was. Say so plainly: "could
            // not replace" and "could not write" are different problems and
            // send you to different places.
            Discard(partial);
            return SaveDidNotLand(path, $"the finished file could not be moved into place ({moved}); " +
                                        "the previous scene file is untouched");
        }

        IsDirty = false;
        GD.Print($"Saved scene to {path} ({_placedParts.Count} parts)");
        return true;
    }

    /// <summary>Drop a half-written file rather than leaving it beside the real
    /// one, where the next person to look at the directory has to guess which of
    /// the two is their scene.</summary>
    private static void Discard(string partial)
    {
        if (Godot.FileAccess.FileExists(partial)) Godot.DirAccess.RemoveAbsolute(partial);
    }

    /// <summary>Report a save that did not happen. <see cref="IsDirty"/> is
    /// deliberately untouched: the work is still unsaved and the title has to
    /// keep saying so.</summary>
    private bool SaveDidNotLand(string path, string why)
    {
        GD.PushError($"Save failed: {path} — {why}");
        GD.PrintErr($"Could not save scene to {path}: {why}");
        EmitSignal(SignalName.SaveFailed, path, why);
        return false;
    }

    /// <summary>
    /// Open a scene file. Returns false without touching the open scene if the
    /// file cannot be used.
    ///
    /// The order here is the whole of HP-02. It used to be: clear the scene,
    /// then open the file, then parse it — so every way a file could be bad cost
    /// the user the scene they already had, and they found out by watching their
    /// work disappear. Read, parse, validate, and only then clear.
    /// </summary>
    /// <param name="dropSortingTags">Undeclare the sorting demo's engine-owned
    /// tags first, so a template starts from a clean I/O list rather than
    /// inheriting the demo's. Done on the far side of validation, with the
    /// clear, because it is just as destructive.</param>
    public bool LoadSceneFromFile(string path = "user://custom_scene.json",
                                  bool dropSortingTags = false)
    {
        if (!Godot.FileAccess.FileExists(path))
        {
            GD.Print($"No saved scene file found at {path}");
            return LoadRefused(path, "there is no file there");
        }

        string json;
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Read))
        {
            if (file is null)
                return LoadRefused(path, $"it could not be opened ({Godot.FileAccess.GetOpenError()})");
            json = file.GetAsText();
        }

        if (!SceneData.TryParse(json, out var data, out string problem) || data is null)
            return LoadRefused(path, problem);

        // Part types this build does not have are dropped rather than refused,
        // and *said out loud* rather than dropped silently — which is what
        // happened before, because CreatePartNode returns null for an unknown
        // type and SpawnFromData quietly returns null in turn. A scene from a
        // newer build that added a part keeps its version number, so refusing
        // the file would make every such scene unopenable; but a machine
        // vanishing from somebody's line with no message is the same silent
        // loss this whole phase is about.
        var unknown = new List<string>();
        foreach (var part in data.Parts)
        {
            if (!PartCatalog.IsKnownType(part.Type) && !unknown.Contains(part.Type))
                unknown.Add(part.Type);
        }

        // Past this line the open scene is gone. Everything that could refuse
        // the file has already had its turn.
        if (dropSortingTags && Scene is null && Tags is not null) SortingTags.Undeclare(Tags);
        ClearAllPlacedParts();

        if (data.Name is { Length: > 0 }) SceneName = data.Name;

        // Before AddChild inside RestorePartsFromSnapshot: parts build their
        // geometry from position/rotation/properties in _Ready, so applying
        // them afterwards would leave the mesh showing the old configuration.
        RestorePartsFromSnapshot(data.Parts);

        IsDirty = false;
        GD.Print($"Loaded scene from {path} ({_placedParts.Count} parts)");

        if (unknown.Count > 0)
        {
            string types = string.Join(", ", unknown);
            GD.PrintErr($"Scene {path} contains part types this build does not have: {types}. " +
                        "They were left out. Saving over the file would lose them.");
            EmitSignal(SignalName.SceneLoadIncomplete, path, types);
        }

        // Deferred: the parts were added this frame and have not run _Ready, so
        // their geometry does not exist yet and anything measuring them now
        // would frame a set of empty boxes at their origins.
        CallDeferred(nameof(AnnounceSceneLoaded));
        return true;
    }

    /// <summary>Refuse a scene file, leaving the open scene exactly as it was.
    /// Reported through a signal as well as the console for the same reason a
    /// failed save is: the person is looking at the window.</summary>
    private bool LoadRefused(string path, string why)
    {
        GD.PrintErr($"Could not open scene {path}: {why}");
        EmitSignal(SignalName.LoadFailed, path, why);
        return false;
    }

    private void AnnounceSceneLoaded() => EmitSignal(SignalName.SceneLoaded);

    public void ClearAllPlacedParts()
    {
        ClearPlacedPartsCore();
        _history.Clear();   // its commands refer to parts that are now gone
        GD.Print("Cleared all editor placed parts");
    }

    /// <summary>The wipe itself, with no opinion on history. Shared by the
    /// scene-loading paths (which drop history entirely — it refers to parts
    /// this just freed) and <see cref="ClearSceneCommand"/> (which needs the
    /// wipe to be redoable without also being the thing that erases itself).
    /// </summary>
    private void ClearPlacedPartsCore()
    {
        ClearHoverHighlight();
        foreach (var part in _placedParts)
        {
            if (part.OwnsTags && Tags is not null)
                PartTagManager.UnregisterPartTags(part.InstanceId, Tags);
            part.Node.QueueFree();
        }
        _placedParts.Clear();
        PartTagManager.ResetCounters();
        _movingPart = null;
        DeselectPart();
        NotifyTagsChanged();
    }

    /// <summary>Everything needed to rebuild the current parts exactly as they
    /// stand — the same shape <see cref="SaveSceneToFile"/> writes to disk,
    /// kept in memory instead so Clear can be undone without a file.</summary>
    private List<PartInstanceData> CapturePartsSnapshot()
    {
        var list = new List<PartInstanceData>();
        foreach (var part in _placedParts)
        {
            list.Add(new PartInstanceData
            {
                Id = part.InstanceId,
                Type = part.PartType,
                Position = new float[] { part.Node.Position.X, part.Node.Position.Y, part.Node.Position.Z },
                Rotation = new float[] { part.Node.Rotation.X, part.Node.Rotation.Y, part.Node.Rotation.Z },
                Properties = PartProperties.Capture(part.Node),
            });
        }
        return list;
    }

    /// <summary>Rebuild parts from a snapshot, preserving their ids so wiring
    /// and mappings survive. Shared by scene loading and Clear's undo.</summary>
    private void RestorePartsFromSnapshot(List<PartInstanceData> parts)
    {
        foreach (var p in parts) SpawnFromData(p, notify: false);
        NotifyTagsChanged();
    }

    /// <summary>
    /// Build, parent and register one part from captured data — position,
    /// rotation, type and every property PartProperties knows how to apply.
    /// The single-part building block <see cref="RestorePartsFromSnapshot"/>
    /// and <see cref="DuplicateSelectedPart"/> both reduce to: the whole
    /// difference between "rebuild ten parts" and "paste one part with its
    /// settings intact" is how many <see cref="PartInstanceData"/> you hand it.
    /// </summary>
    /// <param name="reviveKey">The part key to restore, when this respawn is an
    /// undo of a delete rather than a new part. Zero mints a fresh one. See
    /// <see cref="_nextPartKey"/>.</param>
    private PlacedPart? SpawnFromData(PartInstanceData p, bool notify = true, long reviveKey = 0)
    {
        var node = CreatePartNode(p.Type);
        if (node is null) return null;

        node.Position = new Vector3(p.Position[0], p.Position[1], p.Position[2]);
        node.Rotation = new Vector3(p.Rotation[0], p.Rotation[1], p.Rotation[2]);
        PartProperties.Apply(node, p.Properties);
        GetParent()?.AddChild(node);

        var (instanceId, owns, tagIndex) = PartTagManager.RegisterPartTags(node, p.Type, Tags, p.Id);
        var placed = new PlacedPart(node, instanceId, p.Type, owns,
                                    reviveKey != 0 ? reviveKey : NextPartKey(), tagIndex);
        _placedParts.Add(placed);
        // The name goes on here rather than in a pass afterwards, so a scene
        // loaded with names already on comes up labelled instead of needing
        // the toggle flicked to catch up (NV-01).
        PartNameLabel.Apply(node, instanceId, PartNamesVisible);
        if (notify) NotifyTagsChanged();
        return placed;
    }
}
