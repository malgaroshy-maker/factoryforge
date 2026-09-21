using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Assert that the Tag Inspector can force int and float tags, not just bits.
///
/// <code>godot --headless --path engine -- --self-test=force</code>
///
/// <see cref="TagTable.Force"/> has always taken any <c>object</c>, and parts
/// have always read through <c>TryGetVisible</c> -- the plumbing was never the
/// gap. <see cref="TagInspectorUI"/>'s Force button was: it only handled
/// <see cref="TagType.Bit"/>, so pressing Force on an int or float tag did
/// nothing, silently, and a scene like the tank template -- all float I/O --
/// could not be operated by hand at all (UX-35).
///
/// This drives the inspector's real controls -- the same LineEdit and Button
/// a click would use -- rather than calling private logic directly, so a
/// regression in the wiring fails here too, not just a regression in parsing.
/// </summary>
public partial class TagForceSelfTest : Node
{
    private readonly List<string> _failures = new();
    private TagTable _tags = null!;
    private int _step;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _Ready()
    {
        _tags = new TagTable();
        _tags.Add(new Tag("test.bit", "bit", TagType.Bit, TagKind.Output));
        _tags.Add(new Tag("test.count", "count", TagType.Int, TagKind.Output));
        _tags.Add(new Tag("test.level", "level", TagType.Float, TagKind.Output, 1.0));
        // A second machine, and an Input among the Outputs: grouping and the
        // kind filter are both claims about telling two things apart, and
        // neither can be checked against a list where everything is the same.
        _tags.Add(new Tag("other.detect", "detect", TagType.Bit, TagKind.Input));
        _tags.Add(new Tag("other.count", "count", TagType.Int, TagKind.Input));

        var inspector = new TagInspectorUI { Name = "TagInspectorUI" };
        AddChild(inspector);
        inspector.Setup(_tags);
    }

