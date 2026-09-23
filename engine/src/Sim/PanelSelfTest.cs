using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the operator panel behaves the way a controller expects.
/// Run it with:
///
/// <code>godot --headless --path engine -- --self-test=buttons</code>
///
/// It exits non-zero on failure, so CI can gate on it.
///
/// This is an engine-side test rather than a sidecar one because the behaviour
/// it guards lives entirely in the engine: a mouse click arrives on the frame
/// clock, the tags are written on the physics clock, and the interesting
/// question — how many scans does a press stay high? — is invisible from the bus
/// unless you sample faster than the thing you are measuring.
///
/// It runs after <see cref="Editor.SceneEditor"/> in tree order, so each tick it
/// observes the tags exactly as the dispatch left them.
/// </summary>
public partial class PanelSelfTest : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    private ButtonPanel _panel = null!;
    private string _id = "panel";
    private int _step;
    private int _highTicks;
    private readonly List<string> _failures = new();

    public override void _Ready()
    {
        // --press-train=N turns this run into the click source for
        // tools/click_poll_repro.py instead: the same panel, pressed through
        // the same dispatch, but counted from the far side of a real driver.
        if (PressTrainArgs() is { } train)
        {
            train.Name = "PanelPressTrain";
            train.Tags = Tags;
            train.Editor = Editor;
            GetParent().CallDeferred(Node.MethodName.AddChild, train);
            SetPhysicsProcess(false);
            QueueFree();
            return;
        }

        // The panel the default scene builds, found the way anything else would
        // find it rather than through a back door the test alone can use.
        foreach (var child in GetParent().GetChildren())
        {
            if (child is ButtonPanel found) { _panel = found; break; }
        }

        if (_panel is null)
        {
            GD.PrintErr("self-test: no ButtonPanel in the default scene");
            GetTree().Quit(1);
        }
    }

    /// <summary>The press train's settings, when this run was asked for one:
    /// <c>--press-train=N</c>, and optionally <c>--press-gap=MIN:MAX</c>
    /// seconds and <c>--press-seed=S</c>.</summary>
    private static PanelPressTrain? PressTrainArgs()
    {
        PanelPressTrain? train = null;
        foreach (var arg in OS.GetCmdlineUserArgs())
        {
            if (arg.StartsWith("--press-train="))
                (train ??= new PanelPressTrain()).Presses = arg.Substring("--press-train=".Length).ToInt();
        }
        if (train is null) return null;

        foreach (var arg in OS.GetCmdlineUserArgs())
        {
            if (arg.StartsWith("--press-gap=")
                && arg.Substring("--press-gap=".Length).Split(':') is { Length: 2 } gap)
            {
                train.GapMin = gap[0].ToFloat();
                train.GapMax = gap[1].ToFloat();
            }
            else if (arg.StartsWith("--press-seed="))
            {
                train.Seed = (ulong)arg.Substring("--press-seed=".Length).ToInt();
            }
        }
        return train;
    }

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private bool Tag(string suffix) =>
        Tags.Contains($"{_id}.{suffix}") && (bool)Tags.Visible($"{_id}.{suffix}");

    public override void _PhysicsProcess(double delta)
    {
        _step++;

        switch (_step)
        {
            case 1:
                foreach (string suffix in new[] { "start", "stop", "reset", "estop" })
                    Expect(Tags.Contains($"{_id}.{suffix}"), $"tag {_id}.{suffix} exists");

                // Normally closed: a panel nobody has touched reports a healthy
                // circuit, not a struck mushroom.
                Expect(Tag("estop"), "estop reads true (healthy) at startup");
                Expect(!Tag("start"), "start is low before anything is pressed");

                _panel.Press(PanelButton.Start);
                _highTicks = 0;
                break;

            case >= 2 and <= 9:
                if (Tag("start")) _highTicks++;
                break;

            case 10:
                // The whole point: one press, one scan. A program polling this
                // tag sees an edge it can act on exactly once.
                Expect(_highTicks == 1, $"a press holds start high for exactly 1 tick (saw {_highTicks})");

                // Several clicks between two ticks are one press, not three —
                // otherwise a fast hand would queue phantom starts.
                _panel.Press(PanelButton.Start);
                _panel.Press(PanelButton.Start);
                _panel.Press(PanelButton.Start);
                _highTicks = 0;
                break;

            case >= 11 and <= 18:
                if (Tag("start")) _highTicks++;
                break;

            case 19:
                Expect(_highTicks == 1, $"three clicks in one tick still pulse once (saw {_highTicks})");
                Expect(!Tag("stop") && !Tag("reset"), "pressing start leaves the other buttons alone");

                // Maintained: the mushroom stays struck with nobody holding it.
                _panel.Press(PanelButton.EmergencyStop);
                break;

            case 20:
                Expect(!Tag("estop"), "estop reads false while the mushroom is struck");
                Expect(_panel.EmergencyStopEngaged, "panel reports the mushroom engaged");
                break;

            case 24:
                Expect(!Tag("estop"), "estop stays false without being held (maintained, not momentary)");
                _panel.Press(PanelButton.EmergencyStop);   // twist to release
                break;

            case 25:
                Expect(Tag("estop"), "estop reads true again once released");
                CheckHitTest();
                CheckModeSwitching();
                break;

            case 26:
                if (_failures.Count == 0)
                {
                    GD.Print("self-test buttons: PASS");
                    GetTree().Quit(0);
                }
                else
                {
                    GD.PrintErr($"self-test buttons: FAIL ({_failures.Count})");
                    GetTree().Quit(1);
                }
                break;
        }
    }

    /// <summary>
    /// Run mode has to actually switch editing off. Half a mode — one that
    /// changes what a click does but still lets a part be picked up from the
    /// palette — would leave a ghost conveyor following the cursor around a
    /// running line.
    /// </summary>
    private void CheckModeSwitching()
    {
        if (Editor is null)
        {
            _failures.Add("no editor to test modes against");
            return;
        }

        Editor.SetMode(EditorMode.Edit);
        Editor.SetPlacementPart("ConveyorBelt");
        Expect(Editor.HasPlacementPreview, "Edit mode starts a placement");

        Editor.SetMode(EditorMode.Run);
        Expect(Editor.Mode == EditorMode.Run, "the editor switches to Run");
        Expect(!Editor.HasPlacementPreview, "entering Run drops a placement left in progress");

        Editor.SetPlacementPart("ConveyorBelt");
        Expect(!Editor.HasPlacementPreview, "Run mode refuses to start a placement");

        Editor.SetMode(EditorMode.Edit);
        Expect(Editor.Mode == EditorMode.Edit, "the editor switches back to Edit");
    }

    /// <summary>
    /// Aim rays at the panel and check they hit the cap they are pointed at.
    /// Rays are built from the panel's own transform rather than from a camera,
    /// so the test needs no viewport.
    ///
    /// Run twice: once as the scene places the panel, and once with it turned to
    /// an angle that lines up with nothing. A panel can be rotated to any heading
    /// in the editor, and a hit test that quietly assumed world axes would pass
    /// the first pass and miss every button in the second.
    /// </summary>
    private void CheckHitTest()
    {
        CheckHitTestAtCurrentRotation("as placed");

        var was = _panel.Rotation;
        _panel.Rotation = new Vector3(0, 2.1f, 0);
        _panel.ForceUpdateTransform();
        CheckHitTestAtCurrentRotation("rotated");
        _panel.Rotation = was;
        _panel.ForceUpdateTransform();
    }

    private void CheckHitTestAtCurrentRotation(string when)
    {
        var basis = _panel.GlobalTransform.Basis;
        Vector3 outward = basis.Z.Normalized();

        PanelButton? Aim(Vector3 local)
        {
            Vector3 target = _panel.GlobalTransform * local;
            return _panel.HitTest(target + outward * 1.5f, -outward);
        }

        // Mirrors the layout ButtonPanel derives from PartLayout.PanelWidth/
        // PanelHeight (see FF-25) -- a future resize of that reference needs
        // the same numbers updated here, same as any other black-box "does a
        // ray at this literal point hit this button" check.
        Expect(Aim(new Vector3(-0.12f, 0.3132f, 0.06f)) == PanelButton.Start, $"ray at the start cap hits Start ({when})");
        Expect(Aim(new Vector3(0.0f, 0.3132f, 0.06f)) == PanelButton.Stop, $"ray at the stop cap hits Stop ({when})");
        Expect(Aim(new Vector3(0.12f, 0.3132f, 0.06f)) == PanelButton.Reset, $"ray at the reset cap hits Reset ({when})");
        Expect(Aim(new Vector3(0.0f, 0.1398f, 0.06f)) == PanelButton.EmergencyStop, $"ray at the mushroom hits E-Stop ({when})");

        // The parts of the station that are not buttons must stay dead, or the
        // panel becomes one big Start button.
        Expect(Aim(new Vector3(-0.06f, 0.3132f, 0.06f)) is null, $"the gap between two caps presses nothing ({when})");
        Expect(Aim(new Vector3(0.0f, 0.50f, 0.06f)) is null, $"the housing above the caps presses nothing ({when})");
        Expect(Aim(new Vector3(0.0f, -0.250f, 0.00f)) is null, $"the pedestal presses nothing ({when})");
    }
}
