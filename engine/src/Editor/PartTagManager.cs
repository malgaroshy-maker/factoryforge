using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Editor;

/// <summary>
/// Dynamically registers and manages tags for newly placed factory components in the TagBus.
/// </summary>
public static class PartTagManager
{
    private static readonly Dictionary<string, int> TypeCounters = new();

    public static void ResetCounters()
    {
        TypeCounters.Clear();
    }

    /// <summary>Does the table already hold tags for this instance?</summary>
    public static bool HasTagsFor(string instanceId, TagTable tags)
    {
        string prefix = instanceId + ".";
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(prefix)) return true;
        }
        return false;
    }

    /// <summary>
    /// Remove every tag this manager registered for an instance. Ids are always
    /// "&lt;instanceId&gt;.&lt;suffix&gt;", so the prefix identifies them without
    /// needing to know which suffixes the part type used.
    /// </summary>
    public static void UnregisterPartTags(string instanceId, TagTable tags)
    {
        string prefix = instanceId + ".";
        var doomed = new List<string>();
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(prefix)) doomed.Add(tag.Id);
        }
        foreach (var id in doomed) tags.Remove(id);
    }

    /// <summary>
    /// Is this a usable instance id? Ids become tag prefixes, so a dot would
    /// make <c>a.b</c> + <c>.rotate</c> read as a three-level name nothing
    /// matches, and whitespace makes a mapping file miserable to hand-edit.
    /// </summary>
    public static bool IsValidInstanceId(string id) =>
        id.Length > 0
        && id.IndexOf('.') < 0
        && id.IndexOf(' ') < 0
        && !id.StartsWith("_");

    /// <summary>
    /// Move every tag under <paramref name="oldId"/> to <paramref name="newId"/>,
    /// keeping each one's type, kind and current value.
    ///
    /// Renaming matters because the auto-generated ids
    /// (<c>pushermechanism_2</c>) are what a PLC program has to be written
    /// against, and they are both unreadable and unstable — delete a part and
    /// re-place it and the number moves, silently breaking a mapping file that
    /// still points at the old one.
    ///
    /// Returns false and changes nothing if the target id is already taken, so a
    /// rejected rename cannot half-apply and leave a part driven by a mixture of
    /// two prefixes.
    /// </summary>
    /// <summary>
    /// Keep a type's auto-numbering ahead of an id that was chosen rather than
    /// minted — one that arrived in a scene file, or one a user typed into the
    /// rename box.
    ///
    /// The rename case is the one that was missing (HP-15), and it is worth
    /// saying why the part *key* added for HP-37 does not cover it: that key is
    /// the undo history's idea of identity and is deliberately private to the
    /// editor. An instance id is something else entirely — a tag prefix, shared
    /// with every driver and every PLC program written against the scene. Two
    /// parts may not share one, and nothing about a unique command key makes
    /// that true.
    /// </summary>
    public static void NoteInstanceId(string partType, string instanceId)
    {
        if (!TypeCounters.ContainsKey(partType)) TypeCounters[partType] = 0;

        int underscore = instanceId.LastIndexOf('_');
        if (underscore >= 0 && int.TryParse(instanceId[(underscore + 1)..], out int used))
            TypeCounters[partType] = System.Math.Max(TypeCounters[partType], used);
    }

    public static bool RenameInstance(string oldId, string newId, TagTable tags)
    {
        if (oldId == newId) return true;
        if (!IsValidInstanceId(newId)) return false;

        string oldPrefix = oldId + ".";
        string newPrefix = newId + ".";

        var moving = new List<Tag>();
        foreach (var tag in tags)
        {
            if (tag.Id.StartsWith(oldPrefix)) moving.Add(tag);
        }

        // Check every destination before touching anything.
        foreach (var tag in moving)
        {
            if (tags.Contains(newPrefix + tag.Id[oldPrefix.Length..])) return false;
        }

        // A force is part of what a tag currently *is*, so it moves with the tag
        // (HP-17).
        //
        // Renaming removes and rebuilds each tag, and TagTable.Remove drops the
        // force along with it — correctly, because Remove exists for deleting a
        // part. Here it meant a rename quietly released every force on the part.
        // Renaming a motor you had forced off, while the PLC was commanding it
        // on, *started the motor*: the safest thing in the inspector turning
        // into the most dangerous, from an action that sounds like paperwork.
        //
        // Captured before anything is touched, because the loop below removes
        // the tag it is reading from.
        var pinned = new List<(string Suffix, object Value)>();
        foreach (var tag in moving)
        {
            if (tags.IsForced(tag.Id))
                pinned.Add((tag.Id[oldPrefix.Length..], tags.Visible(tag.Id)));
        }

        foreach (var tag in moving)
        {
            string suffix = tag.Id[oldPrefix.Length..];
            tags.Remove(tag.Id);
            tags.Add(new Tag(newPrefix + suffix, tag.Name, tag.Type, tag.Kind, tag.Value));
        }

        foreach (var (suffix, value) in pinned) tags.Force(newPrefix + suffix, value);

        return true;
    }

    /// <summary>
    /// Register the tags a part type exposes.
    /// </summary>
    /// <param name="preferredId">Id to reuse instead of minting a fresh one —
    /// how a loaded scene keeps the wiring it was saved with. Without it, every
    /// load renamed the parts and left the reloaded scene driven by nothing.</param>
    /// <returns>The instance id, and whether this call actually created the tags.
    /// It did not when they already existed, which means the part is a view of
    /// tags something else owns (the default scene's belt and sensors), and the
    /// caller must not delete them with the part. And the index the tag names
    /// were built with, so a later <see cref="RedeclarePartTags"/> builds the
    /// same ones.</returns>
    public static (string Id, bool Owns, int Index) RegisterPartTags(Node3D partNode, string partType,
                                                                     TagTable tags, string? preferredId = null)
    {
        if (!TypeCounters.ContainsKey(partType))
            TypeCounters[partType] = 0;

        string instanceId;
        if (preferredId is { Length: > 0 })
        {
            instanceId = preferredId;
            // Keep auto-numbering ahead of ids that arrived from a file, so a
            // part placed after a load cannot collide with one from it.
            NoteInstanceId(partType, preferredId);
        }
        else
        {
            // Step over anything already taken (HP-15).
            //
            // The counter alone is not enough and never was: it is advanced when
            // an id arrives from a file, and it was *not* advanced when a user
            // renamed a part. Rename conveyorbelt_1 to conveyorbelt_2, place a
            // new conveyor, and the counter still said 1 — so the new part minted
            // conveyorbelt_2, found tags already under that prefix, and adopted
            // them instead of creating its own. Two machines then answered one
            // PLC output, with nothing on screen to say which.
            //
            // Adoption is a real feature (the default scene's belt is a *view* of
            // tags SortingScene owns) but it is only ever right for an id the
            // caller asked for by name. An auto-minted id that collides is always
            // a bug, so this loop makes the collision impossible rather than
            // relying on every future caller to remember the counter.
            do
            {
                TypeCounters[partType]++;
                instanceId = $"{partType.ToLower()}_{TypeCounters[partType]}";
            }
            while (HasTagsFor(instanceId, tags));
        }

        // Tags already under this prefix belong to whoever created them first —
        // the simulation, for the default scene's belt and sensors. Adopt them
        // rather than re-adding (TagTable.Add throws on a duplicate id).
        if (HasTagsFor(instanceId, tags)) return (instanceId, false, TypeCounters[partType]);

        // The part declares its own I/O (HP-34). This used to be a
        // switch on the type name, three hundred lines long, in a file that
        // knew nothing about any of the machines it was registering tags for --
        // and the weighing conveyor's `.fault` is what that costs: registered
        // here, dispatched nowhere, because the two halves were edits to two
        // different files and one was missed.
        //
        // A part that is not an IPart registers nothing, which is right: it is
        // a decoration, like the chute, or a type this build does not have.
        if (partNode is Parts.IPart part)
            part.DeclareTags(new Parts.PartTagBuilder(tags, instanceId, TypeCounters[partType]));

        return (instanceId, true, TypeCounters[partType]);
    }

    /// <summary>
    /// Bring a placed part's tags back into line with what it declares
    /// <em>now</em>. Returns true if anything changed.
    ///
    /// A part's I/O can depend on its settings (IP-16: an analog input switched
    /// to raw counts turns from a <c>float</c> into an <c>int</c>, and at 4-20
    /// mA gains a wire-break contact), so "register once at placement" is not
    /// enough on its own. This is the second half: ask the part again and
    /// apply the difference.
    ///
    /// A tag survives only if its type, kind <em>and name</em> are unchanged.
    /// The name is the human statement of what the number means, units
    /// included, so a tag whose name changed is a different measurement even
    /// when its type did not -- a weighing conveyor's grams and its raw counts
    /// are both <c>int</c>, and a force of 2000 g carried across as 2000 counts
    /// would be a pin nobody set. Everything that survives keeps its value and
    /// its force; everything replaced starts at the value the part declared.
    /// </summary>
    public static bool RedeclarePartTags(Node3D partNode, string instanceId, int index, TagTable tags)
    {
        if (partNode is not Parts.IPart part) return false;

        var probe = new Parts.PartTagBuilder(null, instanceId, index);
        part.DeclareTags(probe);

        string prefix = instanceId + ".";
        var wanted = new Dictionary<string, Parts.PartTagDeclaration>();
        foreach (var declared in probe.Declarations) wanted[prefix + declared.Suffix] = declared;

        var doomed = new List<string>();
        foreach (var tag in tags)
        {
            if (!tag.Id.StartsWith(prefix, System.StringComparison.Ordinal)) continue;
            if (wanted.TryGetValue(tag.Id, out var declared)
                && declared.Type == tag.Type && declared.Kind == tag.Kind && declared.Name == tag.Name)
                continue;
            doomed.Add(tag.Id);
        }
        foreach (var id in doomed) tags.Remove(id);

        bool added = false;
        foreach (var declared in probe.Declarations)
        {
            string id = prefix + declared.Suffix;
            if (tags.Contains(id)) continue;
            tags.Add(new Tag(id, declared.Name, declared.Type, declared.Kind));
            if (declared.Initial is not null) tags.Set(id, declared.Initial);
            added = true;
        }

        return doomed.Count > 0 || added;
    }
}
