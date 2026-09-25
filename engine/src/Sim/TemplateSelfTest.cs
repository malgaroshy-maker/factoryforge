using System;
using System.Collections.Generic;
using System.Linq;
using FactoryForge.Editor;
using FactoryForge.Scenes;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Load every shipped template and check it is a working scene.
///
/// <code>godot --headless --path engine -- --self-test=templates</code>
///
/// Templates are the first thing a new user clicks, so a broken one is the
/// worst possible bug to ship: it is not a crash, it is a factory that quietly
/// has no conveyor in it. They are also plain JSON with hand-written positions,
/// which nothing else validates.
/// </summary>
public partial class TemplateSelfTest : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    /// <summary>The parts each template exists to demonstrate. Keyed by manifest
    /// id rather than duplicated here as a path list (UX-13) -- add a template to
    /// <c>engine/templates/manifest.json</c> and it is picked up below; this map
    /// only needs a new entry if it is worth asserting specific parts survived.</summary>
    private static readonly Dictionary<string, string[]> MustContain = new()
    {
        ["start-stop-station"] = new[] { "belt", "panel", "tower", "emitter", "counter" },
        ["tank-level-control"] = new[] { "tank", "level_readout", "panel" },
        ["light-curtain-sorting"] = new[] { "belt", "height_gauge", "diverter", "chute", "tall_count" },
        ["roller-line-weighing"] = new[] { "infeed", "scale", "metal_check", "weight_readout" },
        ["pick-and-place-cell"] = new[] { "infeed", "scanner", "pickstation", "gantry", "outfeed" },
        ["heat-treat-station"] = new[] { "oven", "temp_gauge", "panel", "alarm" },
    };

    /// <summary>Every template with a real file -- the built-in scene (empty
    /// path) is covered by the default scene, not this test.</summary>
    private static readonly TemplateEntry[] Expected =
        TemplateManifest.Load().Where(t => t.Path.Length > 0).ToArray();

    private readonly List<string> _failures = new();
    private int _step;
    private int _index;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _PhysicsProcess(double delta)
    {
        if (_done) return;
        _step++;

        // Two ticks per template: one to load, one to inspect. Parts build their
        // geometry in _Ready, which does not run until they have been in the
        // tree for a frame.
        if (_step < 3) return;
        int phase = (_step - 3) % 2;
        int index = (_step - 3) / 2;

        if (index >= Expected.Length)
        {
            _done = true;
            Report();
            return;
        }

        if (phase == 0)
        {
            _index = index;
            try
            {
                Editor!.LoadTemplate(Expected[index].Path);
            }
            catch (System.Exception ex)
            {
                Expect(false, $"{Expected[index].Path} threw: {ex.Message}");
            }
            return;
        }

        var entry = Expected[_index];
        CheckLoaded(entry.Path, MustContain.TryGetValue(entry.Id, out var m) ? m : Array.Empty<string>());
    }

    private void CheckLoaded(string path, string[] mustContain)
    {
        string name = path.GetFile();
        var ids = Editor!.PlacedPartIds();

        Expect(ids.Count > 0, $"{name} loaded at least one part");

        foreach (string id in mustContain)
        {
            Expect(ids.Contains(id), $"{name} contains '{id}'");
        }

        // Every part must have registered its I/O, or the template looks right
        // and exposes nothing for a PLC to talk to. A part that declares no tags
        // by design (the chute, the mezzanine deck) is asked, not guessed from
        // its id -- the id is the scene author's to choose.
        foreach (string id in ids)
        {
            if (Editor.NodeFor(id) is Parts.IPart part)
            {
                var declared = new Parts.PartTagBuilder(null, id, 0);
                part.DeclareTags(declared);
                if (declared.Suffixes.Count == 0) continue;
            }
            Expect(PartTagManager.HasTagsFor(id, Tags), $"{name}: '{id}' registered tags");
        }

        // The sorting demo's tags are declared by the engine at startup, not by
        // any part. A template that inherits them shows a conveyor and two box
        // counters it does not have.
        foreach (string leftover in SortingTags.All)
        {
            if (ids.Contains(leftover.Split('.')[0])) continue;
            Expect(!Tags.Contains(leftover),
                   $"{name}: leftover demo tag '{leftover}' is still declared");
        }

        Expect(Editor.SceneName != "sorting-by-height",
               $"{name} reports its own scene name (got '{Editor.SceneName}')");

        CheckBrief(name);
    }

    /// <summary>
    /// The scene and the lesson attached to it have to describe the same
    /// machine (BR-04).
    ///
    /// A brief is prose, so nothing about it can be checked automatically —
    /// except the one part that is not prose. `uses` names the tags a program
    /// is supposed to drive and read, and a brief naming a tag the scene does
    /// not have is a broken lesson: somebody follows it, cannot find the tag,
    /// and concludes the app is wrong rather than the text.
    ///
    /// That is also exactly how this goes stale. Nobody edits a template
    /// *intending* to invalidate its brief; they rename a part, or drop one,
    /// and the prose keeps saying what it always said. So the check runs
    /// against the scene the editor has actually just loaded, not against a
    /// second list of what it ought to contain.
    /// </summary>
    private void CheckBrief(string name)
    {
        TemplateBrief? brief = TemplateManifest.BriefForScene(Editor!.SceneName);
        if (brief is null)
        {
            Expect(false, $"{name}: no brief in the manifest — a shipped scene with no lesson "
                          + "attached to it is one nobody can tell what to do with");
            return;
        }

        Expect(brief.Task.Length > 0, $"{name}: the brief says what to build");
        Expect(brief.Uses.Length > 0, $"{name}: the brief names the tags to use");
        Expect(brief.Done.Length > 0, $"{name}: the brief says how you know it works");

        foreach (string raw in brief.Uses.Split('·'))
        {
            string tagId = raw.Trim();
            if (tagId.Length == 0) continue;
            Expect(Tags.Contains(tagId),
                   $"{name}: the brief names '{tagId}', which this scene does not have");
        }
    }

    private void Report()
    {
        if (_failures.Count == 0)
        {
            GD.Print("self-test templates: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test templates: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }
}
