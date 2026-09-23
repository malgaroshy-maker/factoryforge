using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// A train of Start clicks for <c>tools/click_poll_repro.py</c> to count from
/// the far side of a real driver (IP-31). Not a pass/fail test on its own: it
/// makes the clicks, counts the edges the engine raised, and leaves the
/// question that matters -- how many of them a polling controller saw -- to
/// the harness, which is the only thing that can see it.
///
/// <code>godot --headless --path engine -- --self-test=buttons --press-train=50 --bus-port=N</code>
///
/// Reached through <c>--self-test=buttons</c> (<see cref="PanelSelfTest"/>
/// hands over to it) rather than a mode of its own, so the panel's timing has
/// one entry point.
///
/// Each click goes through <see cref="SceneEditor.PressControlAtRay"/>, the
/// dispatch a real Run-mode click reaches after its screen position has been
/// projected, and so through <see cref="ButtonPanel.Operate"/>, the press
/// queue and <see cref="ButtonPanel.StepPart"/>. Only the projection is
/// skipped; <c>--self-test=click</c> covers that half. Clicks are made on the
/// frame clock, where real input arrives, at irregular intervals so the train
/// cannot phase-lock to a poller and produce a hit rate that is an artefact of
/// two clocks agreeing.
///
/// Nothing is pressed until a sidecar is connected and has had time to settle:
/// a click made before anyone was listening would be counted as missed and it
/// would not be the panel's fault.
/// </summary>
public partial class PanelPressTrain : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    /// <summary>Clicks to make.</summary>
    public int Presses { get; set; } = 50;

    /// <summary>Seconds between clicks, drawn uniformly from this range.</summary>
    public float GapMin { get; set; } = 0.55f;
    public float GapMax { get; set; } = 0.95f;

    /// <summary>Wall-clock seconds to wait after a client connects before the
    /// first click, and after the last click before reporting.</summary>
    public float Settle { get; set; } = 3.0f;

    public ulong Seed { get; set; } = 31;

    /// <summary>Seconds after each click to click again, or negative for single
    /// clicks. Shorter than the panel's hold, the second click lands on a
    /// button that is already down and must not make an edge of its own;
    /// longer, it must make exactly one more.</summary>
    public float SecondAfter { get; set; } = -1.0f;

    private ButtonPanel? _panel;
    private TagBusServer? _bus;
    private readonly RandomNumberGenerator _rng = new();

    private double _clientSince = -1;
    private double _nextPressAt = -1;
    private double _lastPressAt = -1;
    private int _made;
    private int _seconds;
    private double _secondAt = -1;

    private bool _prev;
    private int _edges;
    private int _highRun;
    private readonly List<int> _highRuns = new();

    private bool _done;
    private readonly double _startedAt = Time.GetTicksMsec() / 1000.0;

    private static double Now => Time.GetTicksMsec() / 1000.0;

    private const string StartTag = "panel.start";

    public override void _Ready()
    {
        _rng.Seed = Seed;
        foreach (var child in GetParent().GetChildren())
        {
            if (child is ButtonPanel panel && _panel is null) _panel = panel;
            if (child is TagBusServer bus) _bus = bus;
        }

        if (_panel is null || _bus is null || Editor is null)
        {
            Finish($"press-train: FAIL no panel, bus or editor in the scene", 1);
        }
    }

    /// <summary>The same ray <c>PanelSelfTest</c> aims at the Start cap: built
    /// from the panel's own transform, so it needs no camera.</summary>
    private (Vector3 From, Vector3 Dir) StartCapRay()
    {
        var xf = _panel!.GlobalTransform;
        Vector3 outward = xf.Basis.Z.Normalized();
        Vector3 target = xf * new Vector3(-0.12f, 0.3132f, 0.06f);
        return (target + outward * 1.5f, -outward);
    }

    public override void _Process(double delta)
    {
        if (_done) return;
        try
        {
            StepFrame();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: a throw here is logged and swallowed, and the run
            // would end on --duration looking like a success.
            Finish($"press-train: FAIL threw: {ex}", 1);
        }
    }

    private void StepFrame()
    {
        double now = Now;

        if (_clientSince < 0)
        {
            if (_bus!.HasClient)
            {
                _clientSince = now;
                _nextPressAt = now + Settle;
                Editor!.SetMode(EditorMode.Run);
                GD.Print("press-train: client connected; first click in " +
                         $"{Settle:0.0}s, {Presses} clicks at {GapMin:0.00}-{GapMax:0.00}s intervals");
            }
            else if (now - _startedAt > 60.0)
            {
                Finish("press-train: FAIL no client connected within 60s", 1);
            }
            return;
        }

        if (_secondAt >= 0 && now >= _secondAt)
        {
            Click();
            _seconds++;
            _secondAt = -1;
            return;
        }

        if (_made < Presses)
        {
            if (now < _nextPressAt) return;
            Click();
            _made++;
            _secondAt = SecondAfter >= 0 ? now + SecondAfter : -1;
            _nextPressAt = now + _rng.RandfRange(GapMin, GapMax);
            return;
        }

        if (_secondAt >= 0 || now - _lastPressAt < Settle) return;

        string runs = _highRuns.Count == 0
            ? "none"
            : $"{Min(_highRuns)}..{Max(_highRuns)}";
        // A second click inside the hold is the same press; one after it is
        // a press of its own.
        int expected = _made + (SecondAfter >= _panel!.PressHold ? _seconds : 0);
        bool ok = _edges == expected;
        Finish($"press-train: presses={_made + _seconds} expected={expected} edges={_edges} " +
               $"hold_s={_panel.PressHold:0.###} high_ticks={runs} " +
               $"physics_hz={Engine.PhysicsTicksPerSecond} " + (ok ? "OK" : "FAIL"),
               ok ? 0 : 1);
    }

    /// <summary>What the engine itself raised, sampled on every physics tick --
    /// the finest clock anything in the engine has. Runs after the editor in
    /// tree order, so it sees each tick's writes.</summary>
    public override void _PhysicsProcess(double delta)
    {
        if (_done || !Tags.Contains(StartTag)) return;

        bool now = Tags.Visible(StartTag) is true;
        if (now && !_prev) { _edges++; _highRun = 0; }
        if (now) _highRun++;
        if (!now && _prev) _highRuns.Add(_highRun);
        _prev = now;
    }

    private void Click()
    {
        var (from, dir) = StartCapRay();
        Editor!.PressControlAtRay(from, dir);
        _lastPressAt = Now;
    }

    private static int Min(List<int> xs) { int m = int.MaxValue; foreach (int x in xs) if (x < m) m = x; return m; }
    private static int Max(List<int> xs) { int m = int.MinValue; foreach (int x in xs) if (x > m) m = x; return m; }

    /// <summary>Gotcha 13: Quit takes effect at the end of the frame, so latch
    /// first.</summary>
    private void Finish(string line, int code)
    {
        if (_done) return;
        _done = true;
        if (code == 0) GD.Print(line); else GD.PrintErr(line);
        GetTree().Quit(code);
    }
}
