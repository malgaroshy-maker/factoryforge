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
/// The answer used to be "exactly one", and this test asserted it. A Modbus
/// master polling every 50 ms then missed 38 of 50 clicks (IP-31,
/// tools/click_poll_repro.py), so the answer is now "one edge, held for at
/// least <see cref="MinVisibleHold"/>, with the same time open before the
/// next". The minimum is this test's own number, not the panel's setting read
/// back: a panel whose hold slid back to one tick must fail here rather than
/// agree with itself.
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
    private bool _done;
    private readonly List<string> _failures = new();

    /// <summary>IP-31: long enough for a 100 ms poll (OpenPLC's default) to see
    /// twice. The panel's default must be at least this.</summary>
    private const float MinVisibleHold = 0.2f;

    /// <summary>The press timeline, in physics steps. Built in _Ready from the
    /// panel's hold, so it stays right if the default changes.</summary>
    private int _hold;
    private int _minTicks;
    private int _single, _triple, _inside, _pair, _long, _end;
    private int _longHold;

    /// <summary>What <c>panel.start</c> read on each step from 2 to
    /// <see cref="_end"/>, as the dispatch left it.</summary>
    private readonly List<bool> _trace = new();

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
            _done = true;
            GetTree().Quit(1);
            return;
        }

        _hold = _panel.PressHoldTicks;
        _minTicks = Mathf.CeilToInt(MinVisibleHold * Engine.PhysicsTicksPerSecond - 1e-3f);
        int gap = 4 * _hold + 6;          // quiet time between scenarios
        _single = 1;
        _triple = _single + gap;
        _inside = _triple + gap;
        _pair = _inside + gap;
        _long = _pair + gap + 2 * _hold;
        _longHold = Mathf.CeilToInt(0.5f * Engine.PhysicsTicksPerSecond - 1e-3f);
        _end = _long + 2 * _longHold + 6;
    }

    /// <summary>The press train's settings, when this run was asked for one:
    /// <c>--press-train=N</c>, and optionally <c>--press-gap=MIN:MAX</c>
    /// seconds, <c>--press-second=S</c> (click again S seconds after each
    /// click) and <c>--press-seed=S</c>.</summary>
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
            else if (arg.StartsWith("--press-second="))
            {
                train.SecondAfter = arg.Substring("--press-second=".Length).ToFloat();
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
        if (_done) return;
        _step++;
        try
        {
            Step();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: a throw here would be logged, swallowed, and the run
            // would end on --duration looking like a pass.
            Expect(false, $"threw at step {_step}: {ex}");
            Report();
        }
    }

    private void Step()
    {
        if (_step >= 2 && _step <= _end) _trace.Add(Tag("start"));

        if (_step == _single)
        {
            foreach (string suffix in new[] { "start", "stop", "reset", "estop" })
                Expect(Tags.Contains($"{_id}.{suffix}"), $"tag {_id}.{suffix} exists");

            // Normally closed: a panel nobody has touched reports a healthy
            // circuit, not a struck mushroom.
            Expect(Tag("estop"), "estop reads true (healthy) at startup");
            Expect(!Tag("start"), "start is low before anything is pressed");

            Expect(_panel.PressHold >= MinVisibleHold - 1e-4f,
                   $"the default press hold is at least {MinVisibleHold} s (is {_panel.PressHold} s)");
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _single + 1)
        {
            Expect(_panel.IsHeld(PanelButton.Start), "the Start cap reports held while its tag is high");
        }
        else if (_step == _triple)
        {
            // Several clicks between two ticks are one press, not three --
            // otherwise a fast hand would queue phantom starts.
            _panel.Press(PanelButton.Start);
            _panel.Press(PanelButton.Start);
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _triple + 2)
        {
            Expect(!Tag("stop") && !Tag("reset"), "pressing start leaves the other buttons alone");
        }
        else if (_step == _inside)
        {
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _inside + _hold / 2)
        {
            // The button is already down: a click now is the same press. It
            // must neither make an edge of its own nor stretch the hold.
            Expect(Tag("start"), "start is still held halfway through the hold");
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _pair)
        {
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _pair + _hold + 2)
        {
            // Just after it opened: a real second press, made while the contact
            // is re-opening. It must be kept, and must wait for a full gap.
            Expect(!Tag("start"), "start has opened again once the hold ran out");
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _long)
        {
            // The hold is the panel's setting, not a constant.
            _panel.PressHold = 0.5f;
            _panel.Press(PanelButton.Start);
        }
        else if (_step == _end)
        {
            _panel.PressHold = ButtonPanel.DefaultPressHold;
            CheckTimeline();

            // Maintained: the mushroom stays struck with nobody holding it.
            _panel.Press(PanelButton.EmergencyStop);
        }
        else if (_step == _end + 1)
        {
            Expect(!Tag("estop"), "estop reads false while the mushroom is struck");
            Expect(_panel.EmergencyStopEngaged, "panel reports the mushroom engaged");
        }
        else if (_step == _end + 5)
        {
            Expect(!Tag("estop"), "estop stays false without being held (maintained, not momentary)");
            _panel.Press(PanelButton.EmergencyStop);   // twist to release
        }
        else if (_step == _end + 6)
        {
            Expect(Tag("estop"), "estop reads true again once released");
            CheckHitTest();
            CheckModeSwitching();
        }
        else if (_step == _end + 7)
        {
            Report();
        }
    }

    /// <summary>
    /// Read the whole recorded trace back as runs of high and low, and hold it
    /// to the rules in <see cref="ButtonPanel.StepPart"/>: one edge per press,
    /// each held for the hold and at least <see cref="MinVisibleHold"/>, each
    /// preceded by at least as long open.
    /// </summary>
    private void CheckTimeline()
    {
        // (first step, length) of every high run. Trace index i is step i + 2.
        var highs = new List<(int Start, int Length)>();
        for (int i = 0; i < _trace.Count; i++)
        {
            if (!_trace[i] || (i > 0 && _trace[i - 1])) continue;
            int length = 0;
            while (i + length < _trace.Count && _trace[i + length]) length++;
            highs.Add((i + 2, length));
        }

        GD.Print($"self-test buttons: hold {_hold} ticks at {Engine.PhysicsTicksPerSecond} Hz; " +
                 $"presses seen as (step, ticks): {string.Join(" ", highs)}");

        Expect(_hold >= _minTicks,
               $"a press holds for at least {MinVisibleHold} s = {_minTicks} ticks (the panel holds {_hold})");

        // single, triple, inside, pair x2, long = six edges.
        if (highs.Count != 6)
        {
            Expect(false, $"six presses' worth of edges on panel.start (saw {highs.Count})");
            return;
        }

        var (single, triple, inside, first, second, longPress) =
            (highs[0], highs[1], highs[2], highs[3], highs[4], highs[5]);

        Expect(single.Start == _single + 1, $"a click closes the contact on the next tick (step {single.Start})");
        Expect(single.Length == _hold && single.Length >= _minTicks,
               $"one click holds start high for the hold, {_hold} ticks (saw {single.Length})");
        Expect(triple.Length == _hold, $"three clicks in one tick are one press, one hold (saw {triple.Length} ticks)");
        Expect(inside.Start == _inside + 1 && inside.Length == _hold,
               $"a click while the button is down neither re-edges nor stretches it (saw {inside.Length} ticks)");

        Expect(first.Length == _hold && second.Length == _hold,
               $"two separate presses are two holds ({first.Length} and {second.Length} ticks)");
        int gap = second.Start - (first.Start + first.Length);
        Expect(gap == _hold,
               $"a click while the contact re-opens waits out a full open gap of {_hold} ticks, then closes (gap {gap})");

        Expect(longPress.Length == _longHold,
               $"a panel set to hold 0.5 s holds {_longHold} ticks (saw {longPress.Length})");
    }

    private void Report()
    {
        if (_done) return;
        _done = true;
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