    public override void _Process(double delta)
    {
        _step++;
        // One frame for _Ready to build the row tree before probing it.
        if (_step < 2) return;

        CheckBit();
        CheckInt();
        CheckFloat();
        CheckInvalid();
        CheckGrouping();
        CheckSearch();
        CheckKindFilter();
        CheckForcedIsObvious();

        if (_failures.Count == 0)
        {
            GD.Print("self-test force: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test force: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private void CheckBit()
    {
        var (input, btn) = FindRow("test.bit");
        Expect(input is null, "test.bit: a bit tag gets no value field (one click is the only choice)");
        if (btn is null) { Expect(false, "test.bit: inspector built no row"); return; }

        bool before = (bool)_tags.Visible("test.bit");
        Click(btn);
        Expect(_tags.IsForced("test.bit"), "test.bit: Force did not force");
        Expect(!Equals(_tags.Visible("test.bit"), before), "test.bit: Force did not flip the value");

        Click(btn);
        Expect(!_tags.IsForced("test.bit"), "test.bit: UNFORCE did not clear");
    }

    private void CheckInt()
    {
        var (input, btn) = FindRow("test.count");
        if (input is null || btn is null) { Expect(false, "test.count: inspector built no row"); return; }

        input.Text = "42";
        Click(btn);
        Expect(_tags.IsForced("test.count"), "test.count: Force did not force");
        Expect(Equals(_tags.Visible("test.count"), 42), $"test.count: expected 42, got {_tags.Visible("test.count")}");

        Click(btn);
        Expect(!_tags.IsForced("test.count"), "test.count: UNFORCE did not clear");
    }

    private void CheckFloat()
    {
        var (input, btn) = FindRow("test.level");
        if (input is null || btn is null) { Expect(false, "test.level: inspector built no row"); return; }

        input.Text = "3.5";
        Click(btn);
        Expect(_tags.IsForced("test.level"), "test.level: Force did not force");
        double got = (double)_tags.Visible("test.level")!;
        Expect(System.Math.Abs(got - 3.5) < 1e-6, $"test.level: expected 3.5, got {got}");

        Click(btn);
        Expect(!_tags.IsForced("test.level"), "test.level: UNFORCE did not clear");
    }

    private void CheckInvalid()
    {
        var (input, btn) = FindRow("test.count");
        if (input is null || btn is null) { Expect(false, "test.count: inspector built no row"); return; }

        input.Text = "not a number";
        Click(btn);
        Expect(!_tags.IsForced("test.count"), "test.count: a bad value must not force anything (UX-36)");
    }

    private static void Click(Button btn) => btn.EmitSignal(BaseButton.SignalName.Pressed);

    // ---------------------------------------------------------------- TI-01..04
    //
    // Driven through the panel's own controls, found the way the force checks
    // above find a row -- by what is on screen -- rather than through
    // test-only accessors into its private state.

    private Button? HeaderFor(string prefix)
    {
        foreach (var b in FindDescendants<Button>(this))
        {
            if (b.TooltipText == $"Collapse or expand {prefix}") return b;
        }
        return null;
    }

    private Control? RowFor(string tagId)
    {
        foreach (var b in FindDescendants<Button>(this))
        {
            if (b.TooltipText.StartsWith(tagId + "\n")) return b.GetParent() as Control;
        }
        return null;
    }

    private Button? NameButtonFor(string tagId)
    {
        foreach (var b in FindDescendants<Button>(this))
        {
            if (b.TooltipText.StartsWith(tagId + "\n")) return b;
        }
        return null;
    }

    private LineEdit? SearchBox()
    {
        foreach (var le in FindDescendants<LineEdit>(this))
        {
            if (le.PlaceholderText == "Search tags…") return le;
        }
        return null;
    }

    private Button? ButtonWithTooltip(string tooltip)
    {
        foreach (var b in FindDescendants<Button>(this))
        {
            if (b.TooltipText == tooltip) return b;
        }
        return null;
    }

    /// <summary>Type into the search box the way a person does. Setting
    /// LineEdit.Text from code does not emit text_changed, so the signal is
    /// raised explicitly -- which still drives the panel's real handler rather
    /// than a copy of it.</summary>
    private void Type(LineEdit box, string text)
    {
        box.Text = text;
        box.EmitSignal(LineEdit.SignalName.TextChanged, text);
    }

    /// <summary>
    /// Can a person actually see this row?
    ///
    /// <c>Visible</c> alone cannot answer that and this test used to ask it
    /// anyway (HP-40). A row sits inside its group's body, and a row whose own
    /// flag is true inside a body whose flag is false is a row nobody can see —
    /// so every assertion here passed while the panel showed an empty list.
    /// That is precisely the bug HP-40 fixes, and this test walked straight
    /// past it for as long as both existed.
    ///
    /// <c>IsVisibleInTree</c> asks the question the user asks.
    /// </summary>
    private static bool OnScreen(Control? node) => node is not null && node.IsVisibleInTree();

    private void CheckGrouping()
    {
        Expect(HeaderFor("test") is not null, "tags are grouped under their part's name");
        Expect(HeaderFor("other") is not null, "and a second part gets its own group");

        var header = HeaderFor("test")!;
        var row = RowFor("test.bit");
        if (row?.GetParent() is not Control body) { Expect(false, "test.bit: no group body"); return; }

        Expect(OnScreen(body), "a group starts expanded");
        Expect(header.Text.StartsWith("\u25be"), "and its header says so");
        Click(header);
        Expect(!OnScreen(body), "clicking its header collapses it");
        Expect(header.Text.StartsWith("\u25b8"),
               $"and the header follows the body (reads '{header.Text}')");
        Click(header);
        Expect(OnScreen(body), "and clicking again opens it");
        Expect(header.Text.StartsWith("\u25be"), "with the header back to expanded");
    }

    private void CheckSearch()
    {
        var box = SearchBox();
        if (box is null) { Expect(false, "the panel has no search box"); return; }

        Type(box, "level");
        Expect(OnScreen(RowFor("test.level")), "searching keeps what matches");
        Expect(!OnScreen(RowFor("test.bit")), "and hides what does not");
        // A whole group with nothing matching takes its header with it, or the
        // results read as a list of empty machines.
        Expect(!OnScreen(HeaderFor("other")), "a group with no matches disappears entirely");

        Type(box, "other.");
        Expect(OnScreen(RowFor("other.detect")), "searching by part prefix finds its tags");
        Expect(!OnScreen(RowFor("test.level")), "and drops the other machine");

        Type(box, "");
        Expect(OnScreen(RowFor("test.bit")), "clearing the box brings everything back");
        Expect(OnScreen(HeaderFor("other")), "headers included");
        // The rows, not only the headers (HP-40). Filtering hides a group's
        // *body*, so a clear that reopened the header and not the body left a
        // "▾" over nothing -- and for the group whose tags never matched, that
        // was every row it had.
        Expect(OnScreen(RowFor("other.detect")),
               "and the rows inside those headers, not just the headers");

        // A group folded by hand is a different statement from a group emptied
        // by a filter, and a cleared filter must not undo it.
        var header = HeaderFor("test")!;
        Click(header);
        Expect(!OnScreen(RowFor("test.bit")), "a group collapsed by hand hides its rows");
        Type(box, "level");
        Expect(OnScreen(RowFor("test.level")),
               "a search opens a collapsed group, or it would hide its own matches");
        Type(box, "");
        Expect(!OnScreen(RowFor("test.bit")),
               "and clearing the search gives the group back to whoever collapsed it");
        Click(header);
        Expect(OnScreen(RowFor("test.bit")), "reopened by hand again");
    }

    private void CheckKindFilter()
    {
        var kind = ButtonWithTooltip("All tags / only what the PLC writes / only what it reads");
        if (kind is null) { Expect(false, "the panel has no kind filter"); return; }

        Click(kind);      // Outputs
        Expect(OnScreen(RowFor("test.bit")), "the Outputs filter keeps what the PLC writes");
        Expect(!OnScreen(RowFor("other.detect")), "and drops what it reads");

        Click(kind);      // Inputs
        Expect(OnScreen(RowFor("other.detect")), "the Inputs filter keeps what the PLC reads");
        Expect(!OnScreen(RowFor("test.bit")), "and drops what it writes");

        Click(kind);      // back to All
        Expect(OnScreen(RowFor("test.bit")) && OnScreen(RowFor("other.detect")),
               "and cycling once more shows both again");
    }

    /// <summary>
    /// The failure mode here is not being unable to find a forced tag. It is
    /// forgetting one exists -- a value that disagrees with the simulation on
    /// purpose, hours after you set it.
    /// </summary>
    private void CheckForcedIsObvious()
    {
        var (_, btn) = FindRow("test.bit");
        if (btn is null) { Expect(false, "test.bit: no row to force"); return; }

        var name = NameButtonFor("test.bit");
        Expect(name?.HasThemeColorOverride("font_color") == false,
               "an ordinary tag's name is not marked");

        Click(btn);
        Expect(_tags.IsForced("test.bit"), "the tag is forced");
        Expect(name?.HasThemeColorOverride("font_color") == true,
               "a forced tag's *name* is marked, not just its button");

        var release = ButtonWithTooltip("Hand every forced tag back to the simulation");
        Expect(release is not null, "the panel offers a way to release everything at once");
        Expect(release?.Visible == true, "shown only while something is forced");

        _tags.Force("test.count", 7);
        Click(release!);
        Expect(!_tags.IsForced("test.bit") && !_tags.IsForced("test.count"),
               "and releasing hands every one of them back");
        Expect(release?.Visible == false, "after which it goes away again");
        Expect(name?.HasThemeColorOverride("font_color") == false,
               "and the name stops being marked");
    }

    /// <summary>Find a tag's row the way a user's eye would -- by the tooltip
    /// the inspector puts on that row's name button -- rather than through a
    /// test-only accessor into the panel's private state.</summary>
    private (LineEdit? input, Button? force) FindRow(string tagId)
    {
        Button? nameBtn = null;
        foreach (var b in FindDescendants<Button>(this))
        {
            if (b.TooltipText.StartsWith(tagId + "\n"))
            {
                nameBtn = b;
                break;
            }
        }
        if (nameBtn?.GetParent() is not HBoxContainer row) return (null, null);

        LineEdit? input = null;
        Button? force = null;
        foreach (var child in row.GetChildren())
        {
            if (child is LineEdit le) input = le;
            if (child is Button b && b != nameBtn) force = b;
        }
        return (input, force);
    }

    private static IEnumerable<T> FindDescendants<T>(Node node) where T : Node
    {
        foreach (var child in node.GetChildren())
        {
            if (child is T match) yield return match;
            foreach (var d in FindDescendants<T>(child)) yield return d;
        }
    }
}
