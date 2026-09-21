using System.Collections.Generic;
using FactoryForge.Editor;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Fail when the editor learns a part type's name again (HP-36).
///
/// <code>godot --headless --path engine -- --self-test=partcontract</code>
///
/// HP-34 moved every part's tags, settings, tick, inspector rows and click
/// behaviour onto the part. Nothing stops them coming back one convenient
/// special case at a time, which is how they arrived the first time — nobody
/// decided that the weighing conveyor's fault contact should do nothing; a tag
/// was registered in one file and never dispatched in another, and five
/// releases went by.
///
/// So this scans the editor's own source for any concrete part type name and
/// fails on every occurrence, with the file and line. It is a text scan on
/// purpose. The three shapes this has to catch do not look alike —
///
/// <code>
///     case "ConveyorBelt":                          // a switch arm
///     if (node is ConveyorBelt belt) { ... }        // a type test
///     ["ConveyorBelt"] = new[] { "rotate" },        // a metadata table
/// </code>
///
/// — and a check that only banned switches would have left
/// <c>TagSuffixesByType</c> and <c>WholeBodyOperableTag</c> standing, which is
/// exactly where the knowledge would have pooled next. A text scan catches all
/// three, plus the fourth shape nobody lists: a comment naming a part, which
/// goes stale silently and then misleads the next person.
///
/// <b>Two files are exempt, and only two.</b>
///
/// <list type="bullet">
/// <item><c>PartCatalog.cs</c> — the registry. "One new file plus one catalog
/// entry" is HP-34's whole promise, and the entry has to name the type it
/// describes and call its constructor.</item>
/// <item><c>SceneEditor.DefaultScene.cs</c> — a scene. Naming the parts it
/// places is what a scene <em>is</em>, the same way
/// <c>engine/templates/*.json</c> and <c>Scenes/SortingScene.cs</c> do. It was
/// split out of <c>SceneEditor.cs</c> so that this exemption is one visible
/// file rather than an exception buried in four thousand lines that also did
/// the dispatching.</item>
/// </list>
///
/// Self-tests under <c>src/Sim/</c> are not scanned and should not be: a test
/// for the encoder has to name the encoder. The editor is the place per-part
/// knowledge pools, because it is the place that has to work for every part.
///
/// Reads <c>res://src/Editor/</c>, which exists in a checkout and not in an
/// exported build — .cs files are not packed. Against an export it reports
/// SKIPPED rather than passing, because a check that cannot see its failure
/// mode is not coverage (gotcha 24), and a vacuous pass is worse than no test.
/// </summary>
public partial class PartContractSelfTest : Node
{
    private const string EditorDir = "res://src/Editor";

    /// <summary>The registry, and the one scene that lives beside it. See the
    /// class comment for why each is allowed to name a part.</summary>
    private static readonly HashSet<string> Exempt = new()
    {
        "PartCatalog.cs",
        "SceneEditor.DefaultScene.cs",
    };

    private readonly List<string> _failures = new();

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _Ready()
    {
        var files = ListEditorSources();
        if (files.Count == 0)
        {
            // Not a failure and not a pass. An exported build has no .cs files
            // to read, and saying PASS here would be the most confident lie in
            // the suite.
            GD.Print($"self-test partcontract: SKIPPED (no sources under {EditorDir}; "
                     + "run this against a checkout, not an exported build)");
            GetTree().Quit(0);
            return;
        }

        var banned = PartCatalog.AllTypes();
        GD.Print($"  scanning {files.Count} file(s) under {EditorDir} for {banned.Length} part type name(s)");
        GD.Print($"  exempt: {string.Join(", ", Exempt)}");

        int scanned = 0;
        foreach (string file in files)
        {
            if (Exempt.Contains(file)) continue;
            scanned++;
            ScanFile(file, banned);
        }

        // The scan has to have work to do. A wrong directory, a renamed folder
        // or an exemption list that grew to cover everything would all produce
        // "no occurrences found" and look like success -- the shape of failure
        // gotcha 16 is about.
        Expect(scanned >= 5,
               $"the scan actually looked at the editor ({scanned} file(s) after exemptions)");
        Expect(banned.Length >= 20,
               $"the catalog gave the scan something to look for ({banned.Length} type(s))");

        if (_failures.Count == 0)
        {
            GD.Print($"self-test partcontract: PASS ({scanned} file(s) clean)");
            GetTree().Quit(0);
            return;
        }
        GD.PrintErr($"self-test partcontract: FAIL ({_failures.Count})");
        GetTree().Quit(1);
    }

    private void ScanFile(string file, string[] banned)
    {
        using var handle = FileAccess.Open($"{EditorDir}/{file}", FileAccess.ModeFlags.Read);
        if (handle is null) return;

        string text = handle.GetAsText();
        string[] lines = text.Split('\n');

        for (int i = 0; i < lines.Length; i++)
        {
            foreach (string type in banned)
            {
                if (!ContainsWord(lines[i], type)) continue;
                Expect(false,
                       $"{file}:{i + 1} names the part type '{type}'. Per-part knowledge belongs "
                       + $"on the part (engine/src/Parts/), not in the editor -- see HP-34. "
                       + $"Line: {lines[i].Trim()}");
            }
        }
    }

    /// <summary>Whole-word only. Without the boundary check, "Emitter" would
    /// match <c>emitterNode</c> and "Chute" would match <c>ChuteLane</c>, and a
    /// test that cries wolf on identifiers that merely contain a part's name is
    /// one somebody switches off.</summary>
    private static bool ContainsWord(string line, string word)
    {
        int from = 0;
        while (true)
        {
            int at = line.IndexOf(word, from, System.StringComparison.Ordinal);
            if (at < 0) return false;

            bool leftClear = at == 0 || !IsWordChar(line[at - 1]);
            int after = at + word.Length;
            bool rightClear = after >= line.Length || !IsWordChar(line[after]);
            if (leftClear && rightClear) return true;

            from = at + 1;
        }
    }

    private static bool IsWordChar(char c) => char.IsLetterOrDigit(c) || c == '_';

    private static List<string> ListEditorSources()
    {
        var files = new List<string>();
        foreach (string name in DirAccess.GetFilesAt(EditorDir))
        {
            if (name.EndsWith(".cs", System.StringComparison.Ordinal)) files.Add(name);
        }
        return files;
    }
}
