using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using FactoryForge.View;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Assert the filming flags do what recording depends on (tools/record_demo.py).
///
/// <code>godot --headless --path engine -- --self-test=film</code>
///
/// Headless, it proves the scheduler: <c>--at=T:ACTION</c> fires on the tick
/// that reads T on the *game* clock and not before, a force holds and a
/// release lets go, <c>operate</c> really moves a guard door through the same
/// call a click ends in (the guard's <c>closed</c> tag follows the door, it is
/// not forced), a watched tag's change is logged with its game time, and an
/// action on something that does not exist is counted and reported instead of
/// silently doing nothing.
///
/// With a display it also proves the two things only a display can show:
/// <c>--film</c> hides every panel that was visible, and <c>--camera=</c>
/// puts the orbit camera exactly where it says. Those are the parts that
/// looked right by inspection and were not asserted anywhere (gotcha 24), so
/// run it both ways (test_plan C and D).
/// </summary>
public partial class FilmSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private FilmDirector _director = null!;
    private readonly List<string> _panelsBefore = new();
    private bool _display;
    private bool _checked;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _Ready()
    {
        _display = DisplayServer.GetName() != "headless";
        Editor.LoadTemplate("res://templates/guarded_cell.json");

        // What is on screen before anything hides it.
        if (_display)
        {
            foreach (var name in new[] { "TagInspectorUI", "PartPaletteUI", "SceneToolbarUI" })
                if (GetParent().GetNodeOrNull<Control>(name) is { Visible: true }) _panelsBefore.Add(name);
        }

        _director = new FilmDirector { Name = "TestDirector", Tags = Tags, Editor = Editor, Film = true,
                                      UiRoot = GetParent() };
        Expect(_director.Schedule("bogus") is not null, "a spec with no colon is refused");
        Expect(_director.Schedule("1.0:frobnicate:x") is not null, "an unknown action is refused");
        Expect(_director.Schedule("x:force:a=1") is not null, "a non-numeric time is refused");
        Expect(_director.Schedule("1.0:force:tower.red") is not null, "force without =VALUE is refused");
        Expect(_director.SetCamera("1,2,3") is not null, "a camera without a target is refused");

        Expect(_director.Schedule("0.5:force:tower.red=true") is null, "force is accepted");
        Expect(_director.Schedule("1.0:release:tower.red") is null, "release is accepted");
        Expect(_director.Schedule("1.5:operate:guard_a") is null, "operate is accepted");
        Expect(_director.Schedule("2.0:force:no.such.tag=1") is null, "a well-formed action on a missing tag parses");
        Expect(_director.Schedule("2.2:operate:no_such_part") is null, "a well-formed action on a missing part parses");
        _director.Watch("tower.red,guard_a.closed");
        Expect(_director.SetCamera("30,-20,4:1,1,1") is null, "a camera pose is accepted");
        AddChild(_director);

        // --camera-to: checked on a director of its own, never added to the
        // tree, so the camera the display half asserts on is left alone.
        var mover = new FilmDirector();
        Expect(mover.SetCameraMove("0,-30,6:0,0,0@1-3") is not null, "a move with no --camera to start from is refused");
        mover.SetCamera("0,-20,4:0,0,0");
        Expect(mover.SetCameraMove("0,-30,6:0,0,0") is not null, "a move with no time window is refused");
        Expect(mover.SetCameraMove("0,-30,6:0,0,0@3-1") is not null, "a move that ends before it starts is refused");
        Expect(mover.SetCameraMove("40,-30,6:2,0,0@1-3") is null, "a camera move is accepted");
        if (mover.PoseAt(0.5) is { } before && mover.PoseAt(2.0) is { } mid && mover.PoseAt(9.0) is { } after)
        {
            Expect(before.Yaw == 0 && before.Distance == 4, $"before the move the camera is at the start pose, got yaw {before.Yaw}");
            Expect(Mathf.Abs(mid.Yaw - 20) < 0.01f && Mathf.Abs(mid.Distance - 5) < 0.01f
                   && Mathf.Abs(mid.Target.X - 1) < 0.01f, $"half way through, the camera is half way, got yaw {mid.Yaw}");
            Expect(after.Yaw == 40 && after.Pitch == -30 && after.Target.X == 2, $"after the move it stays at the end pose, got yaw {after.Yaw}");
        }
        else Expect(false, "PoseAt returned nothing with a pose set");
        mover.Free();
    }

    public override void _PhysicsProcess(double delta)
    {
        if (_checked || _director.GameTime < 3.0) return;
        _checked = true;
        try { Check(); }
        catch (System.Exception ex) { Expect(false, $"self-test threw: {ex.Message}"); }

        if (_failures.Count == 0)
        {
            GD.Print("self-test film: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test film: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    private void Check()
    {
        double tick = 1.0 / Engine.PhysicsTicksPerSecond;
        var fired = _director.FiredAt;
        Expect(fired.Count == 5, $"all five actions fired (got {fired.Count})");
        double[] wanted = { 0.5, 1.0, 1.5, 2.0, 2.2 };
        for (int i = 0; i < wanted.Length && i < fired.Count; i++)
        {
            Expect(fired[i] >= wanted[i] - 1e-6 && fired[i] <= wanted[i] + 2 * tick,
                   $"action {i} fired at game time {fired[i]:F3}, wanted {wanted[i]:F3}");
        }
        Expect(_director.Failures == 2, $"the two actions on nothing were counted as failures (got {_director.Failures})");

        // The changes the watch logged, with their game time.
        double? RedOn = null, RedOff = null, DoorOpen = null;
        bool doorStartedClosed = false;
        foreach (var (time, tag, value) in _director.Changes)
        {
            if (tag == "tower.red" && value == "true" && RedOn is null) RedOn = time;
            if (tag == "tower.red" && value == "false" && RedOn is not null && RedOff is null) RedOff = time;
            if (tag == "guard_a.closed" && value == "true" && time < 0.1) doorStartedClosed = true;
            if (tag == "guard_a.closed" && value == "false" && DoorOpen is null) DoorOpen = time;
        }
        Expect(RedOn is { } on && on >= 0.5 - 1e-6 && on <= 0.5 + 3 * tick,
               $"the force showed on the tag at 0.5 s (got {RedOn})");
        Expect(RedOff is { } off && off >= 1.0 - 1e-6 && off <= 1.0 + 3 * tick,
               $"the release let go at 1.0 s (got {RedOff})");
        Expect(doorStartedClosed, "the guard door started shut");
        // The door has to travel: the guard switch reads open only once it has
        // moved, which is the difference between operating the part and
        // forcing its tag.
        Expect(DoorOpen is { } open && open > 1.5 && open < 2.6,
               $"operate moved the door, and 'closed' followed it (got {DoorOpen})");
        Expect(!(bool)Tags.Visible("guard_a.closed"), "the door is still open at 3 s");
        Expect(!Tags.IsForced("guard_a.closed"), "operate did not force the tag");

        if (!_display) return;

        // What only a display can show.
        Expect(_panelsBefore.Count == 3, $"the panels were on screen before filming ({string.Join(",", _panelsBefore)})");
        foreach (var child in GetParent().GetChildren())
        {
            if (child is Control { Visible: true } c)
                Expect(false, $"--film left {c.Name} visible");
        }
        var cam = GetParent().GetNodeOrNull<OrbitCamera>("OrbitCamera");
        Expect(cam is not null, "there is an orbit camera to place");
        if (cam is null) return;
        Expect(cam.Target.DistanceTo(new Vector3(1, 1, 1)) < 0.01f, $"--camera target is {cam.Target}, wanted (1, 1, 1)");
        Expect(Mathf.Abs(cam.Distance - 4f) < 0.01f, $"--camera distance is {cam.Distance}, wanted 4");
        Expect(Mathf.Abs(Mathf.RadToDeg(cam.Yaw) - 30f) < 0.1f, $"--camera yaw is {Mathf.RadToDeg(cam.Yaw)}, wanted 30");
    }
}
